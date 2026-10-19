"""Plain-English company report built from transparent, rule-based signals.

Principles
- No single overall score and no buy/sell verdict. Four category ratings, each the
  average of visible signals; every signal shows its value, its rule and its points.
- A category needs at least MIN_SIGNALS scored signals, otherwise it is "Unavailable"
  rather than a rating built on one data point.
- The thresholds are rules of thumb that ignore industry norms (a P/E above 25 flags
  most high-growth companies; banks are scored differently). They are exposed in each
  signal's `rule` text so they can be judged, and changed, rather than trusted.
- Data-quality notes sit next to the numbers they affect.
"""
from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass
from datetime import date
from typing import Sequence

import pandas as pd

from meridian.analysis.metrics import PeriodMetrics, compute_metrics, is_financial_sic, ttm_summary
from meridian.analysis.valuation import compute_valuation, current_valuation
from meridian.data.fundamentals import (
    AnnualFact,
    extract_annual,
    extract_cover_shares,
    recent_periods,
)
from meridian.data.ttm import extract_ttm

STRONG_CUTOFF = 67
MODERATE_CUTOFF = 34
MIN_SIGNALS = 2

CATEGORY_TITLES = {
    "growth": "Growth",
    "profitability": "Profitability",
    "financial_health": "Financial health",
    "valuation": "Valuation",
}
CATEGORY_LABELS = {  # (high, middle, low); for valuation, high score = less expensive
    "growth": ("Strong", "Moderate", "Weak"),
    "profitability": ("Strong", "Moderate", "Weak"),
    "financial_health": ("Strong", "Moderate", "Weak"),
    "valuation": ("Inexpensive", "Moderate", "Elevated"),
}
METRIC_NAMES = {
    "revenue": "revenue",
    "net_income": "net income",
    "operating_income": "operating income",
    "operating_cash_flow": "operating cash flow",
    "capex": "capital expenditures",
    "total_assets": "total assets",
    "total_liabilities": "total liabilities",
    "stockholders_equity": "stockholders' equity",
}
CASH_FLOW_METRICS = {"operating_income", "operating_cash_flow", "capex"}
DISCLAIMER = (
    "This summarizes reported financials and the current price. It is not a forecast, "
    "and it is not investment advice."
)


@dataclass
class Signal:
    id: str
    label: str
    value: float | None
    points: float | None  # 0, 0.5 or 1; None = shown but not scored
    finding: str
    rule: str

    def to_dict(self) -> dict:
        return asdict(self)


def _tier(value: float, good: float, ok: float) -> float:
    """Higher is better."""
    return 1.0 if value >= good else 0.5 if value >= ok else 0.0


def _tier_low(value: float, good: float, ok: float) -> float:
    """Lower is better."""
    return 1.0 if value <= good else 0.5 if value <= ok else 0.0


def _pct(x: float, digits: int = 1) -> str:
    return f"{x * 100:.{digits}f}%"


def score_signals(signals: Sequence[Signal]) -> int | None:
    pts = [s.points for s in signals if s.points is not None]
    if len(pts) < MIN_SIGNALS:
        return None
    return int(100 * sum(pts) / len(pts) + 0.5)


def rating_for(category: str, score: int | None) -> str:
    if score is None:
        return "Unavailable"
    high, middle, low = CATEGORY_LABELS[category]
    return high if score >= STRONG_CUTOFF else middle if score >= MODERATE_CUTOFF else low


def _fact_at(annual: dict[str, list[AnnualFact]], metric: str, end: date) -> AnnualFact | None:
    return next((f for f in annual.get(metric, []) if f.period_end == end), None)


def _pick(ttm: dict | None, key: str, fy_value: float | None) -> tuple[float | None, str]:
    """Prefer the TTM figure when a TTM basis exists; else the latest fiscal year."""
    if ttm is not None and ttm.get("basis") == "ttm" and ttm.get(key) is not None:
        return ttm[key], "over the trailing twelve months"
    return fy_value, "in the latest fiscal year"


# -- signals -----------------------------------------------------------------


