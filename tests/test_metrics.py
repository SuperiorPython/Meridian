"""Tests for like-for-like metrics, versions, cover shares and /analysis.

Hand-computed expectations for the GE-style scenario (SCENARIO below):
  2023 revenue growth      = 90 / 80 - 1            = 0.125   (80 = 2022 as restated in
                              the 2023 filing; the first-reported 100 would give -0.10)
  2023 net income growth   = 31 / 8 - 1             = 2.875
  2023 operating inc growth= 18 / 12 - 1            = 0.5
  2023 FCF                 = 30 - 10                = 20 ; prior (same filing) = 20 - 5 = 15
  2023 FCF growth          = 20 / 15 - 1
  2023 margins             = 18/90, 31/90 ; FCF margin = 20/90
  2023 ROE                 = 31 / ((70 + 54) / 2)   = 0.5
  2023 liabilities/equity  = 140 / 70               = 2.0
  2022 ROE (no prior)      = 10 / 50                = 0.2
"""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from meridian.analysis.metrics import compute_metrics, is_financial_sic
from meridian.data.edgar import Company, EdgarError, TickerNotFound
from meridian.data.fundamentals import extract_annual, extract_cover_shares
from meridian.main import app, get_edgar_client


def fact(end, val, filed, accn, start=None, form="10-K"):
    d = {"end": end, "val": val, "filed": filed, "form": form, "accn": accn}
    if start:
        d["start"] = start
    return d


def build(concepts: dict, unit: str = "USD") -> dict:
    return {
        "cik": 1,
        "entityName": "Acme Corp",
        "facts": {
            "us-gaap": {tag: {"units": {unit: rows}} for tag, rows in concepts.items()}
        },
    }


Y22 = dict(start="2022-01-01")
Y23 = dict(start="2023-01-01")
F22, F23 = "2023-02-01", "2024-02-01"

SCENARIO = build(
    {
        "Revenues": [
            fact("2022-12-31", 100, F22, "a22", **Y22),
            fact("2022-12-31", 80, F23, "a23", **Y22),  # restated comparative
            fact("2023-12-31", 90, F23, "a23", **Y23),
        ],
        "NetIncomeLoss": [
            fact("2022-12-31", 10, F22, "a22", **Y22),
            fact("2022-12-31", 8, F23, "a23", **Y22),
            fact("2023-12-31", 31, F23, "a23", **Y23),
        ],
        "OperatingIncomeLoss": [
            fact("2022-12-31", 14, F22, "a22", **Y22),
            fact("2022-12-31", 12, F23, "a23", **Y22),
            fact("2023-12-31", 18, F23, "a23", **Y23),
        ],
        "NetCashProvidedByUsedInOperatingActivities": [
            fact("2022-12-31", 20, F22, "a22", **Y22),
            fact("2022-12-31", 20, F23, "a23", **Y22),
            fact("2023-12-31", 30, F23, "a23", **Y23),
        ],
        "PaymentsToAcquirePropertyPlantAndEquipment": [
            fact("2022-12-31", 5, F22, "a22", **Y22),
            fact("2022-12-31", 5, F23, "a23", **Y22),
            fact("2023-12-31", 10, F23, "a23", **Y23),
        ],
        "StockholdersEquity": [
            fact("2022-12-31", 50, F22, "a22"),
            fact("2022-12-31", 54, F23, "a23"),
            fact("2023-12-31", 70, F23, "a23"),
        ],
        "Liabilities": [fact("2023-12-31", 140, F23, "a23")],
    }
)


def metrics_by_end(facts, financial=False):
    return {
        m.period_end: m for m in compute_metrics(extract_annual(facts), financial)
    }


def test_like_for_like_growth_and_ratios():
    m = metrics_by_end(SCENARIO)[date(2023, 12, 31)]
    assert m.revenue_growth == pytest.approx(0.125)
    assert m.net_income_growth == pytest.approx(2.875)
    assert m.operating_income_growth == pytest.approx(0.5)
    assert m.free_cash_flow == pytest.approx(20)
    assert m.fcf_growth == pytest.approx(20 / 15 - 1)
    assert m.operating_margin == pytest.approx(18 / 90)
    assert m.net_margin == pytest.approx(31 / 90)
    assert m.fcf_margin == pytest.approx(20 / 90)
    assert m.roe == pytest.approx(0.5)
    assert m.liabilities_to_equity == pytest.approx(2.0)


