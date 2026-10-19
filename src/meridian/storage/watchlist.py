from datetime import datetime, timezone

from meridian.storage.db import Database


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Watchlist:
    def __init__(self, db: Database):
        self.db = db

    def list(self) -> list[dict]:
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT ticker, note, added_at FROM watchlist ORDER BY ticker"
            ).fetchall()
        return [dict(r) for r in rows]

    def get(self, ticker: str) -> dict | None:
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT ticker, note, added_at FROM watchlist WHERE ticker = ?", (ticker,)
            ).fetchone()
        return dict(row) if row else None

    def upsert(self, ticker: str, note: str = "") -> tuple[dict, bool]:
        """Add a ticker or update its note. Returns (item, created)."""
        created = self.get(ticker) is None
        with self.db.connection() as conn:
            conn.execute(
                "INSERT INTO watchlist (ticker, note, added_at) VALUES (?, ?, ?) "
                "ON CONFLICT(ticker) DO UPDATE SET note = excluded.note",
                (ticker, note, _now()),
            )
        return self.get(ticker), created

    def remove(self, ticker: str) -> bool:
        with self.db.connection() as conn:
            cur = conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker,))
        return cur.rowcount > 0
