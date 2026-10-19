from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from meridian.analysis import paper as calc
from meridian.analysis.paper import LedgerError, Trade
from meridian.data.prices import NoDataError, ProviderError
from meridian.deps import get_price_service
from meridian.main import app
from meridian.storage.db import Database, get_database
from meridian.storage.paper import PaperLedger


def frame(closes, start="2026-01-05", splits=None):
    """Price frame of already split-adjusted closes on consecutive business days."""
    idx = pd.bdate_range(start, periods=len(closes))
    df = pd.DataFrame({"close": [float(c) for c in closes]}, index=idx)
    df["split"] = 0.0
    for d, ratio in (splits or {}).items():
        df.loc[pd.Timestamp(d), "split"] = ratio
    df.index.name = "date"
    return df


def trade(i, side, shares, price, day, ticker="AAA"):
    return Trade(i, ticker, side, shares, price, date.fromisoformat(day))


def split_series(**by_day):
    return pd.Series({pd.Timestamp(d): r for d, r in by_day.items()}, dtype=float)


SPLIT_4X = pd.Series({pd.Timestamp("2026-01-12"): 4.0})


# ---------- ledger rules ----------

def test_cash_rule():
    ok = [trade(1, "buy", 10, 60, "2026-01-05"), trade(2, "sell", 10, 70, "2026-01-06"),
          trade(3, "buy", 10, 50, "2026-01-07")]
    calc.check_cash(ok, 1000)  # sale proceeds fund the later buy
    bad = [trade(1, "buy", 10, 60, "2026-01-05"), trade(2, "buy", 10, 50, "2026-01-06")]
    with pytest.raises(LedgerError, match="Not enough cash"):
        calc.check_cash(bad, 1000)


def test_same_day_trades_apply_in_id_order():
    buy_first = [trade(1, "buy", 10, 100, "2026-01-05"), trade(2, "sell", 5, 100, "2026-01-05")]
    with pytest.raises(LedgerError):  # the $1,000 buy comes first, before the sale's proceeds
        calc.check_cash(buy_first, 900)
    sell_first = [trade(1, "sell", 5, 100, "2026-01-05"), trade(2, "buy", 10, 100, "2026-01-05")]
    calc.check_cash(sell_first, 900)  # same trades, other order: the sale funds the buy


def test_share_rule_no_shorting():
    calc.check_shares([trade(1, "buy", 10, 5, "2026-01-05"), trade(2, "sell", 10, 6, "2026-01-06")],
                      calc.splits_of(None))
    with pytest.raises(LedgerError, match="only hold 10"):
        calc.check_shares([trade(1, "buy", 10, 5, "2026-01-05"), trade(2, "sell", 11, 6, "2026-01-06")],
                          calc.splits_of(None))


def test_share_rule_respects_splits():
    buy = trade(1, "buy", 10, 100, "2026-01-05")  # becomes 40 shares after the 4:1 split
    calc.check_shares([buy, trade(2, "sell", 40, 30, "2026-01-13")], SPLIT_4X)
    with pytest.raises(LedgerError, match="only hold 40"):
        calc.check_shares([buy, trade(2, "sell", 41, 30, "2026-01-13")], SPLIT_4X)


def test_actual_price_undoes_later_splits_only():
    assert calc.actual_price(25.0, SPLIT_4X, date(2026, 1, 6)) == 100.0
    assert calc.actual_price(25.0, SPLIT_4X, date(2026, 1, 12)) == 25.0  # ex-date itself is post-split
    assert calc.actual_price(25.0, SPLIT_4X, date(2026, 1, 13)) == 25.0
    assert calc.actual_price(12.3456, calc.splits_of(None), date(2026, 1, 6)) == 12.3456


# ---------- replay: cash, average cost, realized profit ----------

