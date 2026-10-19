"""Report tests. Scenarios (six fiscal years 2019-2024, each 10-K carrying the prior
year's comparative so like-for-like growth works):

STRONG  revenue +20%/yr, net margin 20%, operating margin 25%, OCF = 1.2x net income,
        capex 5% of revenue, liabilities 0.5x equity.
        -> growth, profitability, financial health all Strong (every signal 1 point,
           except margin trend, flat at 0 change = 0.5 -> profitability 3.5/4 = 88).
WEAK    revenue -2% to -4%/yr, net income sliding to losses, liabilities 4x equity.
        -> growth, profitability, financial health all Weak (score 0).
        Net income growth is unscored (prior-year base negative); valuation has only
        one scored signal (negative FCF yield) so it is Unavailable.
Score arithmetic: points [1, .5, 0, 1] -> mean .625 -> 63 -> Moderate.
"""
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from meridian.analysis.report import (
    Signal,
    rating_for,
    report_from_facts,
    score_signals,
    valuation_signals,
)
from meridian.data.edgar import Company, EdgarError, TickerNotFound
from meridian.data.prices import NoDataError, ProviderError
from meridian.main import app, get_edgar_client, get_price_service

AS_OF = date(2026, 10, 19)


def dur(vals, fy0=2019):
    rows = []
    for t, v in enumerate(vals):
        y = fy0 + t
        meta = {"filed": f"{y + 1}-02-15", "form": "10-K", "accn": f"a{y}"}
        rows.append({"start": f"{y}-01-01", "end": f"{y}-12-31", "val": v, **meta})
        if t:
            rows.append({"start": f"{y - 1}-01-01", "end": f"{y - 1}-12-31",
                         "val": vals[t - 1], **meta})
    return rows


def inst(vals, fy0=2019):
    rows = []
    for t, v in enumerate(vals):
        y = fy0 + t
        meta = {"filed": f"{y + 1}-02-15", "form": "10-K", "accn": f"a{y}"}
        rows.append({"end": f"{y}-12-31", "val": v, **meta})
        if t:
            rows.append({"end": f"{y - 1}-12-31", "val": vals[t - 1], **meta})
    return rows


def company(revenue, ni, op=None, ocf=None, capex=None, equity=None,
            liabilities=None, cover=1000, diluted=None, fy0=2019):
    gaap = {}
    for tag, vals, fn in (
        ("Revenues", revenue, dur), ("NetIncomeLoss", ni, dur),
        ("OperatingIncomeLoss", op, dur),
        ("NetCashProvidedByUsedInOperatingActivities", ocf, dur),
        ("PaymentsToAcquirePropertyPlantAndEquipment", capex, dur),
        ("StockholdersEquity", equity, inst), ("Liabilities", liabilities, inst),
    ):
        if vals is not None:
            gaap[tag] = {"units": {"USD": fn(vals, fy0)}}
    if diluted is not None:
        gaap["WeightedAverageNumberOfDilutedSharesOutstanding"] = {
            "units": {"shares": dur(diluted, fy0)}}
    facts = {"entityName": "Acme Corp", "facts": {"us-gaap": gaap}}
    if cover is not None:
        facts["facts"]["dei"] = {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            {"end": f"{fy0 + t + 1}-02-01", "val": cover, "filed": f"{fy0 + t + 1}-02-15",
             "form": "10-K", "accn": f"a{fy0 + t}"} for t in range(len(revenue))]}}}
    return facts


def strong(**kw):
    rev = [100, 120, 144, 173, 207, 249]
    ni = [20, 24, 28.8, 34.6, 41.4, 49.8]
    args = dict(
        revenue=rev, ni=ni, op=[r * 0.25 for r in rev], ocf=[n * 1.2 for n in ni],
        capex=[r * 0.05 for r in rev], equity=[100, 120, 145, 175, 210, 250],
        liabilities=[50, 60, 72.5, 87.5, 105, 125],
    )
    args.update(kw)
    return company(**args)


def weak():
    return company(
        revenue=[100, 98, 95, 93, 90, 86], ni=[5, 3, -2, 1, -4, -6],
        op=[8, 6, 2, 3, -1, -3], ocf=[6, 4, 1, 2, -1, -3], capex=[5] * 6,
        equity=[50, 48, 40, 38, 30, 20], liabilities=[200, 192, 160, 152, 120, 80],
    )


def prices(close=10.0, end="2026-10-16"):
    idx = pd.bdate_range("2019-06-03", end, name="date")
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close,
                         "adj_close": close, "volume": 1.0, "split": 0.0}, index=idx)


def run(facts, px=None, sic=7372, **kw):
    return report_from_facts(
        facts, prices() if px is None else px, ticker="ACME", cik=1, sic=sic,
        sic_description="x", as_of=AS_OF, **kw,
    )


def sig(report, cat, sid):
    return next(s for s in report["categories"][cat]["signals"] if s["id"] == sid)


# -- scoring arithmetic --------------------------------------------------------


def s(points):
    return Signal("x", "x", 0.0, points, "", "")


