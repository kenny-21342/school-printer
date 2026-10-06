"""CUPS subprocess wrapper."""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass

from .config import settings

log = logging.getLogger(__name__)


@dataclass
class CupsJob:
    job_id: int
    queue: str
    state: str  # pending, processing, done, cancelled, error
    raw: str


_OWNER_RE = re.compile(r"[A-Za-z0-9._-]{2,64}")


def _lp() -> str:
    if settings.lp_binary:
        return settings.lp_binary
    found = shutil.which("lp")
    if not found:
        raise RuntimeError("lp binary not found")
    return found


def _tool(name: str) -> str:
    """Resolve a CUPS CLI tool to an absolute path.

    launchd gives the service a minimal PATH, so callers must use the resolved
    path rather than a bare name. Falls back to the bare name (and lets the
    subprocess call fail loudly) if the tool is not on PATH at all.
    """
    return shutil.which(name) or name


@dataclass
class PrintSettings:
    copies: int = 1
    duplex: bool = False
    color_mode: str = "monochrome"
    page_range: str | None = None       # "1-3,5,7-9" or None = all
    paper_size: str | None = None       # "A4", "Letter", "A3", ...
    orientation: str | None = None      # "portrait" | "landscape" | "auto"
    scaling: str | None = None          # "fit" | "actual" | "shrink" | custom like "80"
    layout_direction: str | None = None # "ltr" | "rtl" (reading order / N-up direction)


# Mapping paper-size -> IPP media name. The FUJIFILM printer should accept these.
PAPER_SIZES = {
    "A4": "iso_a4_210x297mm",
    "A3": "iso_a3_297x420mm",
    "A5": "iso_a5_148x210mm",
    "Letter": "na_letter_8.5x11in",
    "Legal": "na_legal_8.5x14in",
    "Tabloid": "na_ledger_11x17in",
    "B5": "iso_b5_176x250mm",
}

ORIENTATION_VALUES = {
    "portrait": "3",   # IPP orientation-requested: 3 = portrait
    "landscape": "4",  # 4 = landscape
    "auto": None,      # omit
}

SCALING_VALUES = {
    "fit": "Fit-to-Page",
    "actual": "None",
    "shrink": "Shrink-to-Page",
}


def submit_job(
    spool_path: str,
    *,
    settings_obj: "PrintSettings | None" = None,
    job_title: str = "School Print",
    owner: str | None = None,
) -> tuple[int, str]:
    """Submit a file to the CUPS queue.

    Returns (cups_job_id, raw_lp_output). Raises RuntimeError on failure.
    """
    ps = settings_obj or PrintSettings()
    lp = _lp()
    args = [lp]
    if owner:
        # The campus system matches the job to a school account by this name.
        # Over the local socket CUPS would record the macOS account instead.
        if not _OWNER_RE.fullmatch(owner):
            raise RuntimeError(f"refusing to submit as {owner!r}")
        args += ["-h", settings.cups_server, "-U", owner]
    args += [
        "-d", settings.cups_queue,
        "-n", str(max(1, ps.copies)),
        "-t", job_title[:200],
        "-o", f"print-color-mode={ps.color_mode}",
    ]
    # duplex
    if ps.duplex:
        args += ["-o", "sides=two-sided-long-edge"]
    else:
        args += ["-o", "sides=one-sided"]
    # page range
    if ps.page_range and ps.page_range.strip():
        args += ["-o", f"page-ranges={ps.page_range.strip()}"]
    # paper size
    if ps.paper_size:
        media = PAPER_SIZES.get(ps.paper_size, ps.paper_size)
        args += ["-o", f"media={media}"]
    # orientation
    if ps.orientation and ps.orientation in ORIENTATION_VALUES:
        val = ORIENTATION_VALUES[ps.orientation]
        if val:
            args += ["-o", f"orientation-requested={val}"]
    # scaling
    if ps.scaling:
        if ps.scaling in SCALING_VALUES:
            args += ["-o", f"cupsPrintScaling={SCALING_VALUES[ps.scaling]}"]
        elif ps.scaling.isdigit():
            args += ["-o", f"cupsScaling={int(ps.scaling)}"]
    # Layout direction (N-up reading order).
    #
    # NOTE: this used to emit `cupsBackSide`, which is a PPD attribute
    # describing how the *printer* renders the back of a duplex sheet — it is
    # not a user-settable option and CUPS ignored it. The correct option is
    # number-up-layout. It only has an effect when more than one page is being
    # placed per sheet, and only on a queue with a real driver (not a raw queue).
    if ps.layout_direction in ("ltr", "rtl"):
        args += ["-o", f"number-up-layout={'lrtb' if ps.layout_direction == 'ltr' else 'rltb'}"]

    args.append(spool_path)

    log.info("submitting to CUPS: %s", " ".join(args))
    proc = subprocess.run(args, capture_output=True, text=True, timeout=60)
    out = (proc.stdout + proc.stderr).strip()

    if proc.returncode != 0:
        raise RuntimeError(f"lp failed (rc={proc.returncode}): {out}")

    # Output looks like: "request id is bw-42 (1 file)"  (also localized)
    cups_id = _parse_job_id(out)
    if cups_id is None:
        raise RuntimeError(f"could not parse job id from lp output: {out!r}")
    return cups_id, out