def test_average_cost_and_realized_profit():
    trades = [trade(1, "buy", 10, 10, "2026-01-05"), trade(2, "buy", 10, 20, "2026-01-06"),
              trade(3, "sell", 10, 25, "2026-01-07")]
    cash, pos = calc.replay(trades, 1000, {})
    p = pos["AAA"]
    assert cash == 1000 - 100 - 200 + 250
    assert p.shares == 10 and p.cost == 150  # average cost 15
    assert p.realized == 100  # 250 - 10 * 15


def test_replay_in_todays_share_basis_after_split():
    trades = [trade(1, "buy", 10, 100, "2026-01-05"), trade(2, "sell", 20, 30, "2026-01-13")]
    cash, pos = calc.replay(trades, 2000, {"AAA": SPLIT_4X})
    p = pos["AAA"]
    assert p.shares == 20  # 40 after the split, minus 20 sold
    assert p.cost == 500  # average cost 25 per today's share
    assert p.realized == 100  # 600 proceeds - 20 * 25
    assert cash == 2000 - 1000 + 600


def test_selling_everything_resets_position():
    trades = [trade(1, "buy", 3, 10, "2026-01-05"), trade(2, "sell", 3, 12, "2026-01-06")]
    _, pos = calc.replay(trades, 1000, {})
    assert pos["AAA"].shares == 0 and pos["AAA"].cost == 0 and pos["AAA"].realized == 6


# ---------- summary ----------

def test_summary_values_and_weights():
    frames = {"AAA": frame([10, 11, 12, 13, 14])}
    s = calc.summary([trade(1, "buy", 100, 10, "2026-01-05")], 10000, frames, date(2026, 1, 9))
    assert s["cash"] == 9000 and s["positions_value"] == 1400 and s["total_value"] == 10400
    assert s["total_return"] == 0.04 and s["unrealized_pnl"] == 400 and s["realized_pnl"] == 0
    h = s["holdings"][0]
    assert (h["shares"], h["avg_cost"], h["price"], h["market_value"]) == (100, 10, 14, 1400)
    assert h["unrealized_pnl"] == 400 and h["unrealized_pct"] == 0.4
    assert h["weight"] == round(1400 / 10400, 4)
    assert not s["incomplete"]


def test_summary_incomplete_without_prices():
    s = calc.summary([trade(1, "buy", 100, 10, "2026-01-05")], 10000, {}, date(2026, 1, 9))
    assert s["incomplete"] and s["total_value"] is None and s["total_return"] is None
    assert s["cash"] == 9000 and s["holdings"][0]["market_value"] is None
    assert any("could not be loaded" in n for n in s["notes"])


def test_summary_flags_stale_prices_and_hides_closed_positions():
    frames = {"AAA": frame([10, 11, 12, 13, 14])}
    trades = [trade(1, "buy", 10, 10, "2026-01-05"), trade(2, "sell", 10, 12, "2026-01-07")]
    s = calc.summary(trades, 1000, frames, date(2026, 3, 1))
    assert s["holdings"] == [] and s["realized_pnl"] == 20
    t = calc.summary([trade(1, "buy", 10, 10, "2026-01-05")], 1000, frames, date(2026, 3, 1))
    assert any("days old" in n for n in t["notes"])


def test_summary_split_adjusts_position():
    adj = frame([25, 25, 25, 26, 27], splits={"2026-01-08": 4.0})
    s = calc.summary([trade(1, "buy", 10, 100, "2026-01-05")], 10000, {"AAA": adj}, date(2026, 1, 9))
    h = s["holdings"][0]
    assert h["shares"] == 40 and h["avg_cost"] == 25 and h["market_value"] == 1080
    assert s["total_value"] == 9000 + 1080


# ---------- performance ----------

