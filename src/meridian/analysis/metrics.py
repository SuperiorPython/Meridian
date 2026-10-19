"""Derived per-period metrics from annual fundamentals.

Conventions
- Growth is LIKE-FOR-LIKE: both years come from the same filing (the current year's
  10-K, which restates its prior-year comparatives). Dividing a first-reported value
  by an earlier first-reported value would mix pre- and post-restatement bases (e.g.
  after a spin-off) and show growth or decline that never happened.
- Growth is None when the prior value is missing or <= 0 (percent change from a
  non-positive base is not meaningful), or when the prior fiscal year is not present.
- Ratios use values as first reported for the period; `available_from` is the date by
  which every figure for that period was public (no lookahead in backtests).
- ROE uses average equity (current and same-filing prior year) when the prior year is
  available, else ending equity. None when equity is not positive.
- Free cash flow = operating cash flow - cash capex. Cash capex excludes assets
  acquired through finance leases, so FCF is overstated for lease-heavy companies.
- Financial companies (banks, brokers, insurers): cash-flow metrics are not
  meaningful (trading assets run through operating cash flow), so FCF-based metrics
  are None.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

from meridian.data.fundamentals import MAX_ANNUAL_DAYS, MIN_ANNUAL_DAYS, AnnualFact

FINANCIAL_SIC_MIN = 6000  # finance, banking, brokerage, insurance
FINANCIAL_SIC_MAX = 6411


def is_financial_sic(sic: int | None) -> bool:
    return sic is not None and FINANCIAL_SIC_MIN <= sic <= FINANCIAL_SIC_MAX


@dataclass(frozen=True)
class PeriodMetrics:
    period_end: date
    available_from: date
    revenue_growth: float | None
    net_income_growth: float | None
    operating_income_growth: float | None
    fcf_growth: float | None
    operating_margin: float | None
    net_margin: float | None
    free_cash_flow: float | None
    fcf_margin: float | None
    roe: float | None
    liabilities_to_equity: float | None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["period_end"] = self.period_end.isoformat()
        d["available_from"] = self.available_from.isoformat()
        return d


def _prior_fact(series: list[AnnualFact], fact: AnnualFact) -> AnnualFact | None:
    """The fact for the immediately preceding fiscal year, if present."""
    for f in series:
        gap = (fact.period_end - f.period_end).days
        if MIN_ANNUAL_DAYS <= gap <= MAX_ANNUAL_DAYS:
            return f
    return None


def _prior_value(series: list[AnnualFact], fact: AnnualFact | None) -> float | None:
    """Prior-year value as restated in the SAME filing that first reported `fact`."""
    if fact is None:
        return None
    prior = _prior_fact(series, fact)
    return None if prior is None else prior.value_in(fact.accession)


def _growth(current: float | None, prior: float | None) -> float | None:
    if current is None or prior is None or prior <= 0:
        return None
    return current / prior - 1


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def compute_metrics(
    annual: dict[str, list[AnnualFact]], is_financial: bool = False
) -> list[PeriodMetrics]:
    """One PeriodMetrics per fiscal period end found in `annual`, oldest first."""
    series = {m: annual.get(m, []) for m in (
        "revenue", "net_income", "operating_income", "operating_cash_flow",
        "capex", "total_liabilities", "stockholders_equity",
    )}
    by_end = {m: {f.period_end: f for f in s} for m, s in annual.items()}
    ends = sorted({end for d in by_end.values() for end in d})

    out: list[PeriodMetrics] = []
    for end in ends:
        facts_here = [d[end] for d in by_end.values() if end in d]
        available_from = max(f.filed for f in facts_here)

        def at(metric: str) -> AnnualFact | None:
            return by_end.get(metric, {}).get(end)

        revenue, net_income, op_income = at("revenue"), at("net_income"), at("operating_income")
        ocf, capex = at("operating_cash_flow"), at("capex")
        liabilities, equity = at("total_liabilities"), at("stockholders_equity")

        def val(f: AnnualFact | None) -> float | None:
            return None if f is None else f.value

        # Free cash flow (current and same-filing prior year).
        fcf = fcf_prior = None
        if not is_financial and ocf is not None and capex is not None:
            fcf = ocf.value - capex.value
            ocf_prior = _prior_value(series["operating_cash_flow"], ocf)
            capex_prior = _prior_value(series["capex"], capex)
            if ocf_prior is not None and capex_prior is not None:
                fcf_prior = ocf_prior - capex_prior

        # Return on equity.
        roe = None
        if equity is not None and net_income is not None:
            prior_equity = _prior_value(series["stockholders_equity"], equity)
            avg_equity = (
                equity.value if prior_equity is None else (equity.value + prior_equity) / 2
            )
            if avg_equity > 0:
                roe = net_income.value / avg_equity

        leverage = None
        if equity is not None and liabilities is not None and equity.value > 0:
            leverage = liabilities.value / equity.value

        out.append(
            PeriodMetrics(
                period_end=end,
                available_from=available_from,
                revenue_growth=_growth(
                    val(revenue), _prior_value(series["revenue"], revenue)
                ),
                net_income_growth=_growth(
                    val(net_income), _prior_value(series["net_income"], net_income)
                ),
                operating_income_growth=_growth(
                    val(op_income), _prior_value(series["operating_income"], op_income)
                ),
                fcf_growth=_growth(fcf, fcf_prior),
                operating_margin=_ratio(val(op_income), val(revenue)),
                net_margin=_ratio(val(net_income), val(revenue)),
                free_cash_flow=fcf,
                fcf_margin=_ratio(fcf, val(revenue)),
                roe=roe,
                liabilities_to_equity=leverage,
            )
        )
    return out
