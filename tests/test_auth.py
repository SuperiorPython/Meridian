import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from meridian.auth import MIN_TOKEN_LENGTH, parse_origins, require_auth
from meridian.config import Settings, get_settings
from meridian.main import app
from meridian.storage.db import Database, get_database

TOKEN = "t" * 32


@pytest.fixture
def make_client(tmp_path):
    database = Database(tmp_path / "auth.db")
    app.dependency_overrides[get_database] = lambda: database

    def build(token="", env="dev"):
        cfg = Settings(_env_file=None, api_token=token, env=env)
        app.dependency_overrides[get_settings] = lambda: cfg
        return TestClient(app)

    yield build
    app.dependency_overrides.clear()


def bearer(token=TOKEN):
    return {"Authorization": f"Bearer {token}"}


def test_parse_origins():
    assert parse_origins(" http://a.com/ , ,http://b.com") == ["http://a.com", "http://b.com"]
    assert parse_origins("") == []


# ---- dev with no key: open ----

def test_dev_without_token_is_open(make_client):
    c = make_client("", "dev")
    assert c.get("/watchlist").status_code == 200
    body = c.get("/auth/check").json()
    assert body == {"authenticated": True, "auth_enabled": False}


# ---- key configured ----

def test_correct_key_accepted(make_client):
    c = make_client(TOKEN, "prod")
    assert c.get("/watchlist", headers=bearer()).status_code == 200
    assert c.get("/auth/check", headers=bearer()).json() == {
        "authenticated": True, "auth_enabled": True}


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer wrong-key-wrong-key-wrong-key-123"},
    {"Authorization": "Bearer "},
    {"Authorization": f"Basic {TOKEN}"},
    {"Authorization": TOKEN},
    {"Authorization": f"Bearer {TOKEN}x"},
    {"Authorization": f"Bearer {TOKEN[:-1]}"},
])
def test_bad_credentials_rejected(make_client, headers):
    c = make_client(TOKEN, "prod")
    r = c.get("/watchlist", headers=headers)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_token_enforced_in_dev_too_when_set(make_client):
    assert make_client(TOKEN, "dev").get("/watchlist").status_code == 401


@pytest.mark.parametrize("method,path", [
    ("get", "/watchlist"), ("put", "/watchlist/AAPL"), ("delete", "/watchlist/AAPL"),
    ("get", "/journal"), ("post", "/journal"), ("get", "/journal/1"),
    ("post", "/journal/1/review"), ("delete", "/journal/1"),
    ("get", "/prices/AAPL"), ("get", "/companies/AAPL"), ("get", "/fundamentals/AAPL"),
    ("get", "/analysis/AAPL"), ("get", "/valuation/AAPL"), ("get", "/report/AAPL"),
    ("get", "/auth/check"),
    ("get", "/paper"), ("put", "/paper/account"), ("get", "/paper/trades"),
    ("post", "/paper/trades"), ("delete", "/paper/trades/1"), ("get", "/paper/performance"),
])
def test_every_data_endpoint_requires_key(make_client, method, path):
    c = make_client(TOKEN, "prod")
    assert getattr(c, method)(path).status_code == 401  # rejected before any work happens


def test_health_stays_public(make_client):
    assert make_client(TOKEN, "prod").get("/health").status_code == 200
    assert make_client("", "prod").get("/health").status_code == 200


# ---- fail closed ----

def test_locked_outside_dev_without_token(make_client):
    c = make_client("", "prod")
    r = c.get("/watchlist")
    assert r.status_code == 503 and "MERIDIAN_API_TOKEN" in r.json()["detail"]
    assert c.get("/watchlist", headers=bearer()).status_code == 503  # a header can't unlock it


def test_short_token_refused(make_client):
    short = "x" * (MIN_TOKEN_LENGTH - 1)
    c = make_client(short, "prod")
    assert c.get("/watchlist", headers=bearer(short)).status_code == 503


# ---- guard against future routes forgetting auth ----

def test_all_routes_except_health_are_protected():
    public = {"/health"}
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path in public:
            continue
        calls = [d.call for d in route.dependant.dependencies]
        assert require_auth in calls, f"{route.path} is not behind require_auth"


# ---- CORS ----

def test_cors_allows_frontend_origin_and_blocks_others(make_client):
    c = make_client(TOKEN, "prod")
    ok = c.get("/health", headers={"Origin": "http://localhost:5173"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = c.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in bad.headers


def test_preflight_succeeds_without_key(make_client):
    c = make_client(TOKEN, "prod")
    r = c.options("/watchlist", headers={
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization",
    })
    assert r.status_code == 200
    assert "authorization" in r.headers["access-control-allow-headers"].lower()
