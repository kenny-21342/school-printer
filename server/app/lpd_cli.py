"""Command-line front end for lpd.py, used by the AirPrint shim's print command.

    python -m app.lpd_cli --user s12345 --title Homework [--copies 2] [--sides two-sided-long-edge] file.pdf
    python -m app.lpd_cli --dump ...        # write the exact byte stream to stdout, send nothing

Exit status 0 on success, 1 with the reason on stderr otherwise.
"""
from __future__ import annotations

import argparse
import sys

from . import lpd


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="lpd_cli", description="Print a PDF or PostScript file via LPD/PJL")
    p.add_argument("file")
    p.add_argument("--user", required=True, help="school account the job is released under")
    p.add_argument("--title", default="document")
    p.add_argument("--copies", type=int, default=1)
    p.add_argument("--sides", default="one-sided",
                   choices=["one-sided", "two-sided-long-edge", "two-sided-short-edge"])
    p.add_argument("--paper")
    p.add_argument("--pages", help="one page range, e.g. 2-4")
    p.add_argument("--dump", action="store_true", help="write the stream to stdout instead of sending it")
    a = p.parse_args(argv)
    duplex = a.sides != "one-sided"
    try:
        if a.dump:
            ps = lpd.to_postscript(lpd.Path(a.file), paper=a.paper, page_range=a.pages, duplex=duplex)
            steps = lpd.build_job(ps, user=a.user, title=a.title, copies=a.copies, duplex=duplex)
            sys.stdout.buffer.write(b"".join(payload for _step, payload in steps))
            sys.stdout.buffer.flush()
            return 0
        number = lpd.print_pdf(a.file, user=a.user, title=a.title, copies=a.copies, duplex=duplex,
                               paper=a.paper, page_range=a.pages)
    except lpd.LpdError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"lpd job {number} accepted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
