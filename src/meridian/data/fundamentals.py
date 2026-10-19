"""Extract annual fundamentals from SEC XBRL company-facts JSON.

Design notes
- Periods are identified by their END date. The SEC `fy`/`fp` fields describe the
  filing, not the period a value covers, so they are not used.
- `value`/`filed` are the figure as FIRST reported and the date it became public.
  Backtests must only use a value on or after its `filed` date (no lookahead).
- `latest_value`/`latest_filed` reflect the most recent filing of the same tag, which
  may include restatements. Use these for display, not for backtests.
- `versions` keeps the value from EVERY filing that reported the period. Each 10-K
  restates prior-year comparatives, so a like-for-like comparison of two years must
  take both values from the same filing (see `AnnualFact.value_in`).
- Several XBRL tags can express one metric (and companies switch tags over time), so
  all candidate tags are merged per period.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import NamedTuple


class MetricSpec(NamedTuple):
    kind: str  # "duration" (income/cash-flow) or "instant" (balance sheet)
    unit: str
    tags: tuple[str, ...]  # candidate us-gaap tags in priority order


METRICS: dict[str, MetricSpec] = {
    "revenue": MetricSpec(
        "duration",
        "USD",
        (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
        ),
    ),
    "net_income": MetricSpec("duration", "USD", ("NetIncomeLoss", "ProfitLoss")),
    "operating_income": MetricSpec("duration", "USD", ("OperatingIncomeLoss",)),
    "operating_cash_flow": MetricSpec(
        "duration", "USD", ("NetCashProvidedByUsedInOperatingActivities",)
    ),
    "capex": MetricSpec(
        "duration",
        "USD",
        ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
    ),
    "total_assets": MetricSpec("instant", "USD", ("Assets",)),
    "total_liabilities": MetricSpec("instant", "USD", ("Liabilities",)),
    "stockholders_equity": MetricSpec(
        "instant",
        "USD",
        (
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        ),
    ),
    "diluted_shares": MetricSpec(
        "duration", "shares", ("WeightedAverageNumberOfDilutedSharesOutstanding",)
    ),
}

MIN_ANNUAL_DAYS = 350  # 52/53-week fiscal years are 364 or 371 days
MAX_ANNUAL_DAYS = 380


@dataclass(frozen=True)
class Version:
    """One filing's report of a period's value."""

    accession: str
    filed: date
    value: float


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
    versions: tuple[Version, ...] = ()

    def value_in(self, accession: str) -> float | None:
        """This period's value as reported in a specific filing, if it appears there."""
        for v in self.versions:
            if v.accession == accession:
                return v.value
        return None


@dataclass(frozen=True)
class CoverShares:
    """Shares outstanding from a 10-K cover page (as of a date shortly before filing)."""

    as_of: date
    filed: date
    accession: str
    value: float


def _candidates(concept: dict, kind: str, unit: str):
    """Yield raw fact entries from 10-K filings that match the metric kind."""
    for entry in concept.get("units", {}).get(unit, []):
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
    for metric, spec in METRICS.items():
        rows = []
        for idx, tag in enumerate(spec.tags):
            for entry in _candidates(us_gaap.get(tag, {}), spec.kind, spec.unit):
                rows.append((entry["end"], idx, tag, entry))
        gathered[metric] = rows

    # Fiscal-year ends come from annual duration facts; balance-sheet (instant)
    # values are only kept at those dates.
    fy_ends = {
        end
        for metric, spec in METRICS.items()
        if spec.kind == "duration"
        for end, _, _, _ in gathered[metric]
    }

    result: dict[str, list[AnnualFact]] = {}
    for metric, spec in METRICS.items():
        by_end: dict[str, list[tuple[int, str, dict]]] = {}
        for end, idx, tag, entry in gathered[metric]:
            if spec.kind == "instant" and fy_ends and end not in fy_ends:
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

            # One version per filing (best-priority tag within a filing).
            best: dict[str, tuple[int, dict]] = {}
            for c_idx, _, entry in cands:
                accn = entry.get("accn")
                if accn and (accn not in best or c_idx < best[accn][0]):
                    best[accn] = (c_idx, entry)
            versions = tuple(
                sorted(
                    (
                        Version(a, date.fromisoformat(e["filed"]), float(e["val"]))
                        for a, (_, e) in best.items()
                    ),
                    key=lambda v: (v.filed, v.accession),
                )
            )

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
                    versions=versions,
                )
            )
        result[metric] = sorted(facts, key=lambda f: f.period_end)
    return result


def recent_periods(annual: dict[str, list[AnnualFact]], years: int) -> set[date]:
    """The most recent `years` distinct fiscal period ends across ALL metrics.

    Windowing across metrics (not per metric) keeps a metric a company stopped
    reporting long ago from returning stale periods.
    """
    ends = sorted({f.period_end for series in annual.values() for f in series})
    return set(ends[-years:])


def extract_cover_shares(
    company_facts: dict, forms: tuple[str, ...] = ("10-K",)
) -> list[CoverShares]:
    """Shares outstanding from cover pages (dei), oldest first. 10-K only by default;
    pass forms=("10-K", "10-Q") to include the more recent quarterly counts.

    Filings with several differing values (e.g. multiple share classes) are skipped
    rather than guessed at.
    """
    concept = (
        company_facts.get("facts", {})
        .get("dei", {})
        .get("EntityCommonStockSharesOutstanding", {})
    )
    by_accession: dict[str, list[dict]] = {}
    for entry in concept.get("units", {}).get("shares", []):
        if not str(entry.get("form", "")).startswith(forms):
            continue
        if not all(k in entry for k in ("end", "val", "filed", "accn")):
            continue
        by_accession.setdefault(entry["accn"], []).append(entry)

    out = []
    for accession, entries in by_accession.items():
        if len({e["val"] for e in entries}) != 1:
            continue
        e = entries[0]
        out.append(
            CoverShares(
                as_of=date.fromisoformat(e["end"]),
                filed=date.fromisoformat(e["filed"]),
                accession=accession,
                value=float(e["val"]),
            )
        )
    return sorted(out, key=lambda c: (c.filed, c.accession))
