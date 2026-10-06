"""Administer AirPrint requests on the machine that runs the relay.

    python -m app.airprint_cli list [pending|approved|revoked]
    python -m app.airprint_cli approve <id>
    python -m app.airprint_cli revoke <id>

Works on the database directly, so approving needs no admin password. Run it
with the relay's environment (see tools/school-print, which does that over ssh).
"""
from __future__ import annotations

import sys
import time

from . import airprint
from .db import init_db


def _fmt(ts: int | None) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "-"


def main(argv: list[str]) -> int:
    init_db()
    cmd = argv[0] if argv else "list"
    if cmd == "list":
        rows = airprint.list_requests(argv[1] if len(argv) > 1 else None)
        print(f"{'id':>3}  {'school id':<14} {'status':<9} {'requested':<12} {'last used':<12} name")
        for r in rows:
            print(f"{r['id']:>3}  {r['school_id']:<14} {r['status']:<9} "
                  f"{_fmt(r['created_at']):<12} {_fmt(r['last_used']):<12} {r['display_name'] or ''}")
        return 0
    if cmd in ("approve", "revoke") and len(argv) == 2 and argv[1].isdigit():
        rid = int(argv[1])
        try:
            if cmd == "approve":
                airprint.approve(rid)
                print(f"approved {rid}")
            else:
                airprint.revoke(rid)
                print(f"revoked {rid}")
        except (KeyError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