def test_performance_against_benchmark():
    frames = {"AAA": frame([10, 11, 12, 13, 14])}
    spy = frame([100, 100, 100, 100, 102])
    p = calc.performance([trade(1, "buy", 100, 10, "2026-01-05")], 10000, frames, spy)
    assert p["series"]["portfolio"] == [10000, 10100, 10200, 10300, 10400]
    assert p["series"]["benchmark"] == [10000, 10000, 10000, 10000, 10200]
    assert (p["total_return"], p["benchmark_return"], p["excess_return"]) == (0.04, 0.02, 0.02)
    assert p["first_date"] == "2026-01-05" and p["last_date"] == "2026-01-09"


def test_performance_with_a_sale():
    frames = {"AAA": frame([10, 11, 12, 13, 14])}
    trades = [trade(1, "buy", 100, 10, "2026-01-05"), trade(2, "sell", 50, 12, "2026-01-07")]
    p = calc.performance(trades, 10000, frames, None)
    assert p["series"]["portfolio"][-1] == 9000 + 600 + 50 * 14  # cash plus 50 shares at 14
    assert p["benchmark_return"] is None and p["series"]["benchmark"] is None
    assert any("unavailable" in n for n in p["notes"])


def test_performance_starts_at_first_trade_not_first_price():
    frames = {"AAA": frame([10, 11, 12, 13, 14])}
    p = calc.performance([trade(1, "buy", 10, 12, "2026-01-07")], 1000, frames, None)
    assert p["first_date"] == "2026-01-07" and len(p["series"]["dates"]) == 3


def test_performance_is_continuous_across_a_split():
    adj = frame([25, 25, 25, 26, 27], splits={"2026-01-08": 4.0})  # actual price was 100 before
    p = calc.performance([trade(1, "buy", 10, 100, "2026-01-05")], 10000, {"AAA": adj}, None)
    assert p["series"]["portfolio"] == [10000, 10000, 10000, 10040, 10080]  # no jump on the split day


def test_performance_empty():
    p = calc.performance([], 1000, {}, None)
    assert p["empty"] is True


# ---------- storage ----------

@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "paper.db")


def test_account_default_and_lock_after_trades(db):
    ledger = PaperLedger(db)
    assert ledger.starting_cash() == 100000
    assert ledger.set_starting_cash(5000) and ledger.starting_cash() == 5000
    ledger.add("AAA", "buy", 1, 10, date(2026, 1, 5), date(2026, 1, 5))
    assert not ledger.set_starting_cash(9000) and ledger.starting_cash() == 5000


def test_trades_ordered_by_effective_date(db):
    ledger = PaperLedger(db)
    ledger.add("AAA", "buy", 1, 10, date(2026, 1, 7), date(2026, 1, 7))
    ledger.add("BBB", "buy", 1, 10, date(2026, 1, 5), date(2026, 1, 5))  # backdated, added later
    assert [t["ticker"] for t in ledger.trades()] == ["BBB", "AAA"]
    assert [t["ticker"] for t in ledger.trades("AAA")] == ["AAA"]


def test_database_rejects_bad_trade_values(db):
    import sqlite3
    ledger = PaperLedger(db)
    with pytest.raises(sqlite3.IntegrityError):
        ledger.add("AAA", "short", 1, 10, date(2026, 1, 5), date(2026, 1, 5))
    with pytest.raises(sqlite3.IntegrityError):
        ledger.add("AAA", "buy", 0, 10, date(2026, 1, 5), date(2026, 1, 5))


# ---------- API ----------

class StubPrices:
    def __init__(self, frames, errors=None):
        self.frames, self.errors = frames, errors or {}

    def get_history(self, ticker, start=None, end=None):
        if ticker in self.errors:
            raise self.errors[ticker]
        if ticker not in self.frames:
            raise NoDataError(f"no price data for {ticker}")
        return self.frames[ticker]


CLOSES = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19]  # 2026-01-05 .. 2026-01-16


@pytest.fixture
def setup(tmp_path):
    database = Database(tmp_path / "api.db")
    stub = StubPrices({"AAA": frame(CLOSES), "SPY": frame([100] * 10)})
    app.dependency_overrides[get_database] = lambda: database
    app.dependency_overrides[get_price_service] = lambda: stub
    yield TestClient(app), stub
    app.dependency_overrides.clear()


