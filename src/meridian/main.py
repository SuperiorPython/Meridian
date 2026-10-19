from datetime import date
from functools import lru_cache

import httpx
from fastapi import Depends, FastAPI, HTTPException

from meridian import __version__
from meridian.config import get_settings
from meridian.data.edgar import (
    EdgarClient,
    EdgarConfigError,
    EdgarError,
    NoFactsError,
    TickerNotFound,
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
    except EdgarConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except (TickerNotFound, NoFactsError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except EdgarError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return {
        "cik": comp.cik,
        "ticker": comp.ticker,
        "title": comp.title,
        "entity_name": facts.get("entityName"),
        "concepts_by_taxonomy": {
            tax: len(concepts) for tax, concepts in facts.get("facts", {}).items()
        },
    }