def growth_signals(pm: Sequence[PeriodMetrics]) -> list[Signal]:
    if not pm:
        return []
    last = pm[-1]
    sigs: list[Signal] = []

    g = last.revenue_growth
    if g is not None:
        verb = "grew" if g >= 0 else "fell"
        sigs.append(Signal(
            "revenue_growth", "Revenue growth, latest year", g, _tier(g, 0.10, 0.03),
            f"Revenue {verb} {_pct(abs(g))} in fiscal {last.period_end.year}, "
            "compared like-for-like.",
            "10% or more strong; 3-10% moderate; below 3% weak",
        ))

    obs = [m.revenue_growth for m in pm if m.revenue_growth is not None]
    recent = obs[-4:]
    if len(recent) >= 2:
        product = 1.0
        for x in recent:
            product *= 1 + x
        if product > 0:
            cagr = product ** (1 / len(recent)) - 1
            sigs.append(Signal(
                "revenue_cagr", "Revenue growth, multi-year", cagr, _tier(cagr, 0.10, 0.03),
                f"Revenue compounded at {_pct(cagr)} a year over the last "
                f"{len(recent)} years (like-for-like).",
                "10% a year or more strong; 3-10% moderate; below 3% weak",
            ))

    ng = last.net_income_growth
    if ng is not None:
        verb = "grew" if ng >= 0 else "fell"
        sigs.append(Signal(
            "net_income_growth", "Net income growth, latest year", ng, _tier(ng, 0.10, 0.0),
            f"Net income {verb} {_pct(abs(ng))} in fiscal {last.period_end.year}.",
            "10% or more strong; 0-10% moderate; decline weak",
        ))
    elif last.net_margin is not None:
        sigs.append(Signal(
            "net_income_growth", "Net income growth, latest year", None, None,
            "Net income growth is not meaningful: the prior-year base was zero, "
            "negative, or unavailable.",
            "not scored",
        ))

    last5 = obs[-5:]
    if len(last5) >= 3:
        k = sum(1 for x in last5 if x > 0)
        share = k / len(last5)
        sigs.append(Signal(
            "growth_consistency", "Growth consistency", share,
            1.0 if share == 1.0 else 0.5 if share >= 0.6 else 0.0,
            f"Revenue grew in {k} of the last {len(last5)} years.",
            "every year strong; at least 60% of years moderate; fewer weak",
        ))
    return sigs


def profitability_signals(pm: Sequence[PeriodMetrics], ttm: dict | None) -> list[Signal]:
    if not pm:
        return []
    last = pm[-1]
    sigs: list[Signal] = []

    nm, where = _pick(ttm, "net_margin", last.net_margin)
    if nm is not None:
        sigs.append(Signal(
            "net_margin", "Net margin", nm, _tier(nm, 0.15, 0.05),
            f"Net margin was {_pct(nm)} {where}.",
            "15% or more strong; 5-15% moderate; below 5% weak",
        ))

    om, where = _pick(ttm, "operating_margin", last.operating_margin)
    if om is not None:
        sigs.append(Signal(
            "operating_margin", "Operating margin", om, _tier(om, 0.15, 0.05),
            f"Operating margin was {_pct(om)} {where}.",
            "15% or more strong; 5-15% moderate; below 5% weak",
        ))

    if last.roe is not None:
        sigs.append(Signal(
            "roe", "Return on equity", last.roe, _tier(last.roe, 0.15, 0.08),
            f"Return on equity was {_pct(last.roe)} in fiscal {last.period_end.year} "
            "(on average equity).",
            "15% or more strong; 8-15% moderate; below 8% weak",
        ))

    if len(pm) >= 4 and pm[-1].net_margin is not None and pm[-4].net_margin is not None:
        now, then = pm[-1].net_margin, pm[-4].net_margin
        delta = now - then
        sigs.append(Signal(
            "margin_trend", "Net margin trend, three years", delta,
            1.0 if delta >= 0.01 else 0.5 if delta >= -0.01 else 0.0,
            f"Net margin moved from {_pct(then)} to {_pct(now)} over three years.",
            "up 1 point or more strong; within 1 point moderate; down more than 1 point weak",
        ))
    return sigs