def test_first_period_has_no_growth_and_uses_ending_equity():
    m = metrics_by_end(SCENARIO)[date(2022, 12, 31)]
    assert m.revenue_growth is None
    assert m.net_income_growth is None
    assert m.roe == pytest.approx(0.2)
    assert m.free_cash_flow == pytest.approx(15)
    assert m.liabilities_to_equity is None  # no liabilities reported for 2022


def test_available_from_is_latest_filing_date_for_the_period():
    by_end = metrics_by_end(SCENARIO)
    assert by_end[date(2022, 12, 31)].available_from == date(2023, 2, 1)
    assert by_end[date(2023, 12, 31)].available_from == date(2024, 2, 1)


def test_growth_none_when_prior_not_restated_in_same_filing():
    facts = build(
        {
            "Revenues": [
                fact("2022-12-31", 100, F22, "a22", **Y22),
                fact("2023-12-31", 90, F23, "a23", **Y23),  # no 2022 comparative in a23
            ]
        }
    )
    assert metrics_by_end(facts)[date(2023, 12, 31)].revenue_growth is None


def test_growth_none_when_prior_nonpositive():
    facts = build(
        {
            "NetIncomeLoss": [
                fact("2022-12-31", -5, F22, "a22", **Y22),
                fact("2022-12-31", -5, F23, "a23", **Y22),
                fact("2023-12-31", 10, F23, "a23", **Y23),
            ]
        }
    )
    assert metrics_by_end(facts)[date(2023, 12, 31)].net_income_growth is None


def test_growth_none_across_a_missing_year():
    facts = build(
        {
            "Revenues": [
                fact("2021-12-31", 100, "2022-02-01", "a21", start="2021-01-01"),
                fact("2021-12-31", 100, F23, "a23", start="2021-01-01"),
                fact("2023-12-31", 120, F23, "a23", **Y23),
            ]
        }
    )
    assert metrics_by_end(facts)[date(2023, 12, 31)].revenue_growth is None


def test_negative_equity_blanks_roe_and_leverage():
    facts = build(
        {
            "NetIncomeLoss": [fact("2023-12-31", 10, F23, "a23", **Y23)],
            "StockholdersEquity": [fact("2023-12-31", -10, F23, "a23")],
            "Liabilities": [fact("2023-12-31", 100, F23, "a23")],
        }
    )
    m = metrics_by_end(facts)[date(2023, 12, 31)]
    assert m.roe is None
    assert m.liabilities_to_equity is None


def test_missing_capex_means_no_fcf():
    facts = build(
        {
            "NetCashProvidedByUsedInOperatingActivities": [
                fact("2023-12-31", 30, F23, "a23", **Y23)
            ]
        }
    )
    m = metrics_by_end(facts)[date(2023, 12, 31)]
    assert m.free_cash_flow is None and m.fcf_margin is None


def test_financial_company_blanks_cash_flow_metrics_only():
    m = metrics_by_end(SCENARIO, financial=True)[date(2023, 12, 31)]
    assert m.free_cash_flow is None
    assert m.fcf_margin is None
    assert m.fcf_growth is None
    assert m.net_margin == pytest.approx(31 / 90)  # still computed
    assert m.roe == pytest.approx(0.5)


@pytest.mark.parametrize(
    "sic,expected",
    [(6021, True), (6211, True), (6331, True), (6411, True), (6412, False),
     (7372, False), (3571, False), (None, False)],
)
def test_is_financial_sic(sic, expected):
    assert is_financial_sic(sic) is expected


# -- versions --------------------------------------------------------------


def test_versions_keep_every_filing_and_value_in_lookup():
    rev = extract_annual(SCENARIO)["revenue"]
    fy22 = rev[0]
    assert [(v.accession, v.value) for v in fy22.versions] == [("a22", 100.0), ("a23", 80.0)]
    assert fy22.value_in("a22") == 100.0
    assert fy22.value_in("a23") == 80.0
    assert fy22.value_in("zzz") is None


