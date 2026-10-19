"""Price history: provider interface, yfinance implementation, and a file cache."""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import date
from pathlib import Path
from typing import Protocol

import pandas as pd

log = logging.getLogger(__name__)

COLUMNS = ["open", "high", "low", "close", "adj_close", "volume"]
TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,10}$")
DEFAULT_TTL_SECONDS = 12 * 60 * 60


class ProviderError(Exception):
    """The data provider failed (network, rate limit, upstream change)."""


class NoDataError(ProviderError):
    """The provider responded but has no data for this ticker."""


class PriceProvider(Protocol):
    def fetch_history(self, ticker: str) -> pd.DataFrame:
        """Full daily history, indexed by date, with COLUMNS as columns."""
        ...


def normalize_frame(raw: pd.DataFrame) -> pd.DataFrame:
    """Coerce a provider's frame into Meridian's canonical shape."""
    df = raw.rename(columns=lambda c: str(c).strip().lower().replace(" ", "_"))
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ProviderError(f"provider response missing columns: {missing}")
    df = df[COLUMNS].copy()
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    df.index = idx.normalize()
    df.index.name = "date"
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df.dropna(subset=["close"])


class YFinanceProvider:
    """Prototype data source. Unofficial and may break without notice."""

    def fetch_history(self, ticker: str) -> pd.DataFrame:
        import yfinance as yf  # imported lazily so tests don't need network libs

        try:
            raw = yf.Ticker(ticker).history(period="max", auto_adjust=False)
        except Exception as exc:  # yfinance raises a wide variety of errors
            raise ProviderError(f"yfinance failed for {ticker}: {exc}") from exc
        if raw is None or raw.empty:
            raise NoDataError(f"no price data for {ticker}")
        return normalize_frame(raw)


class PriceService:
    def __init__(
        self,
        provider: PriceProvider,
        cache_dir: Path,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
    ):
        self.provider = provider
        self.cache_dir = Path(cache_dir)
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def clean_ticker(ticker: str) -> str:
        t = ticker.strip().upper()
        if not TICKER_RE.match(t):
            raise ValueError(f"invalid ticker: {ticker!r}")
        return t

    def _path(self, ticker: str) -> Path:
        return self.cache_dir / f"{ticker}.csv"

    def _is_fresh(self, path: Path) -> bool:
        return path.exists() and (time.time() - path.stat().st_mtime) < self.ttl_seconds

    @staticmethod
    def _read(path: Path) -> pd.DataFrame:
        return pd.read_csv(path, index_col="date", parse_dates=True)

    def _write(self, path: Path, df: pd.DataFrame) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".csv.tmp")
        df.to_csv(tmp)
        os.replace(tmp, path)  # atomic: readers never see a half-written file

    def get_history(
        self, ticker: str, start: date | None = None, end: date | None = None
    ) -> pd.DataFrame:
        ticker = self.clean_ticker(ticker)
        path = self._path(ticker)

        if self._is_fresh(path):
            df = self._read(path)
        else:
            try:
                df = self.provider.fetch_history(ticker)
                self._write(path, df)
            except ProviderError:
                if not path.exists():
                    raise
                log.warning("provider failed for %s; serving stale cache", ticker)
                df = self._read(path)

        if start is not None:
            df = df.loc[df.index >= pd.Timestamp(start)]
        if end is not None:
            df = df.loc[df.index <= pd.Timestamp(end)]
        return df
