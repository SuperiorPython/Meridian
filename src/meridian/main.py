from datetime import date
from functools import lru_cache

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query

from meridian import __version__
from meridian.config import get_settings
from meridian.data.edgar import (
    EdgarClient,
    EdgarConfigError,
    EdgarError,
    NoFactsError,
    TickerNotFound,
)
from meridian.analysis.metrics import compute_metrics, is_financial_sic
from meridian.analysis.valuation import compute_valuation, current_valuation
from meridian.data.fundamentals import (
    extract_annual,
    extract_cover_shares,
    recent_periods,
)
from meridian.data.prices import (
    NoDataError,
    PriceService,
    ProviderError,
    YFinanceProvider,
)

app = FastAPI(title="Meridian", version=__version__)


@lru_cache
def get_price_service() -> PriceService:
    return PriceService(YFinanceProvider(), get_settings().cache_dir / "prices")


@lru_cache
def get_edgar_client() -> EdgarClient:
    settings = get_settings()
    http = httpx.Client(timeout=30, follow_redirects=True)
    return EdgarClient(http, settings.sec_user_agent, settings.cache_dir / "edgar")


def edgar_http_error(exc: EdgarError) -> HTTPException:
    if isinstance(exc, EdgarConfigError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, (TickerNotFound, NoFactsError)):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__, "env": get_settings().env}


@app.get("/prices/{ticker}")
def prices(
    ticker: str,
    start: date | None = None,
    end: date | None = None,
    service: PriceService = Depends(get_price_service),
) -> dict:
    try:
        df = service.get_history(ticker, start, end)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except NoDataError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    out = df.reset_index()
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    rows = out.astype(object).where(out.notna(), None).to_dict("records")
    return {"ticker": service.clean_ticker(ticker), "count": len(rows), "rows": rows}


@app.get("/companies/{ticker}")
def company(ticker: str, client: EdgarClient = Depends(get_edgar_client)) -> dict:
    try:
        ticker = PriceService.clean_ticker(ticker)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    try:
        comp = client.lookup_company(ticker)
        facts = client.get_company_facts(comp)
    except EdgarError as exc:
        raise edgar_http_error(exc)

    return {
        "cik": comp.cik,
        "ticker": comp.ticker,
        "title": comp.title,
        "entity_name": facts.get("entityName"),
        "concepts_by_taxonomy": {
            tax: len(concepts) for tax, concepts in facts.get("facts", {}).items()
        },
    }


@app.get("/fundamentals/{ticker}")
def fundamentals(
    ticker: str,
    years: int = Query(10, ge=1, le=50),
    client: EdgarClient = Depends(get_edgar_client),
) -> dict:
    try:
        ticker = PriceService.clean_ticker(ticker)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    try:
        comp = client.lookup_company(ticker)
        facts = client.get_company_facts(comp)
    except EdgarError as exc:
        raise edgar_http_error(exc)

    annual = extract_annual(facts)
    # Window by the most recent distinct fiscal periods across ALL metrics, so a metric
    # a company stopped reporting years ago (e.g. GE operating income, last filed for
    # 2012) shows as missing instead of returning stale periods.
    window = recent_periods(annual, years)
    metrics = {
        name: [
            {
                "period_end": f.period_end.isoformat(),
                "value": f.value,
                "filed": f.filed.isoformat(),
                "latest_value": f.latest_value,
                "latest_filed": f.latest_filed.isoformat(),
                "tag": f.tag,
                "form": f.form,
                "accession": f.accession,
            }
            for f in series
            if f.period_end in window
        ]
        for name, series in annual.items()
    }
    return {
        "ticker": comp.ticker,
        "cik": comp.cik,
        "entity_name": facts.get("entityName"),
        "metrics": metrics,
        "missing": [name for name, rows in metrics.items() if not rows],
        "cover_shares": [
            {
                "as_of": c.as_of.isoformat(),
                "filed": c.filed.isoformat(),
                "value": c.value,
                "accession": c.accession,
            }
            for c in extract_cover_shares(facts)[-years:]
        ],
    }


