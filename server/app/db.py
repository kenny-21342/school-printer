"""SQLite database layer."""
from __future__ import annotations

import fcntl
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import settings

_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    display_name  TEXT,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    INTEGER NOT NULL,
    last_login    INTEGER,
    token_version INTEGER NOT NULL DEFAULT 0,
    failed_logins INTEGER NOT NULL DEFAULT 0,
    lockout_until INTEGER
)
"""

# Kept as a single standalone statement so the migration can recreate just this
# table with conn.execute(). It must NOT go through executescript(), which
# implicitly commits and would break the surrounding transaction.
_JOBS_TABLE = """
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
    -- Set when a worker takes ownership of an Office file's conversion. The
    -- claim is what keeps four uvicorn processes from converting the same job
    -- four times; a claim older than conversion_claim_stale_s means the process
    -- died mid-conversion and the job is fair game again.
    conversion_claimed_at INTEGER,
    -- Nullable: cleared once the spool file has been reaped (C3). A job row
    -- outlives the uploaded file it was created from.
    spool_path      TEXT,
    created_at      INTEGER NOT NULL,
    printed_at      INTEGER,
    FOREIGN KEY (user_id) REFERENCES users(id)
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)",
)


@contextmanager
def startup_lock() -> Iterator[None]:
    """Serialize schema creation and admin bootstrap across worker processes.

    uvicorn starts four workers at once and every one of them imports the app,
    so all four ran init_db() simultaneously against a brand-new database. Two
    of them died with "database is locked" -- the migration takes BEGIN
    IMMEDIATE and `PRAGMA journal_mode=WAL` wants a brief exclusive lock, which
    SQLite refuses outright rather than waiting. The service came up on the
    surviving workers, so this looked healthy from outside while running at half
    capacity.
    """
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with open(settings.data_dir / "startup.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def init_db() -> None:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.execute(_USERS_TABLE)
        conn.execute(_JOBS_TABLE)
        for stmt in _INDEXES:
            conn.execute(stmt)
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
        "conversion_claimed_at": "INTEGER",
    }
    for col, decl in additions.items():
        if col not in cols:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {decl}")

    # users.token_version — bumped on password change so existing JWTs stop
    # being accepted (H8). Existing rows default to 0, matching tokens that
    # carry no "tv" claim.
    ucols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    user_additions = {
        "token_version": "INTEGER NOT NULL DEFAULT 0",
        # Per-username login throttling (H1). IP-based limiting alone is
        # useless behind school NAT and is bypassable via X-Forwarded-For.
        "failed_logins": "INTEGER NOT NULL DEFAULT 0",
        "lockout_until": "INTEGER",
    }
    for col, decl in user_additions.items():
        if col not in ucols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} {decl}")

    # The /pdf-info and /preview endpoints are gone, and with them this table.
    # It only ever held bookkeeping for preview uploads -- no job history -- so
    # dropping it loses nothing. The files themselves are swept by sweep_spool's
    # orphan pass.
    conn.execute("DROP TABLE IF EXISTS preview_files")

    _relax_spool_path_not_null(conn)


def _relax_spool_path_not_null(conn) -> None:
    """Make jobs.spool_path nullable on databases created before the reaper.

    The original schema declared it NOT NULL, which blocks clearing the path
    once the file has been deleted. SQLite cannot ALTER a column's constraints,
    so this does the standard table rebuild — but only when needed, and inside
    a transaction so a crash mid-migration cannot lose job history.
    """
    info = conn.execute("PRAGMA table_info(jobs)").fetchall()
    spool = next((r for r in info if r[1] == "spool_path"), None)
    if spool is None or not spool[3]:  # r[3] == notnull flag
        return

    collist = ", ".join(r[1] for r in info)
    # foreign_keys must be toggled OUTSIDE the transaction — it is a no-op while
    # one is active.
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("ALTER TABLE jobs RENAME TO jobs_old")
            conn.execute(_JOBS_TABLE)
            conn.execute(f"INSERT INTO jobs ({collist}) SELECT {collist} FROM jobs_old")
            conn.execute("DROP TABLE jobs_old")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        # Indexes were dropped along with the old table; recreate them.
        for stmt in _INDEXES:
            conn.execute(stmt)
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Yield a short-lived SQLite connection.

    There is deliberately no application-level lock here. The previous version
    held one module-global threading.Lock for the entire duration of the `with`
    block, which serialized every request in the process — and in refresh_job
    that block contained two `lpstat` subprocess calls with 10s timeouts, so a
    slow printer query blocked all database access server-wide for up to 20s.
    A threading.Lock is also useless now that uvicorn runs multiple worker
    *processes*.

    WAL mode already allows concurrent readers alongside a single writer, and
    `timeout` makes writers wait on SQLite's own lock rather than erroring out.

    Callers must keep the block short and must NOT perform subprocess or
    network I/O inside it.
    """
    conn = sqlite3.connect(
        settings.db_path,
        timeout=30.0,
        isolation_level=None,  # autocommit; we manage tx explicitly
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    # WAL is a property of the database file, so it survives once any connection
    # has set it. Another process holding a write lock makes this fail
    # immediately rather than waiting on busy_timeout, and that is not worth
    # killing a worker over.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
    finally:
        conn.close()