def test_score_arithmetic():
    assert score_signals([s(1), s(0.5), s(0), s(1)]) == 63
    assert score_signals([s(1), s(1), s(0)]) == 67
    assert score_signals([s(0.5)] * 3) == 50
    assert score_signals([s(1), s(None), s(None)]) is None  # one scored signal
    assert score_signals([]) is None


def test_rating_cutoffs_and_labels():
    assert rating_for("growth", 67) == "Strong"
    assert rating_for("growth", 66) == "Moderate"
    assert rating_for("growth", 34) == "Moderate"
    assert rating_for("growth", 33) == "Weak"
    assert rating_for("valuation", 90) == "Inexpensive"
    assert rating_for("valuation", 10) == "Elevated"
    assert rating_for("valuation", None) == "Unavailable"


# -- scenarios -----------------------------------------------------------------


def test_strong_company():
    r = run(strong())
    cats = r["categories"]
    assert [cats[k]["rating"] for k in ("growth", "profitability", "financial_health")] == ["Strong"] * 3
    assert cats["growth"]["score"] == 100
    assert cats["financial_health"]["score"] == 100
    assert cats["profitability"]["score"] == 88  # 3.5 / 4
    assert sig(r, "growth", "revenue_growth")["value"] == pytest.approx(249 / 207 - 1)
    assert sig(r, "growth", "growth_consistency")["finding"] == "Revenue grew in 5 of the last 5 years."
    assert sig(r, "profitability", "margin_trend")["points"] == 0.5
    assert sig(r, "financial_health", "leverage")["value"] == pytest.approx(0.5)
    assert sig(r, "financial_health", "cash_conversion")["value"] == pytest.approx(1.2)


def test_weak_company():
    r = run(weak())
    cats = r["categories"]
    for k in ("growth", "profitability", "financial_health"):
        assert cats[k]["rating"] == "Weak" and cats[k]["score"] == 0, k
    ng = sig(r, "growth", "net_income_growth")
    assert ng["points"] is None and "not meaningful" in ng["finding"]
    assert sig(r, "financial_health", "leverage")["value"] == pytest.approx(4.0)
    assert sig(r, "financial_health", "earnings_stability")["finding"] == \
        "Net income was positive in 3 of the last 6 years."
    # P/E not meaningful (losses) and one scored signal -> not rated
    assert sig(r, "valuation", "pe")["points"] is None
    assert r["categories"]["valuation"]["rating"] == "Unavailable"
    assert not any(x["id"] == "cash_conversion" for x in cats["financial_health"]["signals"])


def test_financial_company_skips_cash_flow_signals():
    r = run(strong(), sic=6021)
    assert r["is_financial"] is True
    ids = [x["id"] for x in r["categories"]["financial_health"]["signals"]]
    assert "leverage" not in ids and "fcf_margin" not in ids and "financial_company" in ids
    assert r["categories"]["financial_health"]["rating"] == "Unavailable"
    assert not any(x["id"] == "fcf_yield" for x in r["categories"]["valuation"]["signals"])
    assert any("Financial company" in n["text"] for n in r["data_quality"])


def test_summary_text():
    r = run(strong())
    assert "Acme Corp (ACME)" in r["summary"]
    assert "growth strong" in r["summary"]
    # growth and financial health tie at 100, so no "strongest"; profitability (88) is
    # the unique lowest
    assert "Strongest" not in r["summary"]
    assert "Weakest: profitability." in r["summary"]
    assert "not investment advice" in r["summary"]
    assert "not investment advice" in r["method"]["disclaimer"]


# -- valuation signals -----------------------------------------------------------


def pts(*pes):
    return [SimpleNamespace(pe=p) for p in pes]


def test_valuation_inexpensive():
    sigs = valuation_signals({"basis": "ttm", "pe": 12.0, "fcf_yield": 0.06}, pts(20, 20, 20, 20), False)
    assert [x.points for x in sigs] == [1.0, 1.0, 1.0]
    assert score_signals(sigs) == 100
    assert [x.value for x in sigs][2] == pytest.approx(0.6)


def test_valuation_elevated():
    sigs = valuation_signals({"basis": "ttm", "pe": 40.0, "fcf_yield": 0.01}, pts(30, 30, 30, 30), False)
    assert [x.points for x in sigs] == [0.0, 0.0, 0.0]
    assert rating_for("valuation", score_signals(sigs)) == "Elevated"


def test_valuation_moderate_and_history_needs_three_points():
    sigs = valuation_signals({"basis": "annual", "pe": 20.0, "fcf_yield": 0.03}, pts(20, 20, 20), False)
    assert [x.points for x in sigs] == [0.5, 0.5, 0.5]
    assert score_signals(sigs) == 50
    short = valuation_signals({"basis": "annual", "pe": 20.0, "fcf_yield": 0.03}, pts(20, 20), False)
    assert [x.id for x in short] == ["pe", "fcf_yield"]


def test_valuation_without_current_or_earnings():
    assert valuation_signals(None, pts(10, 10, 10), False) == []
    sigs = valuation_signals({"basis": "ttm", "pe": None, "fcf_yield": 0.06}, [], False)
    assert sigs[0].points is None and "not meaningful" in sigs[0].finding