def _parse_job_id(lp_output: str) -> int | None:
    """Extract the numeric job id from lp's "request id is bw-42 (1 file)".

    Anchored on the queue name so a digit anywhere else in the (possibly
    localized) output cannot be mistaken for the job id. Falls back to a bare
    number only if the anchored form does not match.
    """
    import re
    m = re.search(rf"{re.escape(settings.cups_queue)}-(\d+)", lp_output)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(\d+)\b", lp_output)
    return int(m.group(1)) if m else None


def queue_options() -> list[str]:
    """Return the option keywords the `bw` queue advertises, via `lpoptions -l`.

    An EMPTY list means the queue is raw (no PPD/driver): CUPS will pass the
    file through untouched and silently ignore page-ranges, sides, media,
    orientation and scaling. That is the F1 finding in the audit plan — the
    print-options UI would be controlling nothing. Surfaced via /health so it
    can be checked without logging into the print server.
    """
    try:
        proc = subprocess.run(
            [_tool("lpoptions"), "-p", settings.cups_queue, "-l"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
    if proc.returncode != 0:
        return []
    keys = []
    for line in proc.stdout.splitlines():
        # lines look like "PageSize/Media Size: Letter *A4 A3"
        if "/" in line and ":" in line:
            keys.append(line.split("/", 1)[0].strip())
    return keys


def cancel_job(job_id: int) -> bool:
    """Cancel a single CUPS job.

    NOTE: this must NOT pass `-a`. In CUPS `cancel -a` means "cancel every job
    on the destination", so the previous `cancel -a bw-42` risked wiping the
    whole queue when one student cancelled one job.
    """
    proc = subprocess.run(
        [_tool("cancel"), f"{settings.cups_queue}-{job_id}"],
        capture_output=True, text=True, timeout=10,
    )
    return proc.returncode == 0


def get_job_state(job_id: int) -> str:
    """Return current state of a CUPS job: pending|processing|done|cancelled|error|unknown."""
    lpstat = _tool("lpstat")
    proc = subprocess.run(
        [lpstat, "-o", settings.cups_queue, "-W", "not-completed"],
        capture_output=True, text=True, timeout=10,
    )
    if proc.returncode != 0:
        return "unknown"
    # Output rows: "bw-42 kenny_lhs        1024   Mon Jul 7 20:00:00 2026"
    for line in proc.stdout.splitlines():
        parts = line.split()
        if parts and parts[0] == f"{settings.cups_queue}-{job_id}":
            return "pending" if "pending" in line.lower() else "processing"
    # not in not-completed list -> check completed
    proc2 = subprocess.run(
        [lpstat, "-o", settings.cups_queue, "-W", "completed"],
        capture_output=True, text=True, timeout=10,
    )
    for line in proc2.stdout.splitlines():
        parts = line.split()
        if parts and parts[0] == f"{settings.cups_queue}-{job_id}":
            return "done"
    return "unknown"
