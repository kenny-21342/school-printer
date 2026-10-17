"""Print straight to the campus printer: LPD with a PJL job header, no CUPS driver.

The wire format is the one paoprint speaks (github.com/PaoDevelopers/paokit,
BSD-2-Clause, copyright Runxi Yu 2024-2025): select the queue, send a control
file, send the data file, each step acknowledged by a zero byte. The data file is
a PJL header, PostScript, and a PJL end-of-job. The printer's vendor driver is
not needed, which matters because it exists only for macOS.

Differences from paoprint, on purpose:
  - No password. paoprint sends the user's school password (lightly obfuscated)
    inside the job; this never handles one. The campus system has accepted jobs
    identified by user name alone, which is also why a bearer token and a printed
    verification code, not a password, decide who may use a name here.
  - The job number rotates instead of being fixed.
  - Everything that lands in the control file or the PJL header is sanitised.
    A job name containing a newline or a quote would otherwise let a student add
    LPD or PJL lines of their own, including a second identity.

Unverified against the real printer (check with a printed page at school): the
one-sided value `SIMPLEX`, and that a black-and-white queue ignores `COLOR`.
"""
from __future__ import annotations

import fcntl
import re
import shutil
import socket
import subprocess
import tempfile
import unicodedata
from pathlib import Path

from .config import settings

UEL = b"\x1b%-12345X"                      # PJL "universal exit language"
EOJ = b"\x04\x1b%-12345X@PJL EOJ\n"
USER_RE = re.compile(r"[A-Za-z0-9._-]{2,64}")
PAGE_RANGE_RE = re.compile(r"(\d+)(?:-(\d+))?")
PAPER = {"A4": "A4", "A3": "A3", "Letter": "letter", "Legal": "legal"}   # pdftops -paper names


class LpdError(RuntimeError):
    """Printing failed; the message is written for the student who sees it."""


def local_host() -> str:
    name = socket.gethostname().split(".")[0]
    return re.sub(r"[^A-Za-z0-9-]", "", name)[:31] or "relay"


def safe_text(text: str, max_bytes: int = 100) -> str:
    """Printable text with nothing that can end a line or a PJL string."""
    out = []
    for ch in text:
        bad = ch in '"\\@' or unicodedata.category(ch)[0] == "C"
        out.append("_" if bad else ch)
    cut = "".join(out).strip() or "document"
    while len(cut.encode()) > max_bytes:
        cut = cut[:-1]
    return cut


