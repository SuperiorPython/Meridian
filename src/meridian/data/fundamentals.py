"""Extract annual fundamentals from SEC XBRL company-facts JSON.

Design notes
- Periods are identified by their END date. The SEC `fy`/`fp` fields describe the
  filing, not the period a value covers, so they are not used.
- `value`/`filed` are the figure as FIRST reported and the date it became public.
  Backtests must only use a value on or after its `filed` date (no lookahead).
- `latest_value`/`latest_filed` reflect the most recent filing of the same tag, which
  may include restatements. Use these for display, not for backtests.
- Several XBRL tags can express one metric (and companies switch tags over time), so
  all candidate tags are merged per period.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# metric -> (kind, candidate us-gaap tags in priority order)
METRICS: dict[str, tuple[str, tuple[str, ...]]] = {
    "revenue": (
        "duration",
        (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
        ),
    ),
    "net_income": ("duration", ("NetIncomeLoss", "ProfitLoss")),
    "operating_income": ("duration", ("OperatingIncomeLoss",)),
    "operating_cash_flow": (
        "duration",
        ("NetCashProvidedByUsedInOperatingActivities",),
    ),
    "capex": (
        "duration",
        ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
    ),
    "total_assets": ("instant", ("Assets",)),
    "total_liabilities": ("instant", ("Liabilities",)),
    "stockholders_equity": (
        "instant",
        (
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        ),
    ),
}

MIN_ANNUAL_DAYS = 350  # 52/53-week fiscal years are 364 or 371 days
MAX_ANNUAL_DAYS = 380


@dataclass(frozen=True)
class AnnualFact:
    metric: str
    period_end: date
    period_start: date | None
    value: float
    filed: date
    form: str
    accession: str
    tag: str
    latest_value: float
    latest_filed: date


def _candidates(concept: dict, kind: str):
    """Yield raw USD fact entries from 10-K filings that match the metric kind."""
    for entry in concept.get("units", {}).get("USD", []):
        if not str(entry.get("form", "")).startswith("10-K"):
            continue
        if not all(k in entry for k in ("end", "val", "filed")):
            continue
        start = entry.get("start")
        if kind == "duration":
            if not start:
                continue
            days = (date.fromisoformat(entry["end"]) - date.fromisoformat(start)).days
            if not MIN_ANNUAL_DAYS <= days <= MAX_ANNUAL_DAYS:
                continue
        elif start:
            continue
        yield entry


def extract_annual(company_facts: dict) -> dict[str, list[AnnualFact]]:
    """Return {metric: [AnnualFact, ...]} sorted by period end. Missing metrics -> []."""
    us_gaap = company_facts.get("facts", {}).get("us-gaap", {})

    # Pass 1: gather candidates per metric as (end, tag_index, tag, entry).
    gathered: dict[str, list[tuple[str, int, str, dict]]] = {}
    for metric, (kind, tags) in METRICS.items():
        rows = []
        for idx, tag in enumerate(tags):
            for entry in _candidates(us_gaap.get(tag, {}), kind):
                rows.append((entry["end"], idx, tag, entry))
        gathered[metric] = rows

    # Fiscal-year ends come from annual duration facts; balance-sheet (instant)
    # values are only kept at those dates.
    fy_ends = {
        end
        for metric, (kind, _) in METRICS.items()
        if kind == "duration"
        for end, _, _, _ in gathered[metric]
    }

    result: dict[str, list[AnnualFact]] = {}
    for metric, (kind, _) in METRICS.items():
        by_end: dict[str, list[tuple[int, str, dict]]] = {}
        for end, idx, tag, entry in gathered[metric]:
            if kind == "instant" and fy_ends and end not in fy_ends:
                continue
            by_end.setdefault(end, []).append((idx, tag, entry))

        facts = []
        for end, cands in by_end.items():
            # First reported: earliest filing; ties broken by tag priority.
            idx, tag, first = min(
                cands, key=lambda c: (c[2]["filed"], c[0], c[2].get("accn", ""))
            )
            same_tag = [c[2] for c in cands if c[1] == tag]
            latest = max(same_tag, key=lambda e: e["filed"])
            start = first.get("start")
            facts.append(
                AnnualFact(
                    metric=metric,
                    period_end=date.fromisoformat(end),
                    period_start=date.fromisoformat(start) if start else None,
                    value=float(first["val"]),
                    filed=date.fromisoformat(first["filed"]),
                    form=str(first.get("form", "")),
                    accession=str(first.get("accn", "")),
                    tag=tag,
                    latest_value=float(latest["val"]),
                    latest_filed=date.fromisoformat(latest["filed"]),
                )
            )
        result[metric] = sorted(facts, key=lambda f: f.period_end)
    return result