def buy(client, shares, day=None, ticker="AAA", side="buy"):
    body = {"ticker": ticker, "side": side, "shares": shares}
    if day:
        body["date"] = day
    return client.post("/paper/trades", json=body)


def test_trade_fills_at_close_on_requested_day(setup):
    client, _ = setup
    r = buy(client, 100, "2026-01-06")
    assert r.status_code == 201
    t = r.json()
    assert (t["price"], t["trade_date"], t["requested_date"], t["amount"]) == (11.0, "2026-01-06", "2026-01-06", 1100.0)


def test_trade_defaults_to_latest_close(setup):
    client, _ = setup
    t = buy(client, 10).json()
    assert t["trade_date"] == "2026-01-16" and t["price"] == 19.0


def test_weekend_trade_fills_next_trading_day(setup):
    client, _ = setup
    t = buy(client, 10, "2026-01-10").json()  # a Saturday
    assert t["requested_date"] == "2026-01-10" and t["trade_date"] == "2026-01-12" and t["price"] == 15.0


def test_trade_date_problems(setup):
    client, _ = setup
    future = buy(client, 10, "2026-02-01")
    assert future.status_code == 422 and "2026-01-16" in future.json()["detail"]
    early = buy(client, 10, "2025-01-01")
    assert early.status_code == 422 and "within a week" in early.json()["detail"]


def test_trade_input_and_price_errors(setup):
    client, stub = setup
    assert buy(client, 10, ticker="ZZZ").status_code == 404
    stub.errors["AAA"] = ProviderError("yahoo down")
    assert buy(client, 10).status_code == 502
    stub.errors.clear()
    assert buy(client, 0).status_code == 422
    assert buy(client, -5).status_code == 422
    assert buy(client, 10, ticker="BAD$").status_code == 422
    assert client.post("/paper/trades", json={"ticker": "AAA", "side": "short", "shares": 1}).status_code == 422


def test_cannot_overspend_or_oversell(setup):
    client, _ = setup
    assert client.put("/paper/account", json={"starting_cash": 10000}).status_code == 200
    over = buy(client, 1001, "2026-01-05")  # 1001 x $10 = $10,010
    assert over.status_code == 409 and "Not enough cash" in over.json()["detail"]
    assert buy(client, 1000, "2026-01-05").status_code == 201  # exactly all the cash is fine
    short = buy(client, 1001, "2026-01-06", side="sell")
    assert short.status_code == 409 and "only hold 1000" in short.json()["detail"]
    assert client.get("/paper/trades").json()["count"] == 1  # rejected trades leave no trace


def test_cannot_sell_what_you_never_bought(setup):
    client, _ = setup
    assert buy(client, 1, side="sell").status_code == 409


def test_backdated_sell_before_the_buy_is_rejected(setup):
    client, _ = setup
    assert buy(client, 10, "2026-01-08").status_code == 201
    r = buy(client, 10, "2026-01-06", side="sell")  # would sell before owning
    assert r.status_code == 409


def test_deleting_a_trade_that_later_trades_depend_on(setup):
    client, _ = setup
    first = buy(client, 100, "2026-01-05").json()
    second = buy(client, 100, "2026-01-07", side="sell").json()
    blocked = client.delete(f"/paper/trades/{first['id']}")
    assert blocked.status_code == 409 and "later trades depend" in blocked.json()["detail"]
    assert client.delete(f"/paper/trades/{second['id']}").json() == {"deleted": second["id"]}
    assert client.delete(f"/paper/trades/{first['id']}").status_code == 200
    assert client.delete(f"/paper/trades/{first['id']}").status_code == 404


