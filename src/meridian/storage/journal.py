from datetime import date, datetime, timezone

from meridian.storage.db import Database

DECISIONS = ("buy", "sell", "hold", "watch", "pass")
COLUMNS = (
    "id, ticker, decision, thesis, conviction, price, review_on, "
    "created_at, outcome_note, reviewed_at"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Journal:
    """Decision journal. The thesis is written once and never edited, so a later
    review can't be rewritten with hindsight; only an outcome note is added."""

    def __init__(self, db: Database):
        self.db = db

    def add(
        self,
        ticker: str,
        decision: str,
        thesis: str,
        conviction: int | None = None,
        price: float | None = None,
        review_on: date | None = None,
    ) -> dict:
        with self.db.connection() as conn:
            cur = conn.execute(
                "INSERT INTO journal_entries "
                "(ticker, decision, thesis, conviction, price, review_on, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    ticker,
                    decision,
                    thesis,
                    conviction,
                    price,
                    review_on.isoformat() if review_on else None,
                    _now(),
                ),
            )
            entry_id = cur.lastrowid
        return self.get(entry_id)

    def get(self, entry_id: int) -> dict | None:
        with self.db.connection() as conn:
            row = conn.execute(
                f"SELECT {COLUMNS} FROM journal_entries WHERE id = ?", (entry_id,)
            ).fetchone()
        return dict(row) if row else None

    def list(self, ticker: str | None = None, due_only: bool = False, today: date | None = None) -> list[dict]:
        """Newest first. due_only keeps entries whose review date has arrived and
        that have no outcome note yet."""
        clauses, params = [], []
        if ticker:
            clauses.append("ticker = ?")
            params.append(ticker)
        if due_only:
            clauses.append("review_on IS NOT NULL AND review_on <= ? AND outcome_note IS NULL")
            params.append((today or date.today()).isoformat())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.db.connection() as conn:
            rows = conn.execute(
                f"SELECT {COLUMNS} FROM journal_entries {where} "
                "ORDER BY created_at DESC, id DESC",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def review(self, entry_id: int, outcome_note: str) -> dict | None:
        with self.db.connection() as conn:
            cur = conn.execute(
                "UPDATE journal_entries SET outcome_note = ?, reviewed_at = ? WHERE id = ?",
                (outcome_note, _now(), entry_id),
            )
        return self.get(entry_id) if cur.rowcount else None

    def delete(self, entry_id: int) -> bool:
        with self.db.connection() as conn:
            cur = conn.execute("DELETE FROM journal_entries WHERE id = ?", (entry_id,))
        return cur.rowcount > 0