def health_signals(
    pm: Sequence[PeriodMetrics],
    annual: dict[str, list[AnnualFact]],
    ttm: dict | None,
    is_financial: bool,
) -> list[Signal]:
    if not pm:
        return []
    last = pm[-1]
    ends = {m.period_end for m in pm}
    sigs: list[Signal] = []

    if is_financial:
        sigs.append(Signal(
            "financial_company", "Financial company", None, None,
            "Leverage and cash-flow signals are not comparable for banks and insurers, "
            "so they are not scored.",
            "not scored",
        ))
    else:
        lev = last.liabilities_to_equity
        if lev is not None:
            sigs.append(Signal(
                "leverage", "Liabilities to equity", lev, _tier_low(lev, 1.5, 3.0),
                f"Total liabilities were {lev:.1f}x equity in fiscal {last.period_end.year}.",
                "1.5x or less strong; 1.5-3x moderate; above 3x weak "
                "(total liabilities, not just interest-bearing debt)",
            ))
        else:
            sigs.append(Signal(
                "leverage", "Liabilities to equity", None, None,
                "Liabilities-to-equity is unavailable (liabilities missing or equity not positive).",
                "not scored",
            ))

        fm, where = _pick(ttm, "fcf_margin", last.fcf_margin)
        if fm is not None:
            sigs.append(Signal(
                "fcf_margin", "Free cash flow margin", fm, _tier(fm, 0.10, 0.0),
                f"Free cash flow was {_pct(fm)} of revenue {where}.",
                "10% or more strong; 0-10% moderate; negative weak "
                "(operating cash flow minus cash capex)",
            ))

        ni = _fact_at(annual, "net_income", last.period_end)
        ocf = _fact_at(annual, "operating_cash_flow", last.period_end)
        if ni is not None and ocf is not None and ni.value > 0:
            ratio = ocf.value / ni.value
            sigs.append(Signal(
                "cash_conversion", "Cash conversion", ratio, _tier(ratio, 1.0, 0.7),
                f"Operating cash flow was {ratio:.1f}x net income in fiscal "
                f"{last.period_end.year}.",
                "1.0x or more strong; 0.7-1.0x moderate; below 0.7x weak",
            ))

    ni_series = [f for f in annual.get("net_income", []) if f.period_end in ends]
    if len(ni_series) >= 3:
        pos = sum(1 for f in ni_series if f.value > 0)
        share = pos / len(ni_series)
        sigs.append(Signal(
            "earnings_stability", "Earnings stability", share,
            1.0 if share == 1.0 else 0.5 if share >= 0.6 else 0.0,
            f"Net income was positive in {pos} of the last {len(ni_series)} years.",
            "every year strong; at least 60% of years moderate; fewer weak",
        ))
    return sigs


def valuation_signals(
    current: dict | None, valuation_points: Sequence, is_financial: bool
) -> list[Signal]:
    if current is None:
        return []
    basis = "trailing twelve months" if current.get("basis") == "ttm" else "latest fiscal year"
    sigs: list[Signal] = []

    pe = current.get("pe")
    if pe is not None:
        sigs.append(Signal(
            "pe", "Price to earnings", pe, _tier_low(pe, 15, 25),
            f"P/E is {pe:.1f} on {basis} earnings.",
            "15 or below inexpensive; 15-25 moderate; above 25 elevated "
            "(ignores growth and industry norms)",
        ))
    else:
        sigs.append(Signal(
            "pe", "Price to earnings", None, None,
            f"No positive earnings over the {basis}, so P/E is not meaningful.",
            "not scored",
        ))

    fy = current.get("fcf_yield")
    if not is_financial and fy is not None:
        sigs.append(Signal(
            "fcf_yield", "Free cash flow yield", fy, _tier(fy, 0.05, 0.02),
            f"Free cash flow yield is {_pct(fy)} of market value.",
            "5% or more inexpensive; 2-5% moderate; below 2% elevated",
        ))

    history = [p.pe for p in valuation_points if getattr(p, "pe", None) is not None]
    if pe is not None and len(history) >= 3:
        median = statistics.median(history)
        ratio = pe / median
        sigs.append(Signal(
            "pe_vs_history", "P/E versus own history", ratio, _tier_low(ratio, 0.8, 1.2),
            f"Current P/E is {ratio * 100:.0f}% of its median ({median:.1f}) over "
            f"the last {len(history)} years.",
            "80% of median or below inexpensive; 80-120% moderate; above 120% elevated",
        ))
    return sigs


