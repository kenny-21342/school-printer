"""Office -> PDF with Microsoft Office on macOS or headless LibreOffice on Linux."""
from __future__ import annotations

import fcntl
import logging
import os
import signal
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import settings

log = logging.getLogger(__name__)

SCRIPT = Path(__file__).parent / "scripts" / "convert-to-pdf.applescript"

# Presence of the .app is what makes a format convertible. Checked for /health
# only -- the conversion itself just tries and reports what AppleScript says.
# Office writes one of these once it has been activated. Neither existing means
# the apps have never been through first-run sign-in -- and an unactivated Word
# does not fail, it *hangs*: it blocks in Office's setup wizard waiting for
# someone to click through it, so every conversion burns the full timeout and
# reports "took too long". Worth reporting up front.
LICENCE_MARKERS = (
    "/Library/Preferences/com.microsoft.office.licensingV2.plist",
    "~/Library/Group Containers/UBF8T346G9.Office/Licenses",
    "~/Library/Group Containers/UBF8T346G9.Office/com.microsoft.Office365.plist",
)

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


def backend() -> str:
    if settings.office_backend != "auto":
        return settings.office_backend
    return "applescript" if sys.platform == "darwin" else "libreoffice"


def _libreoffice() -> str | None:
    return settings.office_convert_binary or shutil.which("libreoffice") or shutil.which("soffice")


def _convert_libreoffice(src: Path, dst: Path) -> None:
    binary = _libreoffice()
    if not binary:
        raise ConversionError("LibreOffice is not installed on the print server — please export to PDF")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    # A fresh profile avoids connecting to the user's desktop LibreOffice, and
    # a fresh output directory prevents a stale PDF from appearing successful.
    with tempfile.TemporaryDirectory(prefix="convert-", dir=settings.data_dir) as work:
        profile = Path(work, "profile").resolve()
        profile.mkdir()
        (profile / "user").mkdir()
        (profile / "user" / "registrymodifications.xcu").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
            '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
            '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>'
            '</item></oor:items>'
        )
        outdir = Path(work, "output")
        outdir.mkdir()
        args = [binary, f"-env:UserInstallation={profile.as_uri()}", "--headless",
                "--nologo", "--nodefault", "--norestore", "--convert-to", "pdf",
                "--outdir", str(outdir), str(src.resolve())]
        # LibreOffice launches child processes. Kill the entire conversion on
        # timeout so an abandoned child cannot keep consuming resources.
        try:
            with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, start_new_session=True,
                                  env={**os.environ, "SAL_USE_VCLPLUGIN": "svp"}) as proc:
                try:
                    stdout, stderr = proc.communicate(timeout=settings.office_convert_timeout_s)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.communicate()
                    raise ConversionError("converting this document took too long — please export it to PDF") from None
        except OSError as e:
            raise ConversionError(f"the document converter could not run: {e}") from e
        output = (stdout + stderr).strip()
        generated = outdir / f"{src.stem}.pdf"
        if proc.returncode != 0:
            raise ConversionError(f"LibreOffice could not convert this document ({output[-200:] or 'unknown error'})")
        if not generated.is_file() or generated.stat().st_size == 0:
            raise ConversionError("the converter produced no PDF — please export the document to PDF and print that instead")
        with generated.open("rb") as fh:
            if not fh.read(5).startswith(b"%PDF-"):
                raise ConversionError("the converter produced an invalid PDF — please export the document to PDF")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(generated), dst)


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

    if not src.exists():
        raise ConversionError("the uploaded file is no longer on the server")
    if backend() == "libreoffice":
        with _conversion_lock():
            try:
                _convert_libreoffice(src, dst)
            except ConversionError:
                _discard(dst)
                raise
        return
    if not SCRIPT.exists():
        raise ConversionError("the document converter is not installed on the server")

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
            hint = (
                " Office does not look activated on the print server, which is "
                "the usual cause: an unsigned-in Word blocks in its setup "
                "wizard instead of converting."
                if not office_licensed() else
                " Office may be showing a dialog on the print server."
            )
            raise ConversionError(
                "converting this document took too long — it may be very large."
                + hint
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


def office_licensed() -> bool:
    """Whether Office looks activated. See LICENCE_MARKERS."""
    return any(Path(m).expanduser().exists() for m in LICENCE_MARKERS)


def office_status() -> dict:
    """Everything /health needs to say whether Office printing will work."""
    if backend() == "libreoffice":
        binary = _libreoffice()
        present = bool(binary and shutil.which(binary))
        return {
            "backend": "libreoffice",
            "enabled": settings.office_conversion_enabled,
            "available": settings.office_conversion_enabled and present,
            "gui_session": False,
            "requires_gui": False,
            "script_present": False,
            "licensed": False,
            "apps": {"LibreOffice": present},
        }
    apps = {name: Path(p).exists() for name, p in OFFICE_APPS.items()}
    script_present = SCRIPT.exists()
    runner_present = bool(settings.office_convert_binary) or shutil.which("osascript") is not None
    licensed = office_licensed()
    return {
        "backend": "applescript",
        "requires_gui": True,
        "enabled": settings.office_conversion_enabled,
        # `licensed` is part of "available" deliberately: an installed but
        # unactivated Word converts nothing, it just hangs for the full timeout.
        "available": (
            settings.office_conversion_enabled
            and script_present
            and runner_present
            and any(apps.values())
            and licensed
        ),
        "gui_session": has_gui_session(),
        "script_present": script_present,
        "licensed": licensed,
        "apps": apps,
    }
