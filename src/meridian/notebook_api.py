"""Watchlist and decision-journal endpoints (your own data, stored in SQLite)."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from meridian.data.prices import PriceService
from meridian.storage.db import Database, get_database
from meridian.storage.journal import Journal
from meridian.storage.watchlist import Watchlist

router = APIRouter()


def clean(ticker: str) -> str:
    try:
        return PriceService.clean_ticker(ticker)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


class WatchlistBody(BaseModel):
    note: str = Field("", max_length=2000)


class JournalBody(BaseModel):
    ticker: str
    decision: Literal["buy", "sell", "hold", "watch", "pass"]
    thesis: str = Field(min_length=1, max_length=20000)
    conviction: int | None = Field(None, ge=1, le=5)
    price: float | None = Field(None, gt=0)
    review_on: date | None = None

    @field_validator("thesis")
    @classmethod
    def thesis_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("thesis cannot be blank")
        return v.strip()


class ReviewBody(BaseModel):
    outcome_note: str = Field(min_length=1, max_length=20000)

    @field_validator("outcome_note")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("outcome_note cannot be blank")
        return v.strip()


@router.get("/watchlist")
def list_watchlist(db: Database = Depends(get_database)) -> dict:
    items = Watchlist(db).list()
    return {"count": len(items), "items": items}


@router.put("/watchlist/{ticker}")
def put_watchlist(
    ticker: str, body: WatchlistBody | None = None, db: Database = Depends(get_database)
) -> dict:
    item, _ = Watchlist(db).upsert(clean(ticker), body.note if body else "")
    return item


@router.delete("/watchlist/{ticker}")
def delete_watchlist(ticker: str, db: Database = Depends(get_database)) -> dict:
    t = clean(ticker)
    if not Watchlist(db).remove(t):
        raise HTTPException(status_code=404, detail=f"{t} is not on the watchlist")
    return {"removed": t}


@router.post("/journal", status_code=201)
def add_entry(body: JournalBody, db: Database = Depends(get_database)) -> dict:
    return Journal(db).add(
        clean(body.ticker),
        body.decision,
        body.thesis,
        body.conviction,
        body.price,
        body.review_on,
    )


@router.get("/journal")
def list_entries(
    ticker: str | None = Query(None),
    due: bool = Query(False, description="Only entries whose review date has arrived"),
    db: Database = Depends(get_database),
) -> dict:
    entries = Journal(db).list(clean(ticker) if ticker else None, due_only=due)
    return {"count": len(entries), "entries": entries}


@router.get("/journal/{entry_id}")
def get_entry(entry_id: int, db: Database = Depends(get_database)) -> dict:
    entry = Journal(db).get(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"no journal entry {entry_id}")
    return entry


@router.post("/journal/{entry_id}/review")
def review_entry(
    entry_id: int, body: ReviewBody, db: Database = Depends(get_database)
) -> dict:
    entry = Journal(db).review(entry_id, body.outcome_note)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"no journal entry {entry_id}")
    return entry


@router.delete("/journal/{entry_id}")
def delete_entry(entry_id: int, db: Database = Depends(get_database)) -> dict:
    if not Journal(db).delete(entry_id):
        raise HTTPException(status_code=404, detail=f"no journal entry {entry_id}")
    return {"deleted": entry_id}
