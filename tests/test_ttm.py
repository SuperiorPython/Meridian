"""TTM tests. Hand-computed scenario (BASE):

  Fiscal year ended 2026-01-25 (10-K filed 2026-02-25).
  Q1 10-Q (ended 2026-04-26) and Q2 10-Q (ended 2026-07-26, filed 2026-08-27).
  TTM = FY + current YTD - prior YTD, using the LATEST quarter (Q2) and its own
  comparative column:
    revenue  = 200 + 130 - 90  = 240      (the 3-month figure 70 must be ignored)
    net inc. = 100 +  75 - 50  = 125
    op. cash = 90  +  60 - 40  = 110
    capex    = 12  +   8 -  5  = 15      -> FCF = 95
  Equity at 2026-07-26 = 160 (from the 10-Q balance sheet).
  Valuation: 1,000 cover shares x $10 = $10,000 market cap
    TTM P/E = 10,000/125 = 80 ; annual P/E = 10,000/100 = 100 ; P/S = 10,000/240
    P/B = 10,000/160 = 62.5 ; FCF yield = 95/10,000 = 0.0095
"""
from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from meridian.analysis.metrics import ttm_summary
from meridian.analysis.valuation import current_valuation
from meridian.data.edgar import Company
from meridian.data.fundamentals import extract_annual, extract_cover_shares
from meridian.data.ttm import extract_ttm
from meridian.main import app, get_edgar_client, get_price_service

FY_START, FY_END = "2025-01-27", "2026-01-25"
K = dict(form="10-K", accn="k26", filed="2026-02-25")
Q1 = dict(form="10-Q", accn="q1", filed="2026-05-28")
Q2 = dict(form="10-Q", accn="q2", filed="2026-08-27")


def e(start, end, val, meta):
    d = {"end": end, "val": val, **meta}
    if start:
        d["start"] = start
    return d


def build(overrides=None, drop=(), extra_q=None):
    """Base facts; `drop` removes (tag, accn, start) entries; extra_q adds entries."""
    g = {
        "Revenues": [
            e(FY_START, FY_END, 200, K),
            # Q1: 3-month YTD and its comparative
            e("2026-01-26", "2026-04-26", 60, Q1),
            e("2025-01-27", "2025-04-27", 45, Q1),
            # Q2: 3-month quarter (ignore), 6-month YTD, and prior-year comparative
            e("2026-04-27", "2026-07-26", 70, Q2),
            e("2026-01-26", "2026-07-26", 130, Q2),
            e("2025-01-27", "2025-07-27", 90, Q2),
        ],
        "NetIncomeLoss": [
            e(FY_START, FY_END, 100, K),
            e("2026-01-26", "2026-04-26", 30, Q1),
            e("2025-01-27", "2025-04-27", 20, Q1),
            e("2026-01-26", "2026-07-26", 75, Q2),
            e("2025-01-27", "2025-07-27", 50, Q2),
        ],
        "NetCashProvidedByUsedInOperatingActivities": [
            e(FY_START, FY_END, 90, K),
            e("2026-01-26", "2026-07-26", 60, Q2),
            e("2025-01-27", "2025-07-27", 40, Q2),
        ],
        "PaymentsToAcquirePropertyPlantAndEquipment": [
            e(FY_START, FY_END, 12, K),
            e("2026-01-26", "2026-07-26", 8, Q2),
            e("2025-01-27", "2025-07-27", 5, Q2),
        ],
        "StockholdersEquity": [
            e(None, FY_END, 140, K),
            e(None, "2026-04-26", 150, Q1),
            e(None, "2026-07-26", 160, Q2),
        ],
    }
    for tag, accn, start in drop:
        g[tag] = [x for x in g[tag] if not (x["accn"] == accn and x.get("start") == start)]
    for tag, entry in (extra_q or []):
        g.setdefault(tag, []).append(entry)
    facts = {"entityName": "Acme", "facts": {"us-gaap": {t: {"units": {"USD": rows}} for t, rows in g.items()}}}
    facts["facts"]["dei"] = {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
        e("2026-02-18", None, 990, K) | {"end": "2026-02-18"},
        {"end": "2026-08-14", "val": 1000, **Q2},
    ]}}}
    return facts


def snapshot(facts):
    return extract_ttm(facts, extract_annual(facts))


def test_ttm_uses_latest_quarter_and_same_filing_comparatives():
    t = snapshot(build())
    assert t.basis == "ttm"
    assert t.fy_end == date(2026, 1, 25)
    assert t.ytd_end == date(2026, 7, 26)
    assert t.values["revenue"] == pytest.approx(240)  # not 200+70-..., not Q1-based
    assert t.values["net_income"] == pytest.approx(125)
    assert t.values["operating_cash_flow"] == pytest.approx(110)
    assert t.values["capex"] == pytest.approx(15)
    assert t.parts["revenue"] == (200.0, 130.0, 90.0)
    assert t.equity == pytest.approx(160)
    assert t.available_from == date(2026, 8, 27)
    assert "operating_income" not in t.values  # no data -> omitted, not guessed


