from datetime import date, datetime, timezone

from meridian.storage.db import Database

COLUMNS = "id, ticker, side, shares, price, trade_date, requested_date, note, created_at"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PaperLedger:
    """Storage for the paper account and its trades. Rules about which trades are
    allowed live in meridian.analysis.paper, not here."""

    def __init__(self, db: Database):
        self.db = db

    def starting_cash(self) -> float:
        with self.db.connection() as conn:
            return float(conn.execute("SELECT starting_cash FROM paper_account WHERE id = 1").fetchone()[0])

    def set_starting_cash(self, amount: float) -> bool:
        """Only allowed while the ledger is empty, since it would rewrite history."""
        with self.db.connection() as conn:
            if conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]:
                return False
            conn.execute("UPDATE paper_account SET starting_cash = ? WHERE id = 1", (amount,))
        return True

    def trades(self, ticker: str | None = None) -> list[dict]:
        """Oldest first, in the order they take effect."""
        where, params = ("WHERE ticker = ?", (ticker,)) if ticker else ("", ())
        with self.db.connection() as conn:
            rows = conn.execute(
                f"SELECT {COLUMNS} FROM paper_trades {where} ORDER BY trade_date, id", params
            ).fetchall()
        return [dict(r) for r in rows]

    def get(self, trade_id: int) -> dict | None:
        with self.db.connection() as conn:
            row = conn.execute(
                f"SELECT {COLUMNS} FROM paper_trades WHERE id = ?", (trade_id,)
            ).fetchone()
        return dict(row) if row else None

    def add(
        self,
        ticker: str,
        side: str,
        shares: float,
        price: float,
        trade_date: date,
        requested_date: date,
        note: str = "",
    ) -> dict:
        with self.db.connection() as conn:
            cur = conn.execute(
                "INSERT INTO paper_trades "
                "(ticker, side, shares, price, trade_date, requested_date, note, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (ticker, side, shares, price, trade_date.isoformat(),
                 requested_date.isoformat(), note, _now()),
            )
            trade_id = cur.lastrowid
        return self.get(trade_id)

    def delete(self, trade_id: int) -> bool:
        with self.db.connection() as conn:
            cur = conn.execute("DELETE FROM paper_trades WHERE id = ?", (trade_id,))
        return cur.rowcount > 0
