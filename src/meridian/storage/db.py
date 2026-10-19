"""SQLite storage: one small file, versioned schema, one connection per operation.

Schema changes are appended to MIGRATIONS and never edited: the database records
how many have run (PRAGMA user_version), so upgrading is just running the rest.
"""

import sqlite3
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from meridian.config import get_settings

MIGRATIONS: list[str] = [
    # 1: watchlist and decision journal
    """
    CREATE TABLE watchlist (
        ticker   TEXT PRIMARY KEY,
        note     TEXT NOT NULL DEFAULT '',
        added_at TEXT NOT NULL
    );

    CREATE TABLE journal_entries (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker             TEXT NOT NULL,
        decision           TEXT NOT NULL
            CHECK (decision IN ('buy', 'sell', 'hold', 'watch', 'pass')),
        thesis             TEXT NOT NULL,
        conviction         INTEGER CHECK (conviction BETWEEN 1 AND 5),
        price              REAL CHECK (price > 0),
        review_on          TEXT,
        created_at         TEXT NOT NULL,
        outcome_note       TEXT,
        reviewed_at        TEXT
    );
    CREATE INDEX idx_journal_ticker ON journal_entries (ticker, created_at);
    """,
    # 2: paper portfolio. The ledger of trades is the source of truth; cash, holdings
    # and performance are always recomputed from it.
    """
    CREATE TABLE paper_account (
        id            INTEGER PRIMARY KEY CHECK (id = 1),
        starting_cash REAL NOT NULL CHECK (starting_cash > 0)
    );
    INSERT INTO paper_account (id, starting_cash) VALUES (1, 100000);

    CREATE TABLE paper_trades (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker         TEXT NOT NULL,
        side           TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
        shares         REAL NOT NULL CHECK (shares > 0),
        price          REAL NOT NULL CHECK (price > 0),
        trade_date     TEXT NOT NULL,
        requested_date TEXT NOT NULL,
        note           TEXT NOT NULL DEFAULT '',
        created_at     TEXT NOT NULL
    );
    CREATE INDEX idx_paper_trades_date ON paper_trades (trade_date, id);
    """,
]


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._memory_conn: sqlite3.Connection | None = None
        self.migrate()

    def _open(self) -> sqlite3.Connection:
        # A private in-memory database vanishes with its connection, so reuse one.
        if self.path == ":memory:":
            if self._memory_conn is None:
                self._memory_conn = self._new_connection()
            return self._memory_conn
        return self._new_connection()

    def _new_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def connection(self):
        """Commit on success, roll back on error."""
        conn = self._open()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            if self.path != ":memory:":
                conn.close()

    def migrate(self) -> int:
        with self.connection() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > len(MIGRATIONS):
                raise RuntimeError(
                    f"database schema v{version} is newer than this code (v{len(MIGRATIONS)})"
                )
            for number in range(version, len(MIGRATIONS)):
                conn.executescript(MIGRATIONS[number])
                conn.execute(f"PRAGMA user_version = {number + 1}")
            return len(MIGRATIONS)

    def schema_version(self) -> int:
        with self.connection() as conn:
            return conn.execute("PRAGMA user_version").fetchone()[0]


@lru_cache
def get_database() -> Database:
    return Database(get_settings().db_path)