def test_prior_ytd_must_come_from_the_same_filing():
    other = e("2025-01-27", "2025-07-27", 999, dict(form="10-Q", accn="other", filed="2026-09-01"))
    t = snapshot(build(extra_q=[("Revenues", other)]))
    assert t.values["revenue"] == pytest.approx(240)  # 999 ignored


def test_tolerates_52_53_week_calendar_shifts():
    # prior-year comparative dates shifted by a few days
    facts = build(drop=[("NetIncomeLoss", "q2", "2025-01-27")],
                  extra_q=[("NetIncomeLoss", e("2025-01-29", "2025-07-30", 50, Q2))])
    assert snapshot(facts).values["net_income"] == pytest.approx(125)


def test_metric_missing_a_component_is_omitted():
    facts = build(drop=[("PaymentsToAcquirePropertyPlantAndEquipment", "q2", "2025-01-27")])
    t = snapshot(facts)
    assert "capex" not in t.values
    assert t.values["net_income"] == pytest.approx(125)


def test_falls_back_to_annual_without_a_newer_10q():
    facts = build()
    for tag in facts["facts"]["us-gaap"].values():
        tag["units"]["USD"] = [x for x in tag["units"]["USD"] if x["form"] == "10-K"]
    t = snapshot(facts)
    assert t.basis == "annual"
    assert t.ytd_end is None
    assert t.values["revenue"] == 200.0 and t.values["net_income"] == 100.0
    assert t.available_from == date(2026, 2, 25)


def test_10q_from_an_older_fiscal_year_is_ignored():
    facts = build()
    for tag in facts["facts"]["us-gaap"].values():  # keep only prior-year comparatives
        tag["units"]["USD"] = [
            x for x in tag["units"]["USD"]
            if x["form"] == "10-K" or x.get("start", "").startswith("2025")
        ]
    assert snapshot(facts).basis == "annual"


def test_no_annual_data_gives_none():
    assert extract_ttm({"facts": {"us-gaap": {}}}, extract_annual({"facts": {"us-gaap": {}}})) is None


def test_ttm_summary_values_and_financial_blanking():
    s = ttm_summary(snapshot(build()))
    assert s["basis"] == "ttm" and s["through"] == "2026-07-26"
    assert s["free_cash_flow"] == pytest.approx(95)
    assert s["net_margin"] == pytest.approx(125 / 240)
    assert s["operating_margin"] is None
    assert ttm_summary(snapshot(build()), is_financial=True)["free_cash_flow"] is None
    assert ttm_summary(None) is None


def test_cover_shares_forms_option():
    facts = build()
    assert [c.value for c in extract_cover_shares(facts)] == [990.0]
    both = extract_cover_shares(facts, forms=("10-K", "10-Q"))
    assert [c.value for c in both] == [990.0, 1000.0]


# -- valuation / endpoints ---------------------------------------------------


def prices(close=10.0):
    idx = pd.bdate_range("2026-02-02", "2026-10-19", name="date")
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close,
                         "adj_close": close, "volume": 1.0, "split": 0.0}, index=idx)


def current(facts, ttm=True):
    annual = extract_annual(facts)
    return current_valuation(
        annual, extract_cover_shares(facts, forms=("10-K", "10-Q")), prices(),
        ttm=extract_ttm(facts, annual) if ttm else None,
    )


def test_current_valuation_uses_ttm_and_latest_quarterly_shares():
    cur = current(build())
    assert cur["basis"] == "ttm"
    assert cur["through"] == "2026-07-26"
    assert cur["market_cap"] == pytest.approx(10_000)  # 1,000 (10-Q cover) x $10
    assert cur["pe"] == pytest.approx(80)
    assert cur["annual_pe"] == pytest.approx(100)
    assert cur["ps"] == pytest.approx(10_000 / 240)
    assert cur["pb"] == pytest.approx(62.5)
    assert cur["fcf_yield"] == pytest.approx(0.0095)
    assert cur["stale_days"] == (date(2026, 10, 19) - date(2026, 7, 26)).days


def test_current_valuation_annual_when_ttm_not_given():
    cur = current(build(), ttm=False)
    assert cur["basis"] == "annual"
    assert cur["pe"] == pytest.approx(100)
    assert cur["through"] == "2026-01-25"


class StubEdgar:
    def __init__(self, facts):
        self.facts = facts

    def lookup_company(self, t):
        return Company(cik=1, ticker=t, title="Acme")

    def get_company_facts(self, c):
        return self.facts

    def get_sic(self, c):
        return 7372, "Software"


class StubPrices:
    def get_history(self, ticker, start=None, end=None):
        return prices()


@pytest.fixture
def client():
    app.dependency_overrides[get_edgar_client] = lambda: StubEdgar(build())
    app.dependency_overrides[get_price_service] = lambda: StubPrices()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_valuation_endpoint_reports_ttm(client):
    cur = client.get("/valuation/acme").json()["current"]
    assert cur["basis"] == "ttm"
    assert cur["pe"] == pytest.approx(80)
    assert cur["annual_pe"] == pytest.approx(100)


def test_analysis_endpoint_has_ttm_block(client):
    ttm = client.get("/analysis/acme").json()["ttm"]
    assert ttm["basis"] == "ttm"
    assert ttm["free_cash_flow"] == pytest.approx(95)
    assert ttm["available_from"] == "2026-08-27"
