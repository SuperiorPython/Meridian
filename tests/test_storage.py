import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from meridian.main import app
from meridian.storage.db import MIGRATIONS, Database, get_database
from meridian.storage.journal import Journal
from meridian.storage.watchlist import Watchlist


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "t.db")


# ---- database / migrations ----

def test_migrate_sets_version_and_is_idempotent(tmp_path):
    path = tmp_path / "sub" / "m.db"  # parent folder is created
    assert Database(path).schema_version() == len(MIGRATIONS)
    assert Database(path).schema_version() == len(MIGRATIONS)  # reopen: nothing re-run


def test_newer_schema_refused(tmp_path):
    path = tmp_path / "n.db"
    Database(path)
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version = {len(MIGRATIONS) + 1}")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="newer"):
        Database(path)


def test_failed_write_rolls_back(db):
    with pytest.raises(sqlite3.IntegrityError):
        with db.connection() as conn:
            conn.execute("INSERT INTO watchlist VALUES ('AAA', '', 'x')")
            conn.execute("INSERT INTO watchlist VALUES ('AAA', '', 'x')")  # duplicate key
    assert Watchlist(db).list() == []


def test_memory_database_persists_across_calls():
    mem = Database(":memory:")
    Watchlist(mem).upsert("AAPL")
    assert [i["ticker"] for i in Watchlist(mem).list()] == ["AAPL"]


# ---- watchlist ----

def test_watchlist_upsert_keeps_added_at_and_updates_note(db):
    wl = Watchlist(db)
    item, created = wl.upsert("AAPL", "first")
    assert created
    again, created2 = wl.upsert("AAPL", "second")
    assert not created2
    assert again["note"] == "second"
    assert again["added_at"] == item["added_at"]
    assert len(wl.list()) == 1


def test_watchlist_sorted_and_remove(db):
    wl = Watchlist(db)
    for t in ("MSFT", "AAPL", "NVDA"):
        wl.upsert(t)
    assert [i["ticker"] for i in wl.list()] == ["AAPL", "MSFT", "NVDA"]
    assert wl.remove("MSFT")
    assert not wl.remove("MSFT")


# ---- journal ----

def test_journal_add_and_fields(db):
    j = Journal(db)
    e = j.add("AAPL", "buy", "Services mix shift", conviction=4, price=190.5,
              review_on=date(2027, 1, 1))
    assert e["id"] == 1 and e["ticker"] == "AAPL" and e["decision"] == "buy"
    assert e["conviction"] == 4 and e["price"] == 190.5
    assert e["review_on"] == "2027-01-01"
    assert e["outcome_note"] is None and e["reviewed_at"] is None


def test_journal_constraints_enforced_by_database(db):
    j = Journal(db)
    with pytest.raises(sqlite3.IntegrityError):
        j.add("AAPL", "yolo", "x")
    with pytest.raises(sqlite3.IntegrityError):
        j.add("AAPL", "buy", "x", conviction=9)
    with pytest.raises(sqlite3.IntegrityError):
        j.add("AAPL", "buy", "x", price=-1)


def test_journal_list_newest_first_and_ticker_filter(db):
    j = Journal(db)
    j.add("AAPL", "buy", "one")
    j.add("MSFT", "watch", "two")
    j.add("AAPL", "hold", "three")
    assert [e["thesis"] for e in j.list()] == ["three", "two", "one"]
    assert [e["thesis"] for e in j.list("AAPL")] == ["three", "one"]


def test_journal_due_filter(db):
    j = Journal(db)
    j.add("A", "buy", "past due", review_on=date(2026, 1, 1))
    j.add("B", "buy", "future", review_on=date(2099, 1, 1))
    j.add("C", "buy", "no date")
    done = j.add("D", "buy", "reviewed", review_on=date(2026, 1, 1))
    j.review(done["id"], "worked")
    due = j.list(due_only=True, today=date(2026, 6, 1))
    assert [e["thesis"] for e in due] == ["past due"]
    on_day = j.list(due_only=True, today=date(2026, 1, 1))  # review date itself counts
    assert {e["thesis"] for e in on_day} == {"past due"}


def test_journal_review_keeps_thesis(db):
    j = Journal(db)
    e = j.add("AAPL", "buy", "original thesis")
    r = j.review(e["id"], "Wrong about margins")
    assert r["thesis"] == "original thesis"
    assert r["outcome_note"] == "Wrong about margins" and r["reviewed_at"]
    assert j.review(999, "x") is None


def test_journal_delete(db):
    j = Journal(db)
    e = j.add("AAPL", "buy", "x")
    assert j.delete(e["id"]) and not j.delete(e["id"])
    assert j.get(e["id"]) is None


# ---- API ----

@pytest.fixture
def client(tmp_path):
    database = Database(tmp_path / "api.db")
    app.dependency_overrides[get_database] = lambda: database
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_api_watchlist_flow(client):
    assert client.get("/watchlist").json() == {"count": 0, "items": []}
    r = client.put("/watchlist/aapl", json={"note": "core holding"})
    assert r.status_code == 200 and r.json()["ticker"] == "AAPL"
    assert client.put("/watchlist/MSFT").json()["note"] == ""  # body optional
    assert client.get("/watchlist").json()["count"] == 2
    assert client.delete("/watchlist/AAPL").json() == {"removed": "AAPL"}
    assert client.delete("/watchlist/AAPL").status_code == 404


def test_api_watchlist_bad_ticker(client):
    assert client.put("/watchlist/BAD$TICKER").status_code == 422
    assert client.delete("/watchlist/BAD$TICKER").status_code == 422


def test_api_journal_flow(client):
    body = {"ticker": "nvda", "decision": "buy", "thesis": "  AI demand  ",
            "conviction": 5, "price": 120.0, "review_on": "2026-01-01"}
    r = client.post("/journal", json=body)
    assert r.status_code == 201
    entry = r.json()
    assert entry["ticker"] == "NVDA" and entry["thesis"] == "AI demand"
    assert client.get(f"/journal/{entry['id']}").json()["id"] == entry["id"]
    assert client.get("/journal?ticker=nvda").json()["count"] == 1
    assert client.get("/journal?ticker=AAPL").json()["count"] == 0
    assert client.get("/journal?due=true").json()["count"] == 1  # review date passed
    rv = client.post(f"/journal/{entry['id']}/review", json={"outcome_note": "up 40%"})
    assert rv.json()["outcome_note"] == "up 40%"
    assert client.get("/journal?due=true").json()["count"] == 0
    assert client.delete(f"/journal/{entry['id']}").json() == {"deleted": entry["id"]}
    assert client.get(f"/journal/{entry['id']}").status_code == 404


@pytest.mark.parametrize("patch", [
    {"decision": "yolo"},
    {"thesis": "   "},
    {"thesis": ""},
    {"conviction": 6},
    {"conviction": 0},
    {"price": 0},
    {"review_on": "not-a-date"},
    {"ticker": "BAD$"},
])
def test_api_journal_rejects_bad_input(client, patch):
    body = {"ticker": "AAPL", "decision": "buy", "thesis": "ok"}
    body.update(patch)
    assert client.post("/journal", json=body).status_code == 422


def test_api_journal_missing_entries(client):
    assert client.get("/journal/42").status_code == 404
    assert client.post("/journal/42/review", json={"outcome_note": "x"}).status_code == 404
    assert client.post("/journal/42/review", json={"outcome_note": "  "}).status_code == 422
    assert client.delete("/journal/42").status_code == 404