def test_one_version_per_filing_using_best_tag():
    facts = build(
        {
            "NetIncomeLoss": [fact("2023-12-31", 50, F23, "a23", **Y23)],
            "ProfitLoss": [fact("2023-12-31", 55, F23, "a23", **Y23)],
        }
    )
    (row,) = extract_annual(facts)["net_income"]
    assert [(v.accession, v.value) for v in row.versions] == [("a23", 50.0)]


# -- shares ----------------------------------------------------------------


def test_diluted_shares_uses_shares_unit():
    facts = build(
        {
            "WeightedAverageNumberOfDilutedSharesOutstanding": [
                fact("2023-12-31", 1_000, F23, "a23", **Y23)
            ]
        },
        unit="shares",
    )
    (row,) = extract_annual(facts)["diluted_shares"]
    assert row.value == 1000.0


def cover(rows):
    return {
        "facts": {
            "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": rows}}}
        }
    }


def test_cover_shares_extracted_and_sorted():
    facts = cover(
        [
            fact("2024-01-20", 500, "2024-02-01", "a23"),
            fact("2023-01-20", 480, "2023-02-01", "a22"),
        ]
    )
    out = extract_cover_shares(facts)
    assert [(c.accession, c.value) for c in out] == [("a22", 480.0), ("a23", 500.0)]
    assert out[1].as_of == date(2024, 1, 20)


def test_cover_shares_skip_ambiguous_and_non_10k():
    facts = cover(
        [
            fact("2024-01-20", 500, "2024-02-01", "a23"),
            fact("2024-01-20", 300, "2024-02-01", "a23"),  # two classes, differing values
            fact("2024-04-20", 510, "2024-05-01", "q1", form="10-Q"),
        ]
    )
    assert extract_cover_shares(facts) == []


# -- /analysis endpoint ----------------------------------------------------


class Stub:
    def __init__(self, sic=(7372, "Services-Prepackaged Software"), sic_error=False):
        self.sic = sic
        self.sic_error = sic_error

    def lookup_company(self, ticker):
        if ticker == "NOPE":
            raise TickerNotFound("nope")
        return Company(cik=1, ticker=ticker, title="Acme Corp")

    def get_company_facts(self, company):
        return SCENARIO

    def get_sic(self, company):
        if self.sic_error:
            raise EdgarError("submissions unavailable")
        return self.sic


@pytest.fixture
def use():
    def _use(stub):
        app.dependency_overrides[get_edgar_client] = lambda: stub
        return TestClient(app)

    yield _use
    app.dependency_overrides.clear()


def test_analysis_endpoint(use):
    body = use(Stub()).get("/analysis/acme").json()
    assert body["is_financial"] is False
    assert body["sic"] == 7372
    assert [p["period_end"] for p in body["periods"]] == ["2022-12-31", "2023-12-31"]
    last = body["periods"][-1]
    assert last["revenue_growth"] == pytest.approx(0.125)
    assert last["available_from"] == "2024-02-01"
    assert any("like-for-like" in n for n in body["notes"])


def test_analysis_financial_company(use):
    body = use(Stub(sic=(6021, "National Commercial Banks"))).get("/analysis/acme").json()
    assert body["is_financial"] is True
    assert body["periods"][-1]["free_cash_flow"] is None
    assert any("Financial company" in n for n in body["notes"])


def test_analysis_degrades_when_sic_lookup_fails(use):
    r = use(Stub(sic_error=True)).get("/analysis/acme")
    assert r.status_code == 200
    body = r.json()
    assert body["sic"] is None and body["is_financial"] is False
    assert any("Sector unknown" in n for n in body["notes"])


def test_analysis_years_window(use):
    body = use(Stub()).get("/analysis/acme?years=1").json()
    assert [p["period_end"] for p in body["periods"]] == ["2023-12-31"]


def test_analysis_validation(use):
    client = use(Stub())
    assert client.get("/analysis/acme?years=0").status_code == 422
    assert client.get("/analysis/bad$ticker").status_code == 422
    assert client.get("/analysis/NOPE").status_code == 404
