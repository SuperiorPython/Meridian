"""Valuation tests. Hand-computed scenario (SCENARIO):

  FY ends 2019-09-28, 10-K filed 2019-10-31. Cover shares 4,000 (as of 2019-10-18).
  Yahoo-basis close = 62.5 every day. A 4-for-1 split has ex-date 2020-08-31, so:
    actual close on 2019-10-31 = 62.5 * 4        = 250.0
    shares on today's basis    = 4,000 * 4       = 16,000
    market cap                 = 16,000 * 62.5   = 1,000,000   (= 4,000 * 250)
  Fundamentals: net income 100,000, revenue 500,000, equity 200,000,
                operating cash flow 150,000, capex 20,000 (FCF 130,000)
    P/E = 10 ; P/S = 2 ; P/B = 5 ; FCF yield = 130,000 / 1,000,000 = 0.13
  Without the split adjustment the market cap would be 250,000 (4x too small).
"""
from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from meridian.analysis.valuation import (
    close_on_or_after,
    compute_valuation,
    current_valuation,
    split_factor_after,
)
from meridian.data.edgar import Company, EdgarError, TickerNotFound
from meridian.data.fundamentals import extract_annual, extract_cover_shares
from meridian.data.prices import NoDataError, ProviderError
from meridian.main import app, get_edgar_client, get_price_service

SPLIT_4 = {"2020-08-31": 4.0}


def prices_frame(start="2019-10-21", end="2020-09-30", close=62.5, splits=None):
    idx = pd.bdate_range(start, end, name="date")
    df = pd.DataFrame(
        {
            "open": close, "high": close, "low": close, "close": close,
            "adj_close": close * 0.9, "volume": 1000.0, "split": 0.0,
        },
        index=idx,
    )
    for d, ratio in (splits or {}).items():
        df.loc[pd.Timestamp(d), "split"] = ratio
    return df


def facts(
    filed="2019-10-31", accn="a19", ni=100_000, rev=500_000, equity=200_000,
    ocf=150_000, capex=20_000, cover=4_000, diluted=None, cover_as_of="2019-10-18",
):
    end, start = "2019-09-28", "2018-09-30"

    def dur(val):
        return [{"end": end, "start": start, "val": val, "filed": filed,
                 "form": "10-K", "accn": accn}]

    gaap = {}
    for tag, val in (
        ("NetIncomeLoss", ni), ("Revenues", rev),
        ("NetCashProvidedByUsedInOperatingActivities", ocf),
        ("PaymentsToAcquirePropertyPlantAndEquipment", capex),
    ):
        if val is not None:
            gaap[tag] = {"units": {"USD": dur(val)}}
    if equity is not None:
        gaap["StockholdersEquity"] = {"units": {"USD": [
            {"end": end, "val": equity, "filed": filed, "form": "10-K", "accn": accn}]}}
    if diluted is not None:
        gaap["WeightedAverageNumberOfDilutedSharesOutstanding"] = {
            "units": {"shares": dur(diluted)}}
    out = {"entityName": "Acme Corp", "facts": {"us-gaap": gaap}}
    if cover is not None:
        out["facts"]["dei"] = {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            {"end": cover_as_of, "val": cover, "filed": filed, "form": "10-K",
             "accn": accn}]}}}
    return out


def run(f, prices, financial=False):
    return compute_valuation(
        extract_annual(f), extract_cover_shares(f), prices, is_financial=financial
    )


def test_market_cap_converts_cover_shares_to_split_adjusted_basis():
    (p,) = run(facts(), prices_frame(splits=SPLIT_4))
    assert p.shares_source == "cover"
    assert p.price_date == date(2019, 10, 31)
    assert p.close == 62.5
    assert p.price_then == pytest.approx(250.0)
    assert p.split_factor == 4.0
    assert p.shares_adjusted == pytest.approx(16_000)
    assert p.market_cap == pytest.approx(1_000_000)
    assert p.pe == pytest.approx(10)
    assert p.ps == pytest.approx(2)
    assert p.pb == pytest.approx(5)
    assert p.fcf_yield == pytest.approx(0.13)


def test_no_split_leaves_shares_unchanged():
    (p,) = run(facts(), prices_frame())
    assert p.split_factor == 1.0
    assert p.market_cap == pytest.approx(4_000 * 62.5)
    assert p.pe == pytest.approx(2.5)


def test_reverse_split():
    (p,) = run(facts(), prices_frame(splits={"2020-08-31": 0.1}))
    assert p.split_factor == pytest.approx(0.1)
    assert p.shares_adjusted == pytest.approx(400)


def test_multiple_splits_multiply():
    splits = {"2020-03-02": 2.0, "2020-08-31": 3.0}
    (p,) = run(facts(), prices_frame(splits=splits))
    assert p.split_factor == pytest.approx(6.0)


def test_split_on_cover_date_not_counted_but_split_before_filing_is():
    # ex-date on the cover date: count is already post-split
    (p,) = run(facts(), prices_frame(splits={"2019-10-18": 5.0}))
    assert p.split_factor == 1.0
    # ex-date between cover date and filing: count is pre-split, price is post-split
    (p,) = run(facts(), prices_frame(splits={"2019-10-22": 5.0}))
    assert p.split_factor == 5.0


def test_split_factor_after_is_strict():
    splits = pd.Series([2.0], index=[pd.Timestamp("2020-01-02")])
    assert split_factor_after(splits, date(2020, 1, 2)) == 1.0
    assert split_factor_after(splits, date(2020, 1, 1)) == 2.0


