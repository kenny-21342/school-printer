"""SQLite database layer."""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import settings

_lock = threading.Lock()
_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    display_name  TEXT,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    INTEGER NOT NULL,
    last_login    INTEGER
);

CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    filename        TEXT NOT NULL,
    mime_type       TEXT,
    size_bytes      INTEGER NOT NULL,
    copies          INTEGER NOT NULL DEFAULT 1,
    duplex          INTEGER NOT NULL DEFAULT 0,
    color_mode      TEXT NOT NULL DEFAULT 'monochrome',
    page_range      TEXT,
    paper_size      TEXT,
    orientation     TEXT,
    scaling         TEXT,
    layout_direction TEXT,
    cups_job_id     INTEGER,
    status          TEXT NOT NULL DEFAULT 'queued',
    error           TEXT,
    needs_conversion INTEGER NOT NULL DEFAULT 0,
    spool_path      TEXT NOT NULL,
    created_at      INTEGER NOT NULL,
    printed_at      INTEGER,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""


def init_db() -> None:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(_SCHEMA)
        _migrate(conn)


def _migrate(conn) -> None:
    """Add columns that may be missing if the DB predates them."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    additions = {
        "page_range": "TEXT",
        "paper_size": "TEXT",
        "orientation": "TEXT",
        "scaling": "TEXT",
        "layout_direction": "TEXT",
        "needs_conversion": "INTEGER NOT NULL DEFAULT 0",
    }
    for col, decl in additions.items():
        if col not in cols:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {decl}")
        conn.commit()


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(
        settings.db_path,
        timeout=30.0,
        isolation_level=None,  # autocommit; we manage tx explicitly
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        with _lock:
            yield conn
    finally:
        conn.close()
