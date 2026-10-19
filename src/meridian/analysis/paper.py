"""Paper portfolio maths. Pure functions over a ledger of trades and price frames.

Conventions
- A trade stores the price you would actually have paid on its fill date and the share
  count as it was then. Yahoo closes are split-adjusted to today, so we convert:
  actual price = adjusted close x (product of splits after the fill date).
- Everything computed later is in today's share basis: a trade's shares are multiplied
  by the splits that happened after it. Cost in dollars never changes.
- Realized profit uses average cost. No commissions, slippage, taxes or dividends.
"""

from dataclasses import dataclass
from datetime import date

import pandas as pd

from meridian.analysis.valuation import split_factor_after
from meridian.data.prices import SPLIT_COLUMN

BENCHMARK = "SPY"
EPS_SHARES = 1e-9
EPS_CASH = 1e-6
STALE_PRICE_DAYS = 5
_NO_SPLITS = pd.Series(dtype=float)


class LedgerError(ValueError):
    """The ledger would break a rule: negative cash or selling shares you don't hold."""


@dataclass(frozen=True)
class Trade:
    id: int
    ticker: str
    side: str  # "buy" | "sell"
    shares: float  # as of the fill date
    price: float  # actual price on the fill date
    trade_date: date

    @classmethod
    def from_row(cls, row: dict) -> "Trade":
        return cls(
            row["id"], row["ticker"], row["side"], row["shares"], row["price"],
            date.fromisoformat(row["trade_date"]),
        )

    @property
    def amount(self) -> float:
        return self.shares * self.price


@dataclass
class Position:
    ticker: str
    shares: float = 0.0  # today's share basis
    cost: float = 0.0  # dollars paid for the shares still held
    realized: float = 0.0


def splits_of(prices: pd.DataFrame | None) -> pd.Series:
    if prices is None or prices.empty or SPLIT_COLUMN not in prices.columns:
        return _NO_SPLITS
    s = prices[SPLIT_COLUMN]
    return s[s > 0]


def ordered(trades: list[Trade]) -> list[Trade]:
    return sorted(trades, key=lambda t: (t.trade_date, t.id))


def actual_price(adjusted_close: float, splits: pd.Series, fill_date: date) -> float:
    """The price quoted on fill_date, before later splits were applied to history."""
    return round(adjusted_close * split_factor_after(splits, fill_date), 4)


def check_cash(trades: list[Trade], starting_cash: float) -> None:
    cash = starting_cash
    for t in ordered(trades):
        cash += -t.amount if t.side == "buy" else t.amount
        if cash < -EPS_CASH:
            raise LedgerError(
                f"Not enough cash for the {t.side} of {t.shares:g} {t.ticker} on {t.trade_date}: "
                f"it would leave cash ${cash:,.2f} below zero."
            )


def check_shares(trades: list[Trade], splits: pd.Series) -> None:
    """Trades of one ticker: never sell more than you hold (no shorting)."""
    held = 0.0  # today's basis
    for t in ordered(trades):
        factor = split_factor_after(splits, t.trade_date)
        n = t.shares * factor
        if t.side == "buy":
            held += n
        else:
            if n > held + EPS_SHARES:
                raise LedgerError(
                    f"Can't sell {t.shares:g} {t.ticker} on {t.trade_date}: "
                    f"you would only hold {max(held, 0.0) / factor:g} then."
                )
            held -= n


def replay(
    trades: list[Trade], starting_cash: float, splits_by_ticker: dict[str, pd.Series]
) -> tuple[float, dict[str, Position]]:
    cash = starting_cash
    positions: dict[str, Position] = {}
    for t in ordered(trades):
        p = positions.setdefault(t.ticker, Position(t.ticker))
        n = t.shares * split_factor_after(splits_by_ticker.get(t.ticker, _NO_SPLITS), t.trade_date)
        if t.side == "buy":
            p.shares += n
            p.cost += t.amount
            cash -= t.amount
        else:
            avg = p.cost / p.shares if p.shares > EPS_SHARES else 0.0
            p.realized += t.amount - avg * n
            p.cost -= avg * n
            p.shares -= n
            cash += t.amount
            if p.shares < EPS_SHARES:
                p.shares, p.cost = 0.0, 0.0
    return cash, positions


def _r(x: float | None, digits: int = 2) -> float | None:
    return None if x is None else round(float(x), digits)


NOTES = [
    "Trades fill at the closing price of the trading day, with no commissions, slippage or taxes.",
    "Prices exclude dividends, so returns understate companies that pay them.",
]


