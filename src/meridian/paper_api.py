"""Paper portfolio endpoints. Trades are recorded at the real closing price on a real
trading day; cash, holdings and performance are recomputed from the ledger."""

import datetime as dt
from typing import Literal

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from meridian.analysis import paper as calc
from meridian.analysis.valuation import close_on_or_after
from meridian.data.prices import NoDataError, PriceService, ProviderError
from meridian.deps import get_price_service
from meridian.notebook_api import clean
from meridian.storage.db import Database, get_database
from meridian.storage.paper import PaperLedger

router = APIRouter(prefix="/paper")


class TradeBody(BaseModel):
    ticker: str
    side: Literal["buy", "sell"]
    shares: float = Field(gt=0, le=1e9, allow_inf_nan=False)
    # Annotated via dt.date because the field name would shadow `date` in the class body.
    date: dt.date | None = Field(None, description="Defaults to the latest close")
    note: str = Field("", max_length=500)


class AccountBody(BaseModel):
    starting_cash: float = Field(gt=0, le=1e12, allow_inf_nan=False)


def load_prices(service: PriceService, ticker: str) -> pd.DataFrame:
    try:
        return service.get_history(ticker)
    except NoDataError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


def optional_prices(service: PriceService, ticker: str) -> pd.DataFrame | None:
    try:
        return service.get_history(ticker)
    except ProviderError:  # includes NoDataError
        return None


def decorate(row: dict) -> dict:
    return {**row, "amount": round(row["shares"] * row["price"], 2)}


@router.get("")
def portfolio(
    db: Database = Depends(get_database),
    prices: PriceService = Depends(get_price_service),
) -> dict:
    ledger = PaperLedger(db)
    trades = [calc.Trade.from_row(r) for r in ledger.trades()]
    frames = {}
    for ticker in sorted({t.ticker for t in trades}):
        frame = optional_prices(prices, ticker)
        if frame is not None:
            frames[ticker] = frame
    result = calc.summary(trades, ledger.starting_cash(), frames, dt.date.today())
    result["trade_count"] = len(trades)
    return result


@router.put("/account")
def set_account(body: AccountBody, db: Database = Depends(get_database)) -> dict:
    if not PaperLedger(db).set_starting_cash(body.starting_cash):
        raise HTTPException(
            status_code=409,
            detail="Starting cash can only be changed while there are no trades. "
            "Delete the trades first.",
        )
    return {"starting_cash": body.starting_cash}


@router.get("/trades")
def list_trades(
    ticker: str | None = Query(None), db: Database = Depends(get_database)
) -> dict:
    rows = PaperLedger(db).trades(clean(ticker) if ticker else None)
    return {"count": len(rows), "trades": [decorate(r) for r in reversed(rows)]}  # newest first


@router.post("/trades", status_code=201)
def add_trade(
    body: TradeBody,
    db: Database = Depends(get_database),
    prices: PriceService = Depends(get_price_service),
) -> dict:
    ticker = clean(body.ticker)
    frame = load_prices(prices, ticker)
    last_day = frame.index[-1].date()
    requested = body.date or last_day
    if requested > last_day:
        raise HTTPException(
            status_code=422,
            detail=f"There is no closing price after {last_day} yet. Pick that date or earlier.",
        )
    fill = close_on_or_after(frame, requested)
    if fill is None:
        raise HTTPException(
            status_code=422,
            detail=f"No trading day for {ticker} within a week of {requested}. "
            "It may not have been listed yet.",
        )
    fill_date, adjusted_close = fill
    splits = calc.splits_of(frame)
    price = calc.actual_price(adjusted_close, splits, fill_date)

    ledger = PaperLedger(db)
    existing = [calc.Trade.from_row(r) for r in ledger.trades()]
    new = calc.Trade(2**62, ticker, body.side, body.shares, price, fill_date)
    try:
        calc.check_cash(existing + [new], ledger.starting_cash())
        calc.check_shares([t for t in existing if t.ticker == ticker] + [new], splits)
    except calc.LedgerError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    row = ledger.add(ticker, body.side, body.shares, price, fill_date, requested, body.note.strip())
    return decorate(row)


@router.delete("/trades/{trade_id}")
def delete_trade(
    trade_id: int,
    db: Database = Depends(get_database),
    prices: PriceService = Depends(get_price_service),
) -> dict:
    ledger = PaperLedger(db)
    row = ledger.get(trade_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no trade {trade_id}")

    # Removing a trade can break later ones (a buy that funded or supplied a sale).
    remaining = [calc.Trade.from_row(r) for r in ledger.trades() if r["id"] != trade_id]
    splits = calc.splits_of(load_prices(prices, row["ticker"]))
    try:
        calc.check_cash(remaining, ledger.starting_cash())
        calc.check_shares([t for t in remaining if t.ticker == row["ticker"]], splits)
    except calc.LedgerError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Can't delete this trade because later trades depend on it. {exc}",
        )
    ledger.delete(trade_id)
    return {"deleted": trade_id}


@router.get("/performance")
def performance(
    db: Database = Depends(get_database),
    prices: PriceService = Depends(get_price_service),
) -> dict:
    ledger = PaperLedger(db)
    trades = [calc.Trade.from_row(r) for r in ledger.trades()]
    frames = {t: load_prices(prices, t) for t in sorted({t.ticker for t in trades})}
    benchmark = optional_prices(prices, calc.BENCHMARK) if trades else None
    return calc.performance(trades, ledger.starting_cash(), frames, benchmark)
