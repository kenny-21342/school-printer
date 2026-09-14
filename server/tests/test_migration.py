"""Migration tests.

There is a live database created by the original schema (carried over from the
Pi when the pipeline moved to the iMac). These tests build a database with that
exact old shape and assert that init_db() upgrades it in place without losing
rows.
"""
from __future__ import annotations

import sqlite3
import sys
import time

import pytest

# The schema exactly as it shipped before this work: jobs.spool_path NOT NULL,
# no token_version / failed_logins / lockout_until.
LEGACY_SCHEMA = """
CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    display_name  TEXT,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    INTEGER NOT NULL,
    last_login    INTEGER
);
CREATE TABLE jobs (
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
"""


@pytest.fixture()
def legacy_db(tmp_path, monkeypatch):
    data = tmp_path / "data"
    spool = tmp_path / "spool"
    data.mkdir(); spool.mkdir()
    db = data / "printers.db"

    conn = sqlite3.connect(db)
    conn.executescript(LEGACY_SCHEMA)
    now = int(time.time())
    conn.execute(
        "INSERT INTO users (username, password_hash, display_name, is_admin, is_active, created_at)"
        " VALUES ('legacy', 'x', 'Legacy User', 1, 1, ?)", (now,))
    for i in range(3):
        conn.execute(
            "INSERT INTO jobs (user_id, filename, size_bytes, status, spool_path, created_at)"
            " VALUES (1, ?, 100, 'done', ?, ?)",
            # The old /opt path is deliberate: these rows are what the Pi's
            # database actually contained before the move to the iMac.
            (f"old{i}.pdf", f"/opt/school-printer/spool/old{i}.pdf", now))
    conn.commit()
    conn.close()

    monkeypatch.setenv("SP_DATA_DIR", str(data))
    monkeypatch.setenv("SP_SPOOL_DIR", str(spool))
    monkeypatch.setenv("SP_DB_PATH", str(db))
    monkeypatch.setenv("SP_BOOTSTRAP_ADMIN_PASS", "")
    for mod in [m for m in list(sys.modules) if m == "app" or m.startswith("app.")]:
        del sys.modules[mod]
    yield db
    for mod in [m for m in list(sys.modules) if m == "app" or m.startswith("app.")]:
        del sys.modules[mod]


def test_legacy_db_upgrades_without_data_loss(legacy_db):
    from app.db import init_db, get_conn

    init_db()

    with get_conn() as conn:
        # No rows lost by the jobs table rebuild.
        assert conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == 3
        assert conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] == 1
        names = {r["filename"] for r in conn.execute("SELECT filename FROM jobs")}
        assert names == {"old0.pdf", "old1.pdf", "old2.pdf"}

        # spool_path is now nullable, so the reaper can clear it.
        info = conn.execute("PRAGMA table_info(jobs)").fetchall()
        spool = next(r for r in info if r[1] == "spool_path")
        assert not spool[3], "spool_path should no longer be NOT NULL"
        conn.execute("UPDATE jobs SET spool_path = NULL WHERE id = 1")

        # New user columns exist with sane defaults for pre-existing rows.
        u = conn.execute("SELECT * FROM users WHERE username='legacy'").fetchone()
        assert u["token_version"] == 0
        assert u["failed_logins"] == 0
        assert u["lockout_until"] is None

        # The conversion worker's claim column exists and is empty on old rows,
        # so a job restored from a pre-conversion database is claimable.
        j = conn.execute("SELECT * FROM jobs WHERE filename='old0.pdf'").fetchone()
        assert j["conversion_claimed_at"] is None

        # preview_files is dropped: the endpoints that used it are gone.
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "preview_files" not in tables


def test_migration_is_idempotent(legacy_db):
    from app.db import init_db, get_conn

    init_db()
    init_db()
    init_db()

    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == 3
        # the rebuild must not have left the temp table behind
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "jobs_old" not in tables


def test_legacy_token_without_tv_claim_still_works(legacy_db):
    """Tokens minted before token_version existed carry no 'tv' claim.

    They must keep working (tv defaults to 0, matching the column default),
    otherwise every student is logged out the moment this deploys.
    """
    from fastapi.testclient import TestClient
    # Importing app.main runs create_app(), which calls settings.ensure_dirs()
    # and so loads the JWT signing key. Tokens must be minted after this.
    from app.main import app
    from app.auth import create_access_token, hash_password, get_user_by_username
    from app.db import get_conn

    with get_conn() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE username='legacy'",
                     (hash_password("legacypass1"),))
    user = get_user_by_username("legacy")

    # A token in the OLD format: no "tv" claim at all.
    old_token = create_access_token(str(user["id"]), {"u": "legacy", "a": True})

    with TestClient(app) as c:
        r = c.get("/users/me", headers={"Authorization": f"Bearer {old_token}"})
        assert r.status_code == 200, r.text
        assert r.json()["username"] == "legacy"
