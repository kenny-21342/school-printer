"""Test fixtures.

Every test runs against a throwaway SQLite DB and spool directory, with `lp`
replaced by a stub script so nothing is ever sent to a real printer. The
SP_LP_BINARY setting exists precisely for this.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

FAKE_LP = """#!/usr/bin/env bash
# Stub `lp`: records its argv and emits the output real lp produces.
echo "$@" >> "$LP_ARGV_LOG"
echo "request id is bw-42 (1 file)"
"""

FAKE_LP_FAIL = """#!/usr/bin/env bash
echo "lp: Error - scheduler not responding." >&2
exit 1
"""


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Configure settings via env vars and import a fresh app instance."""
    data = tmp_path / "data"
    spool = tmp_path / "spool"
    data.mkdir()
    spool.mkdir()

    lp = tmp_path / "fake_lp"
    lp.write_text(FAKE_LP)
    lp.chmod(lp.stat().st_mode | stat.S_IEXEC)

    argv_log = tmp_path / "lp_argv.log"
    argv_log.touch()

    monkeypatch.setenv("SP_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("SP_DATA_DIR", str(data))
    monkeypatch.setenv("SP_SPOOL_DIR", str(spool))
    monkeypatch.setenv("SP_DB_PATH", str(data / "test.db"))
    monkeypatch.setenv("SP_LP_BINARY", str(lp))
    monkeypatch.setenv("SP_BOOTSTRAP_ADMIN_PASS", "adminpass123")
    monkeypatch.setenv("SP_BOOTSTRAP_ADMIN_USER", "admin")
    monkeypatch.setenv("SP_SPOOL_RETENTION_HOURS", "24")
    monkeypatch.setenv("LP_ARGV_LOG", str(argv_log))

    # Drop any already-imported app modules so settings are re-read.
    for mod in [m for m in list(sys.modules) if m == "app" or m.startswith("app.")]:
        del sys.modules[mod]

    import app.main as main  # noqa: E402

    yield type("Env", (), {
        "app": main.app,
        "main": main,
        "tmp_path": tmp_path,
        "spool": spool,
        "argv_log": argv_log,
        "lp": lp,
    })

    for mod in [m for m in list(sys.modules) if m == "app" or m.startswith("app.")]:
        del sys.modules[mod]


@pytest.fixture()
def client(env):
    from fastapi.testclient import TestClient
    with TestClient(env.app) as c:
        c.env = env
        yield c


def _token(client, username="admin", password="adminpass123"):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture()
def admin_auth(client):
    return {"Authorization": f"Bearer {_token(client)}"}


@pytest.fixture()
def student_auth(client, admin_auth):
    """A non-admin user 'alice'."""
    r = client.post("/users", headers=admin_auth, json={
        "username": "alice", "password": "alicepass1", "display_name": "Alice",
    })
    assert r.status_code == 201, r.text
    tok = _token(client, "alice", "alicepass1")
    return {"Authorization": f"Bearer {tok}"}


MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)
