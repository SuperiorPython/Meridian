"""Point-in-time valuation: market cap, P/E, P/S, P/B, FCF yield.

Why this is subtle
- Yahoo's `close` is adjusted for stock splits as of TODAY; share counts in a 10-K are
  as reported at the time. Multiplying them directly is wrong for any company that
  split since (Apple's 2019 cover shares x today-basis price understates by 4x).
  We convert the reported share count to today's split basis by multiplying by every
  split whose ex-date is AFTER the share count's as-of date. Splits on or before that
  date are already reflected in the count.
- Never use `adj_close` here: it is also dividend-adjusted, which distorts market cap.
- Each fiscal period is priced on the first trading day on or after its 10-K filing
  date, so a backtest never sees a price from before the numbers were public.
- Per-period ratios use that fiscal year's annual figures. The `current` block uses
  trailing-twelve-month figures when a 10-Q newer than the latest 10-K exists
  (`basis == "ttm"`), otherwise annual; the annual P/E is kept alongside for comparison.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

import pandas as pd

from meridian.data.fundamentals import AnnualFact, CoverShares
from meridian.data.ttm import TTMSnapshot

MAX_PRICE_GAP_DAYS = 7  # price must exist within a week of the filing date


@dataclass(frozen=True)
class ValuationPoint:
    period_end: date
    price_date: date | None
    close: float | None  # Yahoo basis: split-adjusted to today
    price_then: float | None  # actual closing price on price_date
    shares_source: str | None  # "cover" or "diluted_average"
    shares_reported: float | None
    shares_as_of: date | None
    split_factor: float | None  # product of splits after shares_as_of
    shares_adjusted: float | None  # reported shares on today's split basis
    market_cap: float | None
    pe: float | None
    ps: float | None
    pb: float | None
    fcf_yield: float | None

    def to_dict(self) -> dict:
        d = asdict(self)
        for key in ("period_end", "price_date", "shares_as_of"):
            if d[key] is not None:
                d[key] = d[key].isoformat()
        return d


def _splits(prices: pd.DataFrame) -> pd.Series:
    if "split" not in prices.columns:
        return pd.Series(dtype=float)
    s = prices["split"]
    return s[s > 0]


def split_factor_after(splits: pd.Series, d: date) -> float:
    """Product of split ratios with ex-date strictly after `d`."""
    later = splits[splits.index > pd.Timestamp(d)]
    return float(later.prod()) if len(later) else 1.0


def close_on_or_after(prices: pd.DataFrame, d: date) -> tuple[date, float] | None:
    """(date, close) of the first trading day on or after d, if within a week."""
    sub = prices.loc[prices.index >= pd.Timestamp(d)]
    if sub.empty:
        return None
    day = sub.index[0].date()
    if (day - d).days > MAX_PRICE_GAP_DAYS:
        return None
    return day, float(sub["close"].iloc[0])


def _ratios(
    market_cap: float | None,
    ni: float | None,
    rev: float | None,
    equity: float | None,
    ocf: float | None,
    capex: float | None,
    is_financial: bool,
) -> dict:
    out = {"pe": None, "ps": None, "pb": None, "fcf_yield": None}
    if market_cap is None or market_cap <= 0:
        return out
    if ni is not None and ni > 0:
        out["pe"] = market_cap / ni
    if rev is not None and rev > 0:
        out["ps"] = market_cap / rev
    if equity is not None and equity > 0:
        out["pb"] = market_cap / equity
    if not is_financial and ocf is not None and capex is not None:
        out["fcf_yield"] = (ocf - capex) / market_cap
    return out


def _v(fact: AnnualFact | None) -> float | None:
    return None if fact is None else fact.value


def _by_end(annual: dict[str, list[AnnualFact]]) -> dict[str, dict[date, AnnualFact]]:
    return {m: {f.period_end: f for f in series} for m, series in annual.items()}


def compute_valuation(
    annual: dict[str, list[AnnualFact]],
    cover_shares: list[CoverShares],
    prices: pd.DataFrame,
    is_financial: bool = False,
) -> list[ValuationPoint]:
    """One ValuationPoint per fiscal period, priced when its 10-K became public."""
    by_end = _by_end(annual)
    ends = sorted(set(by_end.get("net_income", {})) | set(by_end.get("revenue", {})))
    covers = {c.accession: c for c in cover_shares}
    have_prices = not prices.empty and "close" in prices.columns
    splits = _splits(prices) if have_prices else pd.Series(dtype=float)

    out: list[ValuationPoint] = []
    for end in ends:
        def at(metric: str) -> AnnualFact | None:
            return by_end.get(metric, {}).get(end)

        ni, rev = at("net_income"), at("revenue")
        anchor = ni or rev  # the annual report that first published this period
        assert anchor is not None

        # Shares: 10-K cover count if available, else weighted-average diluted.
        source = reported = as_of = None
        cover = covers.get(anchor.accession)
        diluted = at("diluted_shares")
        if cover is not None:
            source, reported, as_of = "cover", cover.value, cover.as_of
        elif diluted is not None:
            source, reported, as_of = "diluted_average", diluted.value, anchor.filed

        priced = close_on_or_after(prices, anchor.filed) if have_prices else None
        factor = shares_adj = market_cap = price_then = None
        price_date = close = None
        if priced is not None:
            price_date, close = priced
            price_then = close * split_factor_after(splits, price_date)
        if reported is not None:
            factor = split_factor_after(splits, as_of)
            shares_adj = reported * factor
            if close is not None:
                market_cap = shares_adj * close

        ratios = _ratios(
            market_cap, _v(ni), _v(rev), _v(at("stockholders_equity")),
            _v(at("operating_cash_flow")), _v(at("capex")), is_financial,
        )
        out.append(
            ValuationPoint(
                period_end=end,
                price_date=price_date,
                close=close,
                price_then=price_then,
                shares_source=source,
                shares_reported=reported,
                shares_as_of=as_of,
                split_factor=factor,
                shares_adjusted=shares_adj,
                market_cap=market_cap,
                **ratios,
            )
        )
    return out


def current_valuation(
    annual: dict[str, list[AnnualFact]],
    cover_shares: list[CoverShares],
    prices: pd.DataFrame,
    is_financial: bool = False,
    ttm: TTMSnapshot | None = None,
) -> dict | None:
    """Latest price x latest cover-page shares, against TTM (else annual) figures."""
    if prices.empty or "close" not in prices.columns:
        return None
    by_end = _by_end(annual)
    ends = sorted(set(by_end.get("net_income", {})) | set(by_end.get("revenue", {})))
    if not ends:
        return None
    end = ends[-1]

    def at(metric: str) -> AnnualFact | None:
        return by_end.get(metric, {}).get(end)

    ni, rev = at("net_income"), at("revenue")
    anchor = ni or rev
    assert anchor is not None

    if cover_shares:
        latest = max(cover_shares, key=lambda c: (c.filed, c.accession))
        source, reported, as_of = "cover", latest.value, latest.as_of
    elif at("diluted_shares") is not None:
        source, reported, as_of = "diluted_average", at("diluted_shares").value, anchor.filed
    else:
        return None

    splits = _splits(prices)
    last_day = prices.index[-1].date()
    close = float(prices["close"].iloc[-1])
    shares_adj = reported * split_factor_after(splits, as_of)
    market_cap = shares_adj * close

    annual_ratios = _ratios(
        market_cap, _v(ni), _v(rev), _v(at("stockholders_equity")),
        _v(at("operating_cash_flow")), _v(at("capex")), is_financial,
    )
    use_ttm = ttm is not None and ttm.basis == "ttm"
    if use_ttm:
        v = ttm.values
        equity = ttm.equity if ttm.equity is not None else _v(at("stockholders_equity"))
        ratios = _ratios(
            market_cap, v.get("net_income"), v.get("revenue"), equity,
            v.get("operating_cash_flow"), v.get("capex"), is_financial,
        )
        through = ttm.ytd_end
    else:
        ratios = annual_ratios
        through = end

    return {
        "price_date": last_day.isoformat(),
        "close": close,
        "shares_source": source,
        "shares_as_of": as_of.isoformat(),
        "shares_adjusted": shares_adj,
        "market_cap": market_cap,
        "basis": "ttm" if use_ttm else "annual",
        "through": through.isoformat(),
        "latest_period_end": end.isoformat(),
        "stale_days": (last_day - through).days,
        **ratios,
        "annual_pe": annual_ratios["pe"],
    }
