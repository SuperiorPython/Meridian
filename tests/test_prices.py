import os
import time

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from meridian.data.prices import (
    NoDataError,
    PriceService,
    ProviderError,
    normalize_frame,
)
from meridian.main import app, get_price_service


def make_frame(n: int = 5) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="D", name="date")
    base = pd.Series(range(100, 100 + n), index=idx, dtype=float)
    return pd.DataFrame(
        {
            "open": base,
            "high": base + 1,
            "low": base - 1,
            "close": base,
            "adj_close": base * 0.99,
            "volume": 1000.0,
        }
    )


class FakeProvider:
    def __init__(self, frame=None, error=None):
        self.frame = frame if frame is not None else make_frame()
        self.error = error
        self.calls = 0

    def fetch_history(self, ticker):
        self.calls += 1
        if self.error:
            raise self.error
        return self.frame


def age_cache(service: PriceService, ticker: str, seconds: float) -> None:
    path = service._path(ticker)
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_second_call_hits_cache(tmp_path):
    provider = FakeProvider()
    service = PriceService(provider, tmp_path)
    service.get_history("aapl")
    service.get_history("AAPL")
    assert provider.calls == 1


def test_stale_cache_refetches(tmp_path):
    provider = FakeProvider()
    service = PriceService(provider, tmp_path, ttl_seconds=60)
    service.get_history("AAPL")
    age_cache(service, "AAPL", 3600)
    service.get_history("AAPL")
    assert provider.calls == 2


def test_provider_failure_serves_stale_cache(tmp_path):
    service = PriceService(FakeProvider(), tmp_path, ttl_seconds=60)
    service.get_history("AAPL")
    age_cache(service, "AAPL", 3600)
    service.provider = FakeProvider(error=ProviderError("boom"))
    df = service.get_history("AAPL")
    assert len(df) == 5


def test_provider_failure_without_cache_raises(tmp_path):
    service = PriceService(FakeProvider(error=NoDataError("nope")), tmp_path)
    with pytest.raises(NoDataError):
        service.get_history("ZZZZ")


def test_date_slicing(tmp_path):
    service = PriceService(FakeProvider(), tmp_path)
    df = service.get_history("AAPL", start=pd.Timestamp("2024-01-02").date(),
                             end=pd.Timestamp("2024-01-03").date())
    assert list(df.index.strftime("%Y-%m-%d")) == ["2024-01-02", "2024-01-03"]


@pytest.mark.parametrize("bad", ["../etc/passwd", "AA PL", "", "A" * 11, "AAPL/../x"])
def test_invalid_ticker_rejected(tmp_path, bad):
    service = PriceService(FakeProvider(), tmp_path)
    with pytest.raises(ValueError):
        service.get_history(bad)


def test_normalize_frame_handles_yfinance_shape():
    raw = make_frame().rename(columns=lambda c: c.replace("_", " ").title())
    raw["Dividends"] = 0.0
    raw.index = raw.index.tz_localize("America/New_York")
    out = normalize_frame(raw)
    assert list(out.columns) == ["open", "high", "low", "close", "adj_close", "volume"]
    assert out.index.tz is None
    assert out.index.name == "date"


def test_normalize_frame_missing_columns():
    with pytest.raises(ProviderError):
        normalize_frame(make_frame().drop(columns=["adj_close"]))


@pytest.fixture
def client(tmp_path):
    service = PriceService(FakeProvider(), tmp_path)
    app.dependency_overrides[get_price_service] = lambda: service
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_prices_endpoint(client):
    r = client.get("/prices/aapl?start=2024-01-02&end=2024-01-04")
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "AAPL"
    assert body["count"] == 3
    assert body["rows"][0]["date"] == "2024-01-02"
    assert "adj_close" in body["rows"][0]


def test_prices_endpoint_bad_ticker(client):
    assert client.get("/prices/bad$ticker").status_code == 422


def test_prices_endpoint_no_data(tmp_path):
    service = PriceService(FakeProvider(error=NoDataError("none")), tmp_path)
    app.dependency_overrides[get_price_service] = lambda: service
    try:
        assert TestClient(app).get("/prices/ZZZZ").status_code == 404
    finally:
        app.dependency_overrides.clear()
