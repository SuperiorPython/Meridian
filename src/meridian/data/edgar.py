"""SEC EDGAR client: ticker -> CIK lookup and XBRL company facts, with caching."""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

log = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"

TICKERS_TTL_SECONDS = 7 * 24 * 60 * 60
FACTS_TTL_SECONDS = 24 * 60 * 60
# SEC's limit is 10 requests/second; stay safely under it.
MIN_INTERVAL_SECONDS = 0.11


class EdgarError(Exception):
    """EDGAR request failed or returned something unusable."""


class EdgarConfigError(EdgarError):
    """Client is misconfigured (e.g. placeholder User-Agent)."""


class TickerNotFound(EdgarError):
    """Ticker is not in SEC's ticker list."""


class NoFactsError(EdgarError):
    """Company exists but has no XBRL company facts."""


@dataclass(frozen=True)
class Company:
    cik: int
    ticker: str
    title: str

    @property
    def cik10(self) -> str:
        return f"{self.cik:010d}"


class EdgarClient:
    def __init__(
        self,
        http: httpx.Client,
        user_agent: str,
        cache_dir: Path,
        tickers_ttl: float = TICKERS_TTL_SECONDS,
        facts_ttl: float = FACTS_TTL_SECONDS,
        min_interval: float = MIN_INTERVAL_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.http = http
        self.user_agent = user_agent
        self.cache_dir = Path(cache_dir)
        self.tickers_ttl = tickers_ttl
        self.facts_ttl = facts_ttl
        self.min_interval = min_interval
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None

    # -- low level ---------------------------------------------------------

    def _check_user_agent(self) -> None:
        ua = self.user_agent.strip()
        if not ua or "example.com" in ua or "unset@" in ua:
            raise EdgarConfigError(
                "Set MERIDIAN_SEC_USER_AGENT in .env to 'Your Name your@email.com' "
                "(SEC requires a real contact in the User-Agent)."
            )

    def _throttle(self) -> None:
        if self._last_request is not None:
            wait = self.min_interval - (self._clock() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._clock()

    def _fetch_json(self, url: str) -> dict:
        self._check_user_agent()
        self._throttle()
        try:
            resp = self.http.get(url, headers={"User-Agent": self.user_agent})
        except httpx.HTTPError as exc:
            raise EdgarError(f"request to {url} failed: {exc}") from exc
        if resp.status_code == 404:
            raise NoFactsError(f"not found: {url}")
        if resp.status_code != 200:
            raise EdgarError(f"{url} returned HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise EdgarError(f"{url} returned invalid JSON") from exc

    def _get_json_cached(self, url: str, name: str, ttl: float) -> dict:
        path = self.cache_dir / name
        fresh = path.exists() and (time.time() - path.stat().st_mtime) < ttl
        if fresh:
            return json.loads(path.read_text(encoding="utf-8"))
        try:
            data = self._fetch_json(url)
        except EdgarConfigError:
            raise
        except NoFactsError:
            raise
        except EdgarError:
            if not path.exists():
                raise
            log.warning("EDGAR fetch failed for %s; serving stale cache", name)
            return json.loads(path.read_text(encoding="utf-8"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)  # atomic
        return data

    # -- public ------------------------------------------------------------

    def lookup_company(self, ticker: str) -> Company:
        wanted = ticker.strip().upper()
        data = self._get_json_cached(
            TICKERS_URL, "company_tickers.json", self.tickers_ttl
        )
        for entry in data.values():
            if str(entry.get("ticker", "")).upper() == wanted:
                return Company(
                    cik=int(entry["cik_str"]),
                    ticker=wanted,
                    title=str(entry.get("title", "")),
                )
        raise TickerNotFound(f"ticker not found in SEC list: {wanted}")

    def get_company_facts(self, company: Company) -> dict:
        return self._get_json_cached(
            FACTS_URL.format(cik10=company.cik10),
            f"facts_{company.cik10}.json",
            self.facts_ttl,
        )