def test_price_date_rolls_to_next_trading_day():
    (p,) = run(facts(filed="2019-11-02"), prices_frame())  # a Saturday
    assert p.price_date == date(2019, 11, 4)


def test_no_price_near_filing_gives_no_market_cap():
    (p,) = run(facts(), prices_frame(start="2019-12-02"))
    assert p.price_date is None and p.market_cap is None and p.pe is None


def test_fallback_to_diluted_average_shares():
    (p,) = run(facts(cover=None, diluted=4_000), prices_frame(splits=SPLIT_4))
    assert p.shares_source == "diluted_average"
    assert p.shares_as_of == date(2019, 10, 31)
    assert p.market_cap == pytest.approx(1_000_000)


def test_no_share_data_means_no_market_cap():
    (p,) = run(facts(cover=None), prices_frame())
    assert p.shares_source is None
    assert p.market_cap is None and p.pe is None
    assert p.close == 62.5  # price still reported


def test_loss_and_negative_equity_blank_their_ratios():
    (p,) = run(facts(ni=-5, equity=-1), prices_frame())
    assert p.pe is None
    assert p.pb is None
    assert p.ps is not None


def test_financial_company_has_no_fcf_yield():
    (p,) = run(facts(), prices_frame(), financial=True)
    assert p.fcf_yield is None
    assert p.pe is not None


def test_close_on_or_after_none_past_end_of_history():
    assert close_on_or_after(prices_frame(end="2019-11-01"), date(2020, 1, 1)) is None


def test_current_valuation():
    f = facts()
    cur = current_valuation(
        extract_annual(f), extract_cover_shares(f), prices_frame(splits=SPLIT_4)
    )
    assert cur["price_date"] == "2020-09-30"
    assert cur["close"] == 62.5
    assert cur["market_cap"] == pytest.approx(1_000_000)
    assert cur["pe"] == pytest.approx(10)
    assert cur["latest_period_end"] == "2019-09-28"
    assert cur["stale_days"] == (date(2020, 9, 30) - date(2019, 9, 28)).days


def test_current_valuation_none_without_prices_or_shares():
    f = facts()
    assert current_valuation(
        extract_annual(f), extract_cover_shares(f), pd.DataFrame()
    ) is None
    g = facts(cover=None)
    assert current_valuation(extract_annual(g), [], prices_frame()) is None


# -- endpoint --------------------------------------------------------------


class StubEdgar:
    def __init__(self, f, sic=(7372, "Software")):
        self.f, self.sic = f, sic

    def lookup_company(self, ticker):
        if ticker == "NOPE":
            raise TickerNotFound("nope")
        return Company(cik=1, ticker=ticker, title="Acme Corp")

    def get_company_facts(self, company):
        return self.f

    def get_sic(self, company):
        if self.sic is None:
            raise EdgarError("down")
        return self.sic


class StubPrices:
    def __init__(self, df=None, error=None):
        self.df, self.error = df, error

    def get_history(self, ticker, start=None, end=None):
        if self.error:
            raise self.error
        return self.df


@pytest.fixture
def use():
    def _use(edgar=None, prices=None):
        edgar = edgar or StubEdgar(facts())
        prices = prices or StubPrices(prices_frame(splits=SPLIT_4))
        app.dependency_overrides[get_edgar_client] = lambda: edgar
        app.dependency_overrides[get_price_service] = lambda: prices
        return TestClient(app)

    yield _use
    app.dependency_overrides.clear()


def test_valuation_endpoint(use):
    body = use().get("/valuation/acme").json()
    assert body["is_financial"] is False
    (p,) = body["periods"]
    assert p["period_end"] == "2019-09-28"
    assert p["market_cap"] == pytest.approx(1_000_000)
    assert p["split_factor"] == 4.0
    assert body["current"]["pe"] == pytest.approx(10)
    assert any("split" in n for n in body["notes"])


def test_valuation_financial_note_and_no_fcf_yield(use):
    body = use(StubEdgar(facts(), sic=(6021, "Banks"))).get("/valuation/acme").json()
    assert body["is_financial"] is True
    assert body["periods"][0]["fcf_yield"] is None


def test_valuation_survives_sic_failure(use):
    r = use(StubEdgar(facts(), sic=None)).get("/valuation/acme")
    assert r.status_code == 200
    assert r.json()["is_financial"] is False


def test_valuation_errors(use):
    assert use().get("/valuation/NOPE").status_code == 404
    assert use().get("/valuation/bad$ticker").status_code == 422
    assert use().get("/valuation/acme?years=0").status_code == 422
    no_data = use(prices=StubPrices(error=NoDataError("none")))
    assert no_data.get("/valuation/acme").status_code == 404
    broken = use(prices=StubPrices(error=ProviderError("boom")))
    assert broken.get("/valuation/acme").status_code == 502


def test_no_prices_with_shares_does_not_crash():
    (p,) = run(facts(), pd.DataFrame())
    assert p.market_cap is None and p.price_date is None
    assert p.shares_reported == 4_000
    assert p.split_factor is None and p.shares_adjusted is None


def test_prices_without_split_column_assume_no_splits():
    px = prices_frame().drop(columns=["split"])
    (p,) = run(facts(), px)
    assert p.split_factor == 1.0
    assert p.market_cap == pytest.approx(4_000 * 62.5)