ANALYSIS_NOTES = [
    "Growth is like-for-like: both years come from the same filing, so restatements "
    "and spin-offs do not distort it.",
    "Free cash flow = operating cash flow - cash capex; it excludes assets acquired "
    "through finance leases, so it is overstated for lease-heavy companies.",
    "ROE uses average equity when the prior year is available. liabilities_to_equity "
    "is total liabilities over equity (not interest-bearing debt).",
    "Values are as first reported; available_from is when every figure for the "
    "period was public.",
]


@app.get("/analysis/{ticker}")
def analysis(
    ticker: str,
    years: int = Query(10, ge=1, le=50),
    client: EdgarClient = Depends(get_edgar_client),
) -> dict:
    try:
        ticker = PriceService.clean_ticker(ticker)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    try:
        comp = client.lookup_company(ticker)
        facts = client.get_company_facts(comp)
    except EdgarError as exc:
        raise edgar_http_error(exc)

    try:
        sic, sic_description = client.get_sic(comp)
    except EdgarError:
        sic, sic_description = None, None  # degrade: sector unknown, metrics still shown

    financial = is_financial_sic(sic)
    notes = list(ANALYSIS_NOTES)
    if financial:
        notes.append(
            f"Financial company (SIC {sic}): free cash flow metrics are not shown, "
            "and liabilities_to_equity is not comparable to non-financial companies."
        )
    if sic is None:
        notes.append(
            "Sector unknown (SIC lookup failed): if this is a bank or insurer, "
            "cash-flow metrics will be misleading."
        )

    annual = extract_annual(facts)
    window = recent_periods(annual, years)
    periods = [
        pm.to_dict()
        for pm in compute_metrics(annual, is_financial=financial)
        if pm.period_end in window
    ]
    return {
        "ticker": comp.ticker,
        "cik": comp.cik,
        "entity_name": facts.get("entityName"),
        "sic": sic,
        "sic_description": sic_description,
        "is_financial": financial,
        "notes": notes,
        "periods": periods,
    }


VALUATION_NOTES = [
    "Market cap = shares x price on the first trading day on or after the 10-K "
    "filing date (point-in-time: no price from before the numbers were public).",
    "Shares are the 10-K cover-page count converted to today's split basis so they "
    "match Yahoo's split-adjusted prices; split_factor shows the adjustment applied. "
    "Where no cover count exists, weighted-average diluted shares are used "
    "(shares_source = diluted_average).",
    "Prices are split-adjusted but not dividend-adjusted. price_then is the actual "
    "closing price that day.",
    "Ratios use annual (not trailing-twelve-month) fundamentals, so the current "
    "block can lag the latest quarter by up to a year.",
]


@app.get("/valuation/{ticker}")
def valuation(
    ticker: str,
    years: int = Query(10, ge=1, le=50),
    client: EdgarClient = Depends(get_edgar_client),
    price_service: PriceService = Depends(get_price_service),
) -> dict:
    try:
        ticker = PriceService.clean_ticker(ticker)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    try:
        comp = client.lookup_company(ticker)
        facts = client.get_company_facts(comp)
    except EdgarError as exc:
        raise edgar_http_error(exc)
    try:
        prices = price_service.get_history(ticker)
    except NoDataError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    try:
        sic, _ = client.get_sic(comp)
    except EdgarError:
        sic = None

    financial = is_financial_sic(sic)
    notes = list(VALUATION_NOTES)
    if financial:
        notes.append("Financial company: FCF yield is not shown.")

    annual = extract_annual(facts)
    covers = extract_cover_shares(facts)
    window = recent_periods(annual, years)
    periods = [
        p.to_dict()
        for p in compute_valuation(annual, covers, prices, is_financial=financial)
        if p.period_end in window
    ]
    return {
        "ticker": comp.ticker,
        "cik": comp.cik,
        "entity_name": facts.get("entityName"),
        "is_financial": financial,
        "notes": notes,
        "current": current_valuation(annual, covers, prices, is_financial=financial),
        "periods": periods,
    }