# -- data quality ------------------------------------------------------------


def data_quality(
    *,
    pm: Sequence[PeriodMetrics],
    annual: dict[str, list[AnnualFact]],
    val_points: Sequence,
    current: dict | None,
    ttm: dict | None,
    is_financial: bool,
    sic: int | None,
    as_of: date,
    price_error: str | None,
) -> list[dict]:
    notes: list[dict] = []

    def add(level: str, text: str) -> None:
        notes.append({"level": level, "text": text})

    ends = {m.period_end for m in pm}
    if len(pm) < 4:
        add("warn", f"Limited history: only {len(pm)} fiscal years of data, so trend "
                    "and consistency signals are thin or missing.")
    if sic is None:
        add("warn", "Sector unknown (SIC lookup failed): if this is a bank or insurer, "
                    "cash-flow signals will be misleading.")
    if is_financial:
        add("info", "Financial company: free cash flow and leverage are not scored.")

    missing = [
        name for key, name in METRIC_NAMES.items()
        if not any(f.period_end in ends for f in annual.get(key, []))
        and not (is_financial and key in CASH_FLOW_METRICS)
    ]
    if missing:
        add("info", "Not available in this company's SEC filings: "
                    + ", ".join(missing) + ". Signals that need them are skipped.")

    restated = sum(
        1 for key in ("revenue", "net_income")
        for f in annual.get(key, [])
        if f.period_end in ends and f.value and abs(f.latest_value - f.value) > 0.02 * abs(f.value)
    )
    if restated:
        add("info", f"{restated} revenue/net income figures were later restated by more than "
                    "2%; growth uses like-for-like figures from a single filing.")

    if price_error:
        add("warn", f"Price data unavailable ({price_error}), so valuation is not rated.")
    elif current is None:
        add("warn", "Valuation unavailable: no price history or no share count for this company.")
    else:
        if current.get("basis") == "ttm":
            add("info", f"Valuation uses trailing-twelve-month earnings through {current['through']}.")
        elif current.get("stale_days", 0) > 180:
            add("warn", f"Valuation uses annual earnings that are {current['stale_days']} days old; "
                        "a more recent quarter has likely been reported.")
        if current.get("shares_source") == "diluted_average":
            add("warn", "Share count is a weighted average, not the cover-page count; "
                        "market value is approximate.")
        age = (as_of - date.fromisoformat(current["price_date"])).days
        if age > 7:
            add("warn", f"Latest price is {age} days old (as of {current['price_date']}).")
        else:
            add("info", f"Price as of {current['price_date']}.")

    if not is_financial:
        add("info", "Free cash flow is operating cash flow minus cash capital spending; it "
                    "excludes assets acquired through finance leases.")
    add("info", "liabilities-to-equity uses total liabilities, not interest-bearing debt.")

    notes.sort(key=lambda n: 0 if n["level"] == "warn" else 1)
    return notes


# -- assembly ----------------------------------------------------------------


def _summary(name: str, ticker: str, cats: dict) -> str:
    parts = []
    for key in CATEGORY_TITLES:
        rating = cats[key]["rating"]
        parts.append(f"{CATEGORY_TITLES[key].lower()} "
                     + ("not rated (insufficient data)" if rating == "Unavailable" else rating.lower()))
    text = f"{name} ({ticker}): " + ", ".join(parts) + "."
    rated = {k: cats[k]["score"] for k in ("growth", "profitability", "financial_health")
             if cats[k]["score"] is not None}
    if len(rated) >= 2 and max(rated.values()) != min(rated.values()):
        tops = [k for k, v in rated.items() if v == max(rated.values())]
        bottoms = [k for k, v in rated.items() if v == min(rated.values())]
        bits = []
        if len(tops) == 1:  # ties are not called
            bits.append(f"strongest area: {CATEGORY_TITLES[tops[0]].lower()}")
        if len(bottoms) == 1:
            bits.append(f"weakest: {CATEGORY_TITLES[bottoms[0]].lower()}")
        if bits:
            text += " " + "; ".join(bits).capitalize() + "."
    return text + " " + DISCLAIMER


