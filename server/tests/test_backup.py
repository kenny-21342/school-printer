"""The live migration archive must include committed WAL data and secrets."""
import importlib.util
from pathlib import Path
import sqlite3


def test_database_backup_includes_wal_commits_without_modifying_source(tmp_path):
    path = Path(__file__).resolve().parents[2] / "deploy" / "backup-setup.py"
    spec = importlib.util.spec_from_file_location("backup_setup", path)
    backup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backup)
    source, dest = tmp_path / "live.db", tmp_path / "snapshot.db"
    with sqlite3.connect(source) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE students (id TEXT)")
        conn.execute("INSERT INTO students VALUES ('alice')")
        conn.commit()
        backup.snapshot_database(source, dest)
        with sqlite3.connect(dest) as saved:
            assert saved.execute("SELECT id FROM students").fetchall() == [("alice",)]
            assert saved.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT id FROM students").fetchall() == [("alice",)]
