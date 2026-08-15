"""CUPS subprocess wrapper."""
from __future__ import annotations

import logging
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


def _lp() -> str:
    if settings.lp_binary:
        return settings.lp_binary
    found = shutil.which("lp")
    if not found:
        raise RuntimeError("lp binary not found")
    return found


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
) -> tuple[int, str]:
    """Submit a file to the CUPS queue.

    Returns (cups_job_id, raw_lp_output). Raises RuntimeError on failure.
    """
    ps = settings_obj or PrintSettings()
    lp = _lp()
    args = [
        lp,
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
    # layout direction (N-up reading order)
    if ps.layout_direction in ("ltr", "rtl"):
        args += ["-o", f"cupsBackSide={ps.layout_direction}"]

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
    import re
    # matches "bw-42" or any "<queue>-<digits>"
    m = re.search(r"(\d+)", lp_output)
    if not m:
        return None
    return int(m.group(1))


def cancel_job(job_id: int) -> bool:
    cancel = shutil.which("cancel") or "cancel"
    proc = subprocess.run(
        [cancel, "-a", f"{settings.cups_queue}-{job_id}"],
        capture_output=True, text=True, timeout=10,
    )
    return proc.returncode == 0


def get_job_state(job_id: int) -> str:
    """Return current state of a CUPS job: pending|processing|done|cancelled|error|unknown."""
    lpstat = shutil.which("lpstat") or "lpstat"
    proc = subprocess.run(
        ["lpstat", "-o", settings.cups_queue, "-W", "not-completed"],
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
        ["lpstat", "-o", settings.cups_queue, "-W", "completed"],
        capture_output=True, text=True, timeout=10,
    )
    for line in proc2.stdout.splitlines():
        parts = line.split()
        if parts and parts[0] == f"{settings.cups_queue}-{job_id}":
            return "done"
    return "unknown"