def build_report(
    *,
    ticker: str,
    entity_name: str | None,
    cik: int | None,
    sic: int | None,
    sic_description: str | None,
    is_financial: bool,
    annual: dict[str, list[AnnualFact]],
    period_metrics: Sequence[PeriodMetrics],
    valuation_points: Sequence,
    current: dict | None,
    ttm: dict | None,
    as_of: date,
    price_error: str | None = None,
) -> dict:
    signals = {
        "growth": growth_signals(period_metrics),
        "profitability": profitability_signals(period_metrics, ttm),
        "financial_health": health_signals(period_metrics, annual, ttm, is_financial),
        "valuation": valuation_signals(current, valuation_points, is_financial),
    }
    cats = {}
    for key, sigs in signals.items():
        score = score_signals(sigs)
        cats[key] = {
            "title": CATEGORY_TITLES[key],
            "rating": rating_for(key, score),
            "score": score,
            "signals": [s.to_dict() for s in sigs],
        }

    last = period_metrics[-1] if period_metrics else None
    return {
        "ticker": ticker,
        "entity_name": entity_name,
        "cik": cik,
        "sic": sic,
        "sic_description": sic_description,
        "is_financial": is_financial,
        "as_of": as_of.isoformat(),
        "summary": _summary(entity_name or ticker, ticker, cats),
        "categories": cats,
        "key_figures": {
            "latest_fiscal_year_end": last.period_end.isoformat() if last else None,
            "latest_fiscal_year_available_from": last.available_from.isoformat() if last else None,
            "ttm_through": ttm["through"] if ttm and ttm.get("basis") == "ttm" else None,
            "price_date": current["price_date"] if current else None,
            "close": current["close"] if current else None,
            "market_cap": current["market_cap"] if current else None,
            "pe": current["pe"] if current else None,
            "fcf_yield": current["fcf_yield"] if current else None,
        },
        "data_quality": data_quality(
            pm=period_metrics, annual=annual, val_points=valuation_points,
            current=current, ttm=ttm, is_financial=is_financial, sic=sic,
            as_of=as_of, price_error=price_error,
        ),
        "method": {
            "scoring": (
                "Each category is the average of its signals (1 point strong, 0.5 moderate, "
                "0 weak) scaled to 0-100; 67+ is the top rating and 34+ the middle. A "
                "category with fewer than 2 scored signals is Unavailable. Thresholds are "
                "rules of thumb that ignore industry norms."
            ),
            "disclaimer": DISCLAIMER,
        },
    }


def report_from_facts(
    facts: dict,
    prices: pd.DataFrame,
    *,
    ticker: str,
    cik: int | None = None,
    sic: int | None = None,
    sic_description: str | None = None,
    years: int = 10,
    as_of: date | None = None,
    price_error: str | None = None,
) -> dict:
    """Everything between raw SEC facts + prices and the finished report."""
    as_of = as_of or date.today()
    financial = is_financial_sic(sic)
    annual = extract_annual(facts)
    window = recent_periods(annual, years)
    ttm_snapshot = extract_ttm(facts, annual)

    period_metrics = [m for m in compute_metrics(annual, financial) if m.period_end in window]
    val_points = [
        p for p in compute_valuation(annual, extract_cover_shares(facts), prices, financial)
        if p.period_end in window
    ]
    current = current_valuation(
        annual, extract_cover_shares(facts, forms=("10-K", "10-Q")), prices,
        financial, ttm_snapshot,
    )
    return build_report(
        ticker=ticker,
        entity_name=facts.get("entityName"),
        cik=cik,
        sic=sic,
        sic_description=sic_description,
        is_financial=financial,
        annual=annual,
        period_metrics=period_metrics,
        valuation_points=val_points,
        current=current,
        ttm=ttm_summary(ttm_snapshot, financial),
        as_of=as_of,
        price_error=price_error,
    )
