import os
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from meridian.data.edgar import (
    EdgarClient,
    EdgarConfigError,
    EdgarError,
    NoFactsError,
    TickerNotFound,
)
from meridian.main import app, get_edgar_client

UA = "Meridian Tests tester@meridian-test.dev"

TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
}
FACTS = {
    "cik": 320193,
    "entityName": "Apple Inc.",
    "facts": {
        "dei": {"EntityCommonStockSharesOutstanding": {}},
        "us-gaap": {"Revenues": {}, "Assets": {}},
    },
}


class Fake:
    """Mock SEC server that records requests."""

    def __init__(self, fail=False, facts_status=200):
        self.requests = []
        self.fail = fail
        self.facts_status = facts_status

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail:
            return httpx.Response(500)
        url = str(request.url)
        if url.endswith("company_tickers.json"):
            return httpx.Response(200, json=TICKERS)
        if "companyfacts" in url:
            if self.facts_status != 200:
                return httpx.Response(self.facts_status)
            return httpx.Response(200, json=FACTS)
        return httpx.Response(404)


def make_client(tmp_path, fake=None, ua=UA, **kw):
    fake = fake or Fake()
    http = httpx.Client(transport=httpx.MockTransport(fake.handler))
    kw.setdefault("min_interval", 0)
    return EdgarClient(http, ua, tmp_path, **kw), fake


def test_lookup_company_case_insensitive(tmp_path):
    client, _ = make_client(tmp_path)
    comp = client.lookup_company("msft")
    assert comp.cik == 789019
    assert comp.cik10 == "0000789019"
    assert comp.title == "MICROSOFT CORP"


def test_unknown_ticker(tmp_path):
    client, _ = make_client(tmp_path)
    with pytest.raises(TickerNotFound):
        client.lookup_company("NOPE")


def test_user_agent_header_sent(tmp_path):
    client, fake = make_client(tmp_path)
    client.lookup_company("AAPL")
    assert fake.requests[0].headers["user-agent"] == UA


@pytest.mark.parametrize("ua", ["Meridian Research your-email@example.com",
                                "Meridian Research unset@example.com", ""])
def test_placeholder_user_agent_refused(tmp_path, ua):
    client, fake = make_client(tmp_path, ua=ua)
    with pytest.raises(EdgarConfigError):
        client.lookup_company("AAPL")
    assert fake.requests == []


def test_tickers_cached(tmp_path):
    client, fake = make_client(tmp_path)
    client.lookup_company("AAPL")
    client.lookup_company("MSFT")
    assert len(fake.requests) == 1


def test_facts_fetched_with_padded_cik_and_cached(tmp_path):
    client, fake = make_client(tmp_path)
    comp = client.lookup_company("AAPL")
    assert client.get_company_facts(comp)["entityName"] == "Apple Inc."
    client.get_company_facts(comp)
    facts_requests = [r for r in fake.requests if "companyfacts" in str(r.url)]
    assert len(facts_requests) == 1
    assert str(facts_requests[0].url).endswith("CIK0000320193.json")


def test_stale_cache_served_when_edgar_fails(tmp_path):
    client, _ = make_client(tmp_path, facts_ttl=60)
    comp = client.lookup_company("AAPL")
    client.get_company_facts(comp)
    path = tmp_path / "facts_0000320193.json"
    old = time.time() - 3600
    os.utime(path, (old, old))
    broken, _ = make_client(tmp_path, Fake(fail=True), facts_ttl=60)
    assert broken.get_company_facts(comp)["entityName"] == "Apple Inc."


def test_failure_without_cache_raises(tmp_path):
    client, _ = make_client(tmp_path, Fake(fail=True))
    with pytest.raises(EdgarError):
        client.lookup_company("AAPL")


def test_facts_404_is_no_facts(tmp_path):
    client, _ = make_client(tmp_path, Fake(facts_status=404))
    comp = client.lookup_company("AAPL")
    with pytest.raises(NoFactsError):
        client.get_company_facts(comp)


def test_rate_limit_sleeps_between_requests(tmp_path):
    sleeps = []
    client, _ = make_client(
        tmp_path, min_interval=1.0, sleep=sleeps.append, clock=lambda: 0.0
    )
    comp = client.lookup_company("AAPL")  # request 1
    client.get_company_facts(comp)  # request 2
    assert sleeps == [1.0]


@pytest.fixture
def api(tmp_path):
    client, _ = make_client(tmp_path)
    app.dependency_overrides[get_edgar_client] = lambda: client
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_company_endpoint(api):
    r = api.get("/companies/aapl")
    assert r.status_code == 200
    body = r.json()
    assert body["cik"] == 320193
    assert body["entity_name"] == "Apple Inc."
    assert body["concepts_by_taxonomy"] == {"dei": 1, "us-gaap": 2}


def test_company_endpoint_unknown_ticker(api):
    assert api.get("/companies/NOPE").status_code == 404


def test_company_endpoint_bad_ticker(api):
    assert api.get("/companies/bad$ticker").status_code == 422


def test_company_endpoint_placeholder_ua(tmp_path):
    client, _ = make_client(tmp_path, ua="Meridian Research your-email@example.com")
    app.dependency_overrides[get_edgar_client] = lambda: client
    try:
        r = TestClient(app).get("/companies/AAPL")
        assert r.status_code == 503
        assert "MERIDIAN_SEC_USER_AGENT" in r.json()["detail"]
    finally:
        app.dependency_overrides.clear()