def summary(
    trades: list[Trade],
    starting_cash: float,
    frames: dict[str, pd.DataFrame],
    as_of: date,
) -> dict:
    """Cash, holdings and profit. `frames` holds price history per ticker; a ticker
    without one is listed without a market value and the totals are marked incomplete."""
    splits = {t: splits_of(f) for t, f in frames.items()}
    cash, positions = replay(trades, starting_cash, splits)

    holdings, market_total, unrealized, incomplete, stale = [], 0.0, 0.0, False, False
    for p in sorted(positions.values(), key=lambda p: p.ticker):
        if p.shares <= 0:
            continue
        frame = frames.get(p.ticker)
        row = {
            "ticker": p.ticker,
            "shares": _r(p.shares, 6),
            "avg_cost": _r(p.cost / p.shares, 4),
            "cost_basis": _r(p.cost),
            "price": None, "price_date": None, "market_value": None,
            "unrealized_pnl": None, "unrealized_pct": None, "weight": None,
        }
        if frame is None or frame.empty:
            incomplete = True
        else:
            price = float(frame["close"].iloc[-1])
            price_date = frame.index[-1].date()
            value = p.shares * price
            market_total += value
            unrealized += value - p.cost
            stale = stale or (as_of - price_date).days > STALE_PRICE_DAYS
            row.update(
                price=_r(price, 4), price_date=price_date.isoformat(), market_value=_r(value),
                unrealized_pnl=_r(value - p.cost),
                unrealized_pct=_r((value - p.cost) / p.cost, 4) if p.cost > 0 else None,
            )
        holdings.append(row)

    total = cash + market_total
    for row in holdings:
        if row["market_value"] is not None and not incomplete and total > 0:
            row["weight"] = _r(row["market_value"] / total, 4)

    notes = list(NOTES)
    if incomplete:
        notes.append("Some prices could not be loaded, so total value and return are not shown.")
    if stale:
        notes.append(f"Some prices are more than {STALE_PRICE_DAYS} days old.")
    return {
        "starting_cash": _r(starting_cash),
        "cash": _r(cash),
        "positions_value": None if incomplete else _r(market_total),
        "total_value": None if incomplete else _r(total),
        "total_return": None if incomplete else _r(total / starting_cash - 1, 4),
        "realized_pnl": _r(sum(p.realized for p in positions.values())),
        "unrealized_pnl": None if incomplete else _r(unrealized),
        "holdings": holdings,
        "incomplete": incomplete,
        "notes": notes,
    }


def performance(
    trades: list[Trade],
    starting_cash: float,
    frames: dict[str, pd.DataFrame],
    benchmark: pd.DataFrame | None,
) -> dict:
    """Daily portfolio value from the first trade, against the benchmark buy-and-held
    with the same starting cash. Every ticker traded must have a price frame."""
    if not trades:
        return {"empty": True, "notes": ["No trades yet."] + NOTES}

    ts = ordered(trades)
    first = pd.Timestamp(ts[0].trade_date)
    splits = {t: splits_of(f) for t, f in frames.items()}

    pieces = [f.index for f in frames.values()]
    if benchmark is not None and not benchmark.empty:
        pieces.append(benchmark.index)
    full = pieces[0]
    for ix in pieces[1:]:
        full = full.union(ix)
    idx = full[full >= first]

    def aligned(series: pd.Series) -> pd.Series:
        return series.reindex(full).ffill().loc[idx]

    cash = pd.Series(float(starting_cash), index=idx)
    held = {t: pd.Series(0.0, index=idx) for t in {t.ticker for t in ts}}
    for t in ts:
        mask = idx >= pd.Timestamp(t.trade_date)
        sign = 1.0 if t.side == "buy" else -1.0
        cash.loc[mask] -= sign * t.amount
        held[t.ticker].loc[mask] += sign * t.shares * split_factor_after(
            splits.get(t.ticker, _NO_SPLITS), t.trade_date
        )
    value = cash.copy()
    for ticker, shares in held.items():
        value += shares * aligned(frames[ticker]["close"]).fillna(0.0)

    bench = None
    notes = list(NOTES)
    if benchmark is not None and not benchmark.empty:
        b = aligned(benchmark["close"])
        if pd.notna(b.iloc[0]) and b.iloc[0] > 0:
            bench = starting_cash * b / b.iloc[0]
    if bench is None:
        notes.append(f"{BENCHMARK} prices were unavailable, so there is no benchmark.")
    else:
        notes.append(
            f"{BENCHMARK} is bought with the same starting cash on the first trade date and held; "
            "your portfolio may hold cash, so the comparison is not like for like."
        )

    end_value = float(value.iloc[-1])
    port_ret = end_value / starting_cash - 1
    bench_ret = float(bench.iloc[-1] / starting_cash - 1) if bench is not None else None
    return {
        "empty": False,
        "benchmark_ticker": BENCHMARK if bench is not None else None,
        "first_date": idx[0].date().isoformat(),
        "last_date": idx[-1].date().isoformat(),
        "start_value": _r(starting_cash),
        "end_value": _r(end_value),
        "total_return": _r(port_ret, 4),
        "benchmark_return": _r(bench_ret, 4),
        "excess_return": _r(port_ret - bench_ret, 4) if bench_ret is not None else None,
        "series": {
            "dates": [d.date().isoformat() for d in idx],
            "portfolio": [_r(v) for v in value],
            "benchmark": [_r(v) for v in bench] if bench is not None else None,
        },
        "notes": notes,
    }
