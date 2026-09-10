"""Office -> PDF conversion, by driving Microsoft Office through AppleScript.

This is the piece that used to be a two-machine protocol. The Pi held the API
and shipped Office files to the iMac over SMB, then polled a shared outbox for
the PDF to come back. With the API running on the iMac itself, that whole relay
collapses into one subprocess call.

Shaped like cups.py: a thin, blocking subprocess wrapper with a settings-driven
binary override so the tests never launch Word.
"""
from __future__ import annotations

import fcntl
import logging
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import settings

log = logging.getLogger(__name__)

SCRIPT = Path(__file__).parent / "scripts" / "convert-to-pdf.applescript"

# Presence of the .app is what makes a format convertible. Checked for /health
# only -- the conversion itself just tries and reports what AppleScript says.
OFFICE_APPS = {
    "Word": "/Applications/Microsoft Word.app",
    "Excel": "/Applications/Microsoft Excel.app",
    "PowerPoint": "/Applications/Microsoft PowerPoint.app",
}


class ConversionError(RuntimeError):
    """An Office file could not be turned into a PDF.

    The message reaches the student as the job's error text, so it is written
    for them, not for a log reader.
    """


def _osascript() -> str:
    if settings.office_convert_binary:
        return settings.office_convert_binary
    return shutil.which("osascript") or "/usr/bin/osascript"


@contextmanager
def _conversion_lock() -> Iterator[None]:
    """Serialize conversions across every uvicorn worker process.

    AppleScript drives one shared GUI instance of Word, Excel or PowerPoint --
    two conversions at once fight over the same application and one of them
    loses its document. An flock works across processes, which a threading.Lock
    would not: uvicorn runs 4 of them.

    The wait is unbounded on purpose; each holder is already bounded by the
    subprocess timeout below, so the queue drains on its own.
    """
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = settings.data_dir / "convert.lock"
    with open(lock_path, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def convert_to_pdf(src: str | Path, dst: str | Path) -> None:
    """Convert one Office document to PDF at `dst`. Raises ConversionError."""
    src = Path(src)
    dst = Path(dst)

    if not SCRIPT.exists():
        raise ConversionError("the document converter is not installed on the server")
    if not src.exists():
        raise ConversionError("the uploaded file is no longer on the server")

    args = [_osascript(), str(SCRIPT), str(src), str(dst)]
    log.info("converting %s -> %s", src.name, dst.name)

    with _conversion_lock():
        try:
            proc = subprocess.run(
                args, capture_output=True, text=True,
                timeout=settings.office_convert_timeout_s,
            )
        except subprocess.TimeoutExpired:
            _discard(dst)
            raise ConversionError(
                "converting this document took too long — it may be very large, "
                "or Office may be showing a dialog on the print server"
            ) from None
        except (FileNotFoundError, OSError) as e:
            raise ConversionError(f"the document converter could not run: {e}") from e

    out = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        _discard(dst)
        raise ConversionError(_friendly(out))
    # AppleScript can exit 0 having quietly written nothing.
    if not dst.exists() or dst.stat().st_size == 0:
        _discard(dst)
        raise ConversionError(
            "the converter finished but produced no PDF — please export the "
            "document to PDF and print that instead"
        )


def _discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _friendly(osascript_output: str) -> str:
    """Turn AppleScript's error text into something a student can act on."""
    text = osascript_output or "unknown error"
    low = text.lower()
    if "-1743" in text or "not allowed to send apple events" in low or "not authorized" in low:
        return (
            "the print server is not yet allowed to control Microsoft Office — "
            "an administrator needs to approve it in Privacy & Security → Automation"
        )
    if "-1728" in text or "-600" in text or "can’t get application" in low or "can't get application" in low:
        return "Microsoft Office is not running or not installed on the print server"
    if "unsupported extension" in low:
        return "this document type can't be converted — please export it to PDF instead"
    # Keep some of the real text: it is the only clue when something new breaks.
    return f"the document could not be converted ({text.splitlines()[-1][:200]})"


def has_gui_session() -> bool:
    """True when this process is in a logged-in Aqua session.

    A LaunchDaemon (or ssh) is in a background session, where Office cannot
    launch at all. Reported through /health because the symptom otherwise is
    that PDFs print and every Office file fails, which looks like a converter
    bug rather than a login setting.
    """
    try:
        proc = subprocess.run(
            ["/bin/launchctl", "managername"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False
    return proc.stdout.strip() == "Aqua"


def office_status() -> dict:
    """Everything /health needs to say whether Office printing will work."""
    apps = {name: Path(p).exists() for name, p in OFFICE_APPS.items()}
    script_present = SCRIPT.exists()
    runner_present = bool(settings.office_convert_binary) or shutil.which("osascript") is not None
    return {
        "enabled": settings.office_conversion_enabled,
        "available": (
            settings.office_conversion_enabled
            and script_present
            and runner_present
            and any(apps.values())
        ),
        "gui_session": has_gui_session(),
        "script_present": script_present,
        "apps": apps,
    }