def next_job_number() -> int:
    """A three-digit LPD job number that survives restarts and is shared by every process."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with open(settings.data_dir / "lpd_job_number", "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        try:
            last = int(fh.read().strip() or 0)
        except ValueError:
            last = 0
        number = (last + 1) % 1000
        fh.seek(0)
        fh.truncate()
        fh.write(str(number))
        return number


def pjl_header(*, host: str, user: str, title: str, copies: int = 1, duplex: bool = False) -> str:
    """The job header, as paoprint writes it, minus the password.

    @JOEP (the encoded password) is empty. @RENDERMODE/COLORMODE stay COLOR: the
    queue is black-and-white (sjbw), and paoprint prints through it that way.
    """
    copies = max(1, min(int(copies), 99))
    return (
        "@PJL COMMENT FXJOBINFO STAPLE=OFF\r\n"
        "@PJL SET STRINGCODESET=UTF8\r\n"
        "@PJL JOB MODE=PRINTER\r\n"
        f'@PJL SET JOBATTR="@CNAM={host}"\r\n'
        f'@PJL SET JOBATTR="@LUNA={user}"\r\n'
        '@PJL SET JOBATTR="@HOAD=I0A02BCE5"\r\n'
        '@PJL SET JOBATTR="@NLPP=1"\r\n'
        f'@PJL SET JOBATTR="@JOAU={user}"\r\n'
        '@PJL SET JOBATTR="@JOEP="\r\n'
        '@PJL SET JOBATTR="@DAID="\r\n'
        f'@PJL SET JOBATTR="@ACNA={title}"\r\n'
        '@PJL SET JOBATTR="@DIND=PORTRAIT"\r\n'
        "@PJL SET RENDERMODE=COLOR\r\n"
        "@PJL COMMENT FXJOBINFO VERSION=1.5.0\r\n"
        "@PJL COMMENT FXJOBINFO BEGIN\r\n"
        "@PJL COMMENT FXJOBINFO PDLTYPE=PDL[POSTSCRIPT]:VERSION[3]\r\n"
        "@PJL COMMENT FXJOBINFO JOBTYPE=PRINT\r\n"
        f"@PJL COMMENT FXJOBINFO JOBCOPIES={copies}\r\n"
        "@PJL COMMENT FXJOBINFO PAGECOPIES=1\r\n"
        f"@PJL COMMENT FXJOBINFO DUPLEXTYPE={'DUPLEX' if duplex else 'SIMPLEX'}\r\n"
        "@PJL COMMENT FXJOBINFO COLORMODE=COLOR\r\n"
        "@PJL COMMENT FXJOBINFO NUP=1\r\n"
        "@PJL COMMENT FXJOBINFO ORIENTATION=PORTRAIT\r\n"
        "@PJL COMMENT FXJOBINFO MIXEDSIZETYPE=NONE\r\n"
        "@PJL COMMENT FXJOBINFO CUD=OFF\r\n"
        "@PJL COMMENT FXJOBINFO END\r\n"
        "@PJL ENTER LANGUAGE=POSTSCRIPT\r\n")


def build_job(ps: bytes, *, user: str, title: str, copies: int = 1, duplex: bool = False,
              number: int | None = None) -> list[tuple[str, bytes]]:
    """The whole conversation as (step, bytes); the printer ACKs after each step.

    Used for both sending and `--dump`, so what is inspected is what is sent.
    """
    if not USER_RE.fullmatch(user or ""):
        raise LpdError("this account name cannot be used for printing")
    number = next_job_number() if number is None else number
    host, title = local_host(), safe_text(title)
    cf, df = f"cfA{number:03d}{host}", f"dfA{number:03d}{host}"
    control = f"H{host}\nP{user}\nl{df}\nJ{title}\nU{df}\nN{title}\n".encode()
    header = pjl_header(host=host, user=user, title=title, copies=copies, duplex=duplex).encode()
    data = UEL + header + ps + EOJ
    return [
        ("queue", b"\x02" + settings.lpd_queue.encode() + b"\n"),
        ("control header", b"\x02%d %s\n" % (len(control), cf.encode())),
        ("control body", control + b"\x00"),
        ("data header", b"\x03%d %s\n" % (len(data), df.encode())),
        ("data", data + b"\x00"),
    ]


def send(steps: list[tuple[str, bytes]]) -> None:
    where = f"{settings.lpd_host}:{settings.lpd_port}"
    try:
        sock = socket.create_connection((settings.lpd_host, settings.lpd_port),
                                        timeout=settings.lpd_timeout_s)
    except OSError as e:
        raise LpdError(f"could not reach the printer at {where}: {e}") from e
    with sock:
        sock.settimeout(settings.lpd_timeout_s)
        for step, payload in steps:
            try:
                sock.sendall(payload)
                ack = sock.recv(1)
            except OSError as e:
                raise LpdError(f"the printer stopped answering after {step}: {e}") from e
            if not ack:
                raise LpdError(f"the printer closed the connection at {step}")
            if ack != b"\x00":
                raise LpdError(f"the printer refused the job at {step}")


def submit_ps(path: str | Path, *, user: str, title: str, copies: int = 1, duplex: bool = False) -> int:
    """Send a PostScript file. Returns the LPD job number."""
    steps = build_job(Path(path).read_bytes(), user=user, title=title, copies=copies, duplex=duplex)
    number = int(steps[1][1].split(b"cfA")[1][:3])
    send(steps)
    return number


def _converter() -> list[str]:
    binary = settings.pdftops_binary or shutil.which("pdftops")
    if binary:
        return [binary, "-level3"]
    gs = shutil.which("gs")
    if gs:
        return [gs, "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=ps2write"]
    raise LpdError("this server cannot convert PDFs for the printer (install poppler's pdftops or Ghostscript)")


def to_postscript(path: Path, *, paper: str | None = None, page_range: str | None = None,
                  duplex: bool = False) -> bytes:
    """PostScript for `path`: PDFs are converted, PostScript passes through."""
    head = path.read_bytes()[:8]
    if head.startswith(b"%!"):
        return path.read_bytes()
    if not head.startswith(b"%PDF"):
        raise LpdError("direct printing accepts PDF and PostScript only; export the file to PDF")
    first = last = None
    if page_range and page_range.strip():
        m = PAGE_RANGE_RE.fullmatch(page_range.strip())
        if not m:
            raise LpdError("direct printing takes one page range such as 2-4, not a list")
        first, last = m.group(1), m.group(2) or m.group(1)
    cmd = _converter()
    with tempfile.TemporaryDirectory(prefix="lpd-") as work:
        out = Path(work, "job.ps")
        if Path(cmd[0]).name.startswith("gs") or Path(cmd[0]).name == "gs":
            args = [*cmd, *([f"-dFirstPage={first}", f"-dLastPage={last}"] if first else []),
                    *([f"-sPAPERSIZE={PAPER[paper].lower()}"] if paper in PAPER else []),
                    f"-sOutputFile={out}", str(path)]
        else:
            args = [*cmd, *(["-paper", PAPER[paper]] if paper in PAPER else []),
                    *(["-duplex"] if duplex else []),
                    *(["-f", first, "-l", last] if first else []), str(path), str(out)]
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise LpdError(f"could not convert the PDF for the printer: {e}") from e
        if proc.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
            raise LpdError("could not convert the PDF for the printer"
                           f" ({(proc.stderr or proc.stdout).strip()[-160:] or 'no output'})")
        return out.read_bytes()


def print_pdf(path: str | Path, *, user: str, title: str, copies: int = 1, duplex: bool = False,
              paper: str | None = None, page_range: str | None = None) -> int:
    """Convert if needed, then send. Returns the LPD job number."""
    ps = to_postscript(Path(path), paper=paper, page_range=page_range, duplex=duplex)
    steps = build_job(ps, user=user, title=title, copies=copies, duplex=duplex)
    number = int(steps[1][1].split(b"cfA")[1][:3])
    send(steps)
    return number
