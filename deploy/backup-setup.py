#!/usr/bin/env python3
"""Archive deployed state before replacing the host OS; no service changes.

Run on the deployed iMac, optionally with sudo to include protected CUPS files:
  python3 deploy/backup-setup.py --install-dir ~/school-printer
  sudo python3 deploy/backup-setup.py --install-dir /Users/<user>/school-printer \
    --output-dir /Users/<user>/school-printer-backups
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile


def snapshot_database(source: Path, destination: Path) -> None:
    with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as src:
        with sqlite3.connect(destination) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError(f"Database integrity check failed: {source}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-dir", type=Path, default=Path.home() / "school-printer")
    parser.add_argument("--output-dir", type=Path, default=Path.home() / "school-printer-backups")
    parser.add_argument("--data-dir", type=Path, help="Override if SP_DATA_DIR differs from <install-dir>/data")
    parser.add_argument("--db-path", type=Path, help="Override if SP_DB_PATH differs from <data-dir>/printers.db")
    args = parser.parse_args()
    base = args.install_dir.expanduser().resolve()
    data = (args.data_dir or base / "data").expanduser().resolve()
    db = (args.db_path or data / "printers.db").expanduser().resolve()
    if not base.is_dir() or not db.is_file():
        parser.error("No deployed database found; set --install-dir (or --data-dir / --db-path).")
    output = args.output_dir.expanduser().resolve()
    if output == base or base in output.parents:
        parser.error("Keep backups outside the installation directory.")
    os.umask(0o077)
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    archive = output / f"school-printer-setup-{stamp}.tar.gz"
    warnings, included = [], []
    with tempfile.TemporaryDirectory(prefix="school-printer-backup-", dir=output) as staging:
        stage = Path(staging)
        (stage / "relay" / "data").mkdir(parents=True)
        # The backup API includes WAL commits safely while the service runs.
        # Raw database/WAL files are deliberately not copied.
        snapshot_database(db, stage / "relay" / "data" / "printers.db")

        def copy(source: Path, relative: str, *, required: bool = False) -> None:
            if not source.exists():
                if required:
                    raise FileNotFoundError(f"Required backup file missing: {source}")
                return
            target = stage / relative
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                if source.is_dir():
                    shutil.copytree(source, target, dirs_exist_ok=True, symlinks=True,
                                    ignore=shutil.ignore_patterns("*.db", "*.db-wal", "*.db-shm",
                                        "*.sqlite", "*.sqlite3", ".venv", "venv", "__pycache__", ".pytest_cache"))
                else:
                    shutil.copy2(source, target)
                included.append(relative)
            except (OSError, shutil.Error) as e:
                if required:
                    raise
                warnings.append(f"Could not fully copy {source}: {e}")

        copy(data, "relay/data", required=True)
        copy(base / ".env", "relay/.env", required=True)
        copy(base / "server", "relay/server")
        copy(base / "spool", "relay/spool")
        copy(base / "airprint-shim", "relay/airprint-shim")
        copy(base / "logs", "relay/logs")
        copy(base.parent / "Library/LaunchAgents/com.school.printer.plist", "macos/com.school.printer.plist")
        copy(base.parent / "Library/LaunchAgents/com.school.airprint.plist", "macos/com.school.airprint.plist")
        copy(base.parent / "Desktop/School Print.app", "macos/School Print.app")
        copy(base.parent / ".local/bin/school-print", "macos/school-print")
        copy(Path("/etc/cups"), "system/cups")
        copy(Path("/Library/Printers/FUJIFILM"), "macos/FUJIFILM")
        copy(Path("/Users/Shared/school-printer-drivers"), "macos/staged-drivers")
        for tool, command in {"queues.txt": ["lpstat", "-t"], "options.txt": ["lpoptions", "-p", "bw", "-l"],
                              "cups-settings.txt": ["cupsctl"]}.items():
            try:
                proc = subprocess.run(command, capture_output=True, text=True, timeout=10)
                (stage / tool).write_text(proc.stdout + proc.stderr)
            except (OSError, subprocess.TimeoutExpired) as e:
                warnings.append(f"Could not capture {command[0]}: {e}")
        (stage / "MANIFEST.json").write_text(json.dumps({
            "created_utc": stamp, "install_dir": str(base), "data_dir": str(data),
            "db_path": str(db), "database_snapshot": "relay/data/printers.db",
            "included": included, "warnings": warnings,
            "note": "Stop services before a final migration backup to freeze in-flight jobs. macOS drivers/config are archival; do not install them on Linux.",
        }, indent=2))
        try:
            with tarfile.open(archive, "w:gz") as tar:
                for child in sorted(stage.iterdir()):
                    tar.add(child, arcname=child.name)
        except Exception:
            archive.unlink(missing_ok=True)
            raise
    print(f"Backup: {archive}")
    print("Contains credentials and student data. Copy it off the iMac before erasing macOS.")
    for warning in warnings:
        print(f"WARNING: {warning}")
    return 1 if warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