def test_deleting_a_buy_that_funded_a_later_buy(setup):
    client, _ = setup
    client.put("/paper/account", json={"starting_cash": 1500})
    a = buy(client, 100, "2026-01-05").json()  # $1,000
    buy(client, 100, "2026-01-07", side="sell")  # back to $1,200 + ...
    buy(client, 100, "2026-01-08")  # $1,300 more, funded by the sale
    assert client.delete(f"/paper/trades/{a['id']}").status_code == 409


def test_account_starting_cash(setup):
    client, _ = setup
    assert client.put("/paper/account", json={"starting_cash": 25000}).status_code == 200
    assert client.get("/paper").json()["cash"] == 25000
    buy(client, 10)
    locked = client.put("/paper/account", json={"starting_cash": 1})
    assert locked.status_code == 409 and "no trades" in locked.json()["detail"]
    assert client.put("/paper/account", json={"starting_cash": 0}).status_code == 422


def test_portfolio_summary_via_api(setup):
    client, _ = setup
    assert client.get("/paper").json()["holdings"] == []
    buy(client, 100, "2026-01-05")
    s = client.get("/paper").json()
    assert s["cash"] == 99000 and s["total_value"] == 99000 + 1900 and s["trade_count"] == 1
    assert s["holdings"][0]["ticker"] == "AAA" and s["holdings"][0]["unrealized_pnl"] == 900


def test_trade_list_newest_first(setup):
    client, _ = setup
    buy(client, 1, "2026-01-05")
    buy(client, 2, "2026-01-07")
    trades = client.get("/paper/trades").json()["trades"]
    assert [t["shares"] for t in trades] == [2, 1]
    assert client.get("/paper/trades?ticker=ZZZ").json()["count"] == 0


def test_summary_degrades_when_prices_fail(setup):
    client, stub = setup
    buy(client, 100, "2026-01-05")
    stub.errors["AAA"] = ProviderError("yahoo down")
    s = client.get("/paper").json()
    assert s["incomplete"] and s["total_value"] is None and s["cash"] == 99000


def test_performance_via_api(setup):
    client, stub = setup
    assert client.get("/paper/performance").json()["empty"] is True
    buy(client, 100, "2026-01-05")
    p = client.get("/paper/performance").json()
    assert p["start_value"] == 100000 and p["end_value"] == 100900
    assert p["total_return"] == 0.009 and p["benchmark_return"] == 0.0
    stub.errors["AAA"] = ProviderError("yahoo down")
    assert client.get("/paper/performance").status_code == 502  # can't value a holding: no guessing


def test_performance_without_benchmark_prices(setup):
    client, stub = setup
    buy(client, 100, "2026-01-05")
    stub.errors["SPY"] = ProviderError("nope")
    p = client.get("/paper/performance").json()
    assert p["benchmark_return"] is None and p["total_return"] == 0.009


def test_split_handled_end_to_end(tmp_path):
    database = Database(tmp_path / "split.db")
    adjusted = frame([25, 25, 25, 26, 27, 28, 29, 30, 31, 32], splits={"2026-01-08": 4.0})
    stub = StubPrices({"AAA": adjusted, "SPY": frame([100] * 10)})
    app.dependency_overrides[get_database] = lambda: database
    app.dependency_overrides[get_price_service] = lambda: stub
    try:
        client = TestClient(app)
        t = buy(client, 10, "2026-01-06").json()
        assert t["price"] == 100.0  # the real quote before the 4:1 split, not the adjusted 25
        h = client.get("/paper").json()["holdings"][0]
        assert (h["shares"], h["avg_cost"], h["cost_basis"]) == (40, 25, 1000)
        assert (h["price"], h["market_value"], h["unrealized_pnl"]) == (32, 1280, 280)
        assert buy(client, 41, "2026-01-13", side="sell").status_code == 409
        assert buy(client, 40, "2026-01-13", side="sell").status_code == 201
        perf = client.get("/paper/performance").json()
        assert perf["end_value"] == 100000 - 1000 + 40 * 29  # sold 40 at 29 after the split
    finally:
        app.dependency_overrides.clear()
