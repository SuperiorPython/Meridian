"""Shared FastAPI dependencies (kept apart from main so routers can use them)."""

from functools import lru_cache

import httpx

from meridian.config import get_settings
from meridian.data.edgar import EdgarClient
from meridian.data.prices import PriceService, YFinanceProvider


@lru_cache
def get_price_service() -> PriceService:
    return PriceService(YFinanceProvider(), get_settings().cache_dir / "prices")


@lru_cache
def get_edgar_client() -> EdgarClient:
    settings = get_settings()
    http = httpx.Client(timeout=30, follow_redirects=True)
    return EdgarClient(http, settings.sec_user_agent, settings.cache_dir / "edgar")
