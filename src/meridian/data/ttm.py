"""Trailing-twelve-month (TTM) figures from the latest 10-K plus 10-Q year-to-date facts.

    TTM = last fiscal year + current year-to-date - prior-year year-to-date

Design notes
- Cash-flow statements in 10-Qs are cumulative (6 and 9 months), so we always work with
  year-to-date values, never with single quarters.
- The prior-year year-to-date value is taken from the SAME 10-Q (its comparative column),
  so restatements cannot mix accounting bases.
- Fiscal-year start is inferred from the latest 10-K's period end; year-to-date facts
  must start within a few days after it. 52/53-week calendars are handled with a
  tolerance on both the start and the prior-year dates.
- If no 10-Q newer than the latest 10-K exists, the snapshot falls back to annual
  figures (`basis == "annual"`).
- A metric missing any component is omitted rather than guessed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from meridian.data.fundamentals import METRICS, AnnualFact

TTM_METRICS = ("revenue", "net_income", "operating_income", "operating_cash_flow", "capex")
START_TOLERANCE_DAYS = 10  # YTD start vs the day after fiscal-year end
PRIOR_TOLERANCE_DAYS = 10  # prior-year YTD dates vs 365 days earlier
MIN_YTD_DAYS = 60
MAX_YTD_DAYS = 290


@dataclass(frozen=True)
class TTMSnapshot:
    basis: str  # "ttm" or "annual"
    fy_end: date  # latest 10-K period end
    ytd_end: date | None  # latest 10-Q period end (None when basis == "annual")
    available_from: date  # filing date by which every component was public
    values: dict[str, float] = field(default_factory=dict)
    # metric -> (fiscal year, current YTD, prior YTD); only for basis == "ttm"
    parts: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    equity: float | None = None  # balance-sheet equity at ytd_end from the 10-Q


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _quarterly_entries(concept: dict, unit: str = "USD"):
    for e in concept.get("units", {}).get(unit, []):
        if not str(e.get("form", "")).startswith("10-Q"):
            continue
        if not all(k in e for k in ("end", "val", "filed", "accn")):
            continue
        yield e


def extract_ttm(company_facts: dict, annual: dict[str, list[AnnualFact]]) -> TTMSnapshot | None:
    core = annual.get("net_income") or annual.get("revenue")
    if not core:
        return None
    anchor = core[-1]
    fy_end = anchor.period_end
    us_gaap = company_facts.get("facts", {}).get("us-gaap", {})

    fy_values: dict[str, float] = {}
    for m in TTM_METRICS:
        fact = next((f for f in annual.get(m, []) if f.period_end == fy_end), None)
        if fact is not None:
            fy_values[m] = fact.latest_value

    def annual_snapshot() -> TTMSnapshot:
        return TTMSnapshot("annual", fy_end, None, anchor.filed, dict(fy_values))

    # Year-to-date entries starting just after the fiscal year end, per metric.
    ytd: dict[str, list[tuple[int, dict]]] = {m: [] for m in TTM_METRICS}
    all_entries: dict[str, list[tuple[int, dict]]] = {m: [] for m in TTM_METRICS}
    for m in TTM_METRICS:
        for idx, tag in enumerate(METRICS[m].tags):
            for e in _quarterly_entries(us_gaap.get(tag, {})):
                if not e.get("start"):
                    continue
                all_entries[m].append((idx, e))
                days = (_d(e["end"]) - _d(e["start"])).days
                gap = (_d(e["start"]) - fy_end).days
                if 0 <= gap <= START_TOLERANCE_DAYS and MIN_YTD_DAYS <= days <= MAX_YTD_DAYS:
                    ytd[m].append((idx, e))

    core_metric = "net_income" if "net_income" in fy_values else "revenue"
    core_ends = [e["end"] for _, e in ytd.get(core_metric, [])]
    if not core_ends:
        return annual_snapshot()
    target_end = max(core_ends)

    values: dict[str, float] = {}
    parts: dict[str, tuple[float, float, float]] = {}
    filed_dates = [anchor.filed]
    for m in TTM_METRICS:
        if m not in fy_values:
            continue
        cur_candidates = [(i, e) for i, e in ytd[m] if e["end"] == target_end]
        if not cur_candidates:
            continue
        idx, cur = min(cur_candidates, key=lambda c: (c[1]["filed"], c[0]))
        cur_days = (_d(cur["end"]) - _d(cur["start"])).days
        prior_candidates = []
        for i, e in all_entries[m]:
            if e["accn"] != cur["accn"]:
                continue  # same filing only: its prior-year comparative column
            start_gap = (_d(cur["start"]) - _d(e["start"])).days
            end_gap = (_d(cur["end"]) - _d(e["end"])).days
            dur = (_d(e["end"]) - _d(e["start"])).days
            if (
                abs(start_gap - 365) <= PRIOR_TOLERANCE_DAYS
                and abs(end_gap - 365) <= PRIOR_TOLERANCE_DAYS
                and abs(dur - cur_days) <= PRIOR_TOLERANCE_DAYS
            ):
                prior_candidates.append((i, e))
        if not prior_candidates:
            continue
        _, prior = min(prior_candidates, key=lambda c: c[0])
        values[m] = fy_values[m] + float(cur["val"]) - float(prior["val"])
        parts[m] = (fy_values[m], float(cur["val"]), float(prior["val"]))
        filed_dates.append(_d(cur["filed"]))

    if core_metric not in values:
        return annual_snapshot()

    # Latest balance-sheet equity from the same quarter.
    equity = None
    best = None
    for idx, tag in enumerate(METRICS["stockholders_equity"].tags):
        for e in _quarterly_entries(us_gaap.get(tag, {})):
            if e.get("start") or e["end"] != target_end:
                continue
            key = (idx, e["filed"])
            if best is None or key < best:
                best, equity = key, float(e["val"])

    return TTMSnapshot(
        basis="ttm",
        fy_end=fy_end,
        ytd_end=_d(target_end),
        available_from=max(filed_dates),
        values=values,
        parts=parts,
        equity=equity,
    )