# -- data quality ----------------------------------------------------------------


def dq(report, needle):
    return [n for n in report["data_quality"] if needle in n["text"]]


def test_limited_history_warning():
    r = run(company(revenue=[100, 110, 121], ni=[10, 11, 12.1]))
    (n,) = dq(r, "Limited history")
    assert n["level"] == "warn"


def test_stale_annual_valuation_warning():
    (n,) = dq(run(strong()), "days old")
    assert n["level"] == "warn" and "654 days" in n["text"]  # 2024-12-31 -> 2026-10-16


def test_price_error_degrades_valuation_only():
    r = run(strong(), px=pd.DataFrame(), price_error="boom")
    assert r["categories"]["valuation"]["rating"] == "Unavailable"
    assert r["categories"]["growth"]["rating"] == "Strong"
    (n,) = dq(r, "Price data unavailable (boom)")
    assert n["level"] == "warn"


def test_old_price_warning():
    r = run(strong(), px=prices(end="2026-09-01"))
    (n,) = dq(r, "Latest price is")
    assert "48 days old" in n["text"] and n["level"] == "warn"


def test_missing_metric_note_and_skipped_signal():
    r = run(strong(capex=None))
    (n,) = dq(r, "Not available in this company's SEC filings")
    assert "capital expenditures" in n["text"]
    assert not any(x["id"] == "fcf_margin" for x in r["categories"]["financial_health"]["signals"])


def test_restatement_note():
    facts = strong()
    facts["facts"]["us-gaap"]["Revenues"]["units"]["USD"].append(
        {"start": "2019-01-01", "end": "2019-12-31", "val": 130, "filed": "2026-01-01",
         "form": "10-K", "accn": "late"})
    (n,) = dq(run(facts), "restated")
    assert n["text"].startswith("1 revenue")


def test_diluted_share_fallback_warning():
    facts = strong(cover=None, diluted=[1000] * 6)
    r = run(facts)
    assert dq(r, "weighted average")
    assert r["key_figures"]["market_cap"] == pytest.approx(10_000)


def test_sector_unknown_warning():
    (n,) = dq(run(strong(), sic=None), "Sector unknown")
    assert n["level"] == "warn"


def test_warnings_listed_before_info():
    levels = [n["level"] for n in run(strong())["data_quality"]]
    assert levels == sorted(levels, key=lambda lv: 0 if lv == "warn" else 1)


# -- endpoint --------------------------------------------------------------------


class StubEdgar:
    def __init__(self, facts, sic_ok=True):
        self.facts, self.sic_ok = facts, sic_ok

    def lookup_company(self, t):
        if t == "NOPE":
            raise TickerNotFound("nope")
        return Company(cik=1, ticker=t, title="Acme")

    def get_company_facts(self, c):
        return self.facts

    def get_sic(self, c):
        if not self.sic_ok:
            raise EdgarError("down")
        return 7372, "Software"


class StubPrices:
    def __init__(self, error=None):
        self.error = error

    def get_history(self, ticker, start=None, end=None):
        if self.error:
            raise self.error
        return prices()


@pytest.fixture
def use():
    def _use(edgar=None, px=None):
        app.dependency_overrides[get_edgar_client] = lambda: edgar or StubEdgar(strong())
        app.dependency_overrides[get_price_service] = lambda: px or StubPrices()
        return TestClient(app)

    yield _use
    app.dependency_overrides.clear()


def test_report_endpoint(use):
    body = use().get("/report/acme").json()
    assert body["ticker"] == "ACME" and body["is_financial"] is False
    assert set(body["categories"]) == {"growth", "profitability", "financial_health", "valuation"}
    assert body["categories"]["growth"]["rating"] == "Strong"
    assert body["key_figures"]["close"] == 10.0
    assert body["method"]["disclaimer"]


@pytest.mark.parametrize("error", [ProviderError("boom"), NoDataError("none")])
def test_report_endpoint_survives_price_failure(use, error):
    r = use(px=StubPrices(error)).get("/report/acme")
    assert r.status_code == 200
    body = r.json()
    assert body["categories"]["valuation"]["rating"] == "Unavailable"
    assert any("Price data unavailable" in n["text"] for n in body["data_quality"])


def test_report_endpoint_survives_sic_failure(use):
    r = use(edgar=StubEdgar(strong(), sic_ok=False)).get("/report/acme")
    assert r.status_code == 200
    assert any("Sector unknown" in n["text"] for n in r.json()["data_quality"])


def test_report_endpoint_errors(use):
    client = use()
    assert client.get("/report/NOPE").status_code == 404
    assert client.get("/report/bad$ticker").status_code == 422
    assert client.get("/report/acme?years=0").status_code == 422


def test_summary_calls_no_strongest_or_weakest_on_all_ties():
    r = run(weak())  # growth, profitability, financial health all score 0
    assert "Strongest" not in r["summary"] and "Weakest" not in r["summary"]
    assert "growth weak, profitability weak, financial health weak" in r["summary"]
    assert "valuation not rated (insufficient data)" in r["summary"]
