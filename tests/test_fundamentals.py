from datetime import date

import pytest
from fastapi.testclient import TestClient

from meridian.data.edgar import Company, TickerNotFound
from meridian.data.fundamentals import METRICS, extract_annual
from meridian.main import app, get_edgar_client

REV = "RevenueFromContractWithCustomerExcludingAssessedTax"


def fact(end, val, filed, form="10-K", start=None, accn="0000000000-00-000001"):
    d = {"end": end, "val": val, "filed": filed, "form": form, "accn": accn}
    if start:
        d["start"] = start
    return d


def facts_json(concepts: dict, units: str = "USD") -> dict:
    return {
        "cik": 1,
        "entityName": "Acme Corp",
        "facts": {
            "us-gaap": {tag: {"units": {units: rows}} for tag, rows in concepts.items()}
        },
    }


def test_all_metrics_present_even_when_empty():
    out = extract_annual({"facts": {"us-gaap": {}}})
    assert set(out) == set(METRICS)
    assert all(v == [] for v in out.values())


def test_no_us_gaap_taxonomy():
    out = extract_annual({"facts": {"ifrs-full": {}}})
    assert all(v == [] for v in out.values())


def test_annual_duration_only():
    data = facts_json(
        {
            "NetIncomeLoss": [
                fact("2023-09-30", 97, "2023-11-03", start="2022-09-30"),  # annual
                fact("2023-06-30", 22, "2023-08-04", form="10-Q", start="2023-04-01"),
                fact("2023-12-30", 5, "2024-02-01", start="2023-10-01"),  # 90 days in a 10-K
            ]
        }
    )
    rows = extract_annual(data)["net_income"]
    assert [(r.period_end, r.value) for r in rows] == [(date(2023, 9, 30), 97.0)]


def test_52_week_year_accepted():
    data = facts_json(
        {"NetIncomeLoss": [fact("2022-09-24", 10, "2022-10-28", start="2021-09-26")]}
    )
    assert len(extract_annual(data)["net_income"]) == 1  # 363 days


def test_non_10k_excluded():
    data = facts_json({"Assets": [fact("2023-09-30", 1000, "2023-08-04", form="10-Q")]})
    assert extract_annual(data)["total_assets"] == []


def test_non_usd_units_ignored():
    data = facts_json(
        {"NetIncomeLoss": [fact("2023-09-30", 1, "2023-11-03", start="2022-09-30")]},
        units="EUR",
    )
    assert extract_annual(data)["net_income"] == []


def test_first_reported_vs_restated():
    data = facts_json(
        {
            REV: [
                fact("2022-09-24", 100, "2022-10-28", start="2021-09-26", accn="a"),
                fact("2022-09-24", 110, "2023-10-27", start="2021-09-26", accn="b"),
            ]
        }
    )
    (row,) = extract_annual(data)["revenue"]
    assert row.value == 100.0
    assert row.filed == date(2022, 10, 28)
    assert row.accession == "a"
    assert row.latest_value == 110.0
    assert row.latest_filed == date(2023, 10, 27)


def test_tag_switch_merges_into_one_series():
    data = facts_json(
        {
            "SalesRevenueNet": [
                fact("2017-09-30", 229, "2017-11-03", start="2016-10-01")
            ],
            REV: [fact("2018-09-29", 265, "2018-11-05", start="2017-09-30")],
        }
    )
    rows = extract_annual(data)["revenue"]
    assert [r.value for r in rows] == [229.0, 265.0]
    assert [r.tag for r in rows] == ["SalesRevenueNet", REV]


def test_priority_breaks_ties_on_same_filing():
    data = facts_json(
        {
            "NetIncomeLoss": [fact("2023-09-30", 50, "2023-11-03", start="2022-09-30")],
            "ProfitLoss": [fact("2023-09-30", 55, "2023-11-03", start="2022-09-30")],
        }
    )
    (row,) = extract_annual(data)["net_income"]
    assert (row.tag, row.value) == ("NetIncomeLoss", 50.0)


def test_earliest_filing_wins_across_tags():
    data = facts_json(
        {
            REV: [fact("2019-12-31", 200, "2021-02-01", start="2019-01-01")],  # recast later
            "Revenues": [fact("2019-12-31", 190, "2020-02-01", start="2019-01-01")],
        }
    )
    (row,) = extract_annual(data)["revenue"]
    assert (row.tag, row.value, row.filed) == ("Revenues", 190.0, date(2020, 2, 1))


def test_instants_kept_only_at_fiscal_year_ends():
    data = facts_json(
        {
            "NetIncomeLoss": [fact("2023-09-30", 5, "2023-11-03", start="2022-09-30")],
            "Assets": [
                fact("2023-09-30", 1000, "2023-11-03"),
                fact("2023-12-30", 900, "2023-11-03"),
            ],
        }
    )
    rows = extract_annual(data)["total_assets"]
    assert [r.period_end for r in rows] == [date(2023, 9, 30)]
    assert rows[0].period_start is None


def test_instants_unfiltered_when_no_duration_data():
    data = facts_json({"Assets": [fact("2023-09-30", 1000, "2023-11-03")]})
    assert len(extract_annual(data)["total_assets"]) == 1


def test_sorted_by_period_end():
    data = facts_json(
        {
            "NetIncomeLoss": [
                fact("2023-09-30", 3, "2023-11-03", start="2022-09-30"),
                fact("2021-09-30", 1, "2021-11-03", start="2020-09-30"),
                fact("2022-09-30", 2, "2022-11-03", start="2021-09-30"),
            ]
        }
    )
    assert [r.value for r in extract_annual(data)["net_income"]] == [1.0, 2.0, 3.0]


# -- endpoint --------------------------------------------------------------

API_FACTS = facts_json(
    {
        "NetIncomeLoss": [
            fact("2022-09-30", 2, "2022-11-03", start="2021-09-30"),
            fact("2023-09-30", 3, "2023-11-03", start="2022-09-30"),
        ]
    }
)


class StubEdgar:
    def lookup_company(self, ticker):
        if ticker == "NOPE":
            raise TickerNotFound("nope")
        return Company(cik=1, ticker=ticker, title="Acme Corp")

    def get_company_facts(self, company):
        return API_FACTS


@pytest.fixture
def api():
    app.dependency_overrides[get_edgar_client] = lambda: StubEdgar()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_fundamentals_endpoint(api):
    r = api.get("/fundamentals/acme")
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "ACME"
    assert [x["value"] for x in body["metrics"]["net_income"]] == [2.0, 3.0]
    assert "revenue" in body["missing"]
    assert "net_income" not in body["missing"]


def test_fundamentals_years_limit(api):
    body = api.get("/fundamentals/acme?years=1").json()
    assert [x["value"] for x in body["metrics"]["net_income"]] == [3.0]


def test_fundamentals_validation(api):
    assert api.get("/fundamentals/acme?years=0").status_code == 422
    assert api.get("/fundamentals/bad$ticker").status_code == 422
    assert api.get("/fundamentals/NOPE").status_code == 404
