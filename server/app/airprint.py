"""Per-student AirPrint registry.

A student asks for a printer with their school ID; the administrator approves
the request; approval yields a token that identifies one device. The token goes
two places: into an AirPrint configuration profile (as the printer's URL path,
for the native Print dialog) and into an iOS Shortcut (as `X-Print-Key`, for
iPads that will not install profiles). Either way the iMac learns which school
account a job belongs to from the token, because iOS reports its user as
"mobile" and the campus system takes whatever name arrives with a job.

Tokens are not stored. A token is `<row id>-<HMAC of the row under the server
secret>`, so it can be shown again on request, cannot be guessed, and dies when
the row is revoked or rotated. Nothing here proves a student owns the school ID
they type: that is the administrator's check at approval time.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import plistlib
import re
import secrets
import time
import uuid

from .auth import create_user, get_user_by_username
from .config import settings
from .db import get_conn

SCHOOL_ID_RE = re.compile(r"[A-Za-z0-9._-]{2,64}")
MAX_PENDING = 200
MAX_ATTEMPTS = 5            # wrong codes before a new one must be printed
MAX_SENDS = 3               # codes printed per request
RESEND_DELAY_S = 60
MAX_PER_ID_PER_HOUR = 3     # requests per school ID: each one prints a page in that account's queue


class TooManyRequests(Exception):
    pass


class VerificationError(Exception):
    """The code page could not be printed (printer or CUPS unavailable)."""


def _secret() -> bytes:
    if not settings.secret_key:
        settings.ensure_dirs()
    return settings.secret_key.encode()


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _mac(row: dict) -> str:
    msg = f"airprint:{row['id']}:{row['school_id'].lower()}:{row['rotation']}".encode()
    return hmac.new(_secret(), msg, hashlib.sha256).hexdigest()[:32]


def token_for(row: dict) -> str:
    return f"{row['id']}-{_mac(row)}"


def request_access(school_id: str, display_name: str) -> str:
    """Record a pending request and return its secret request code."""
    if not SCHOOL_ID_RE.fullmatch(school_id):
        raise ValueError("invalid school ID")
    code = secrets.token_hex(16)
    with get_conn() as conn:
        pending = conn.execute(
            "SELECT COUNT(*) FROM print_keys WHERE status='pending'").fetchone()[0]
        recent = conn.execute(
            "SELECT COUNT(*) FROM print_keys WHERE school_id=? AND created_at>?",
            (school_id, int(time.time()) - 3600)).fetchone()[0]
        if pending >= MAX_PENDING or recent >= MAX_PER_ID_PER_HOUR:
            raise TooManyRequests
        conn.execute(
            "INSERT INTO print_keys (school_id, display_name, code_hash, created_at) "
            "VALUES (?, ?, ?, ?)",
            (school_id, display_name.strip()[:80], _hash(code), int(time.time())))
    return code


def _new_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def _verify_mac(request_id: int, code: str) -> str:
    return hmac.new(_secret(), f"verify:{request_id}:{code}".encode(), hashlib.sha256).hexdigest()


def verification_pdf(code: str, school_id: str) -> bytes:
    """A one-page A4 PDF carrying the code, written by hand (no PDF library)."""
    def esc(t: str) -> str:
        return t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    lines = [(18, 760, "School Print - verification"),
             (11, 730, f"Requested for school account: {esc(school_id)}"),
             (11, 712, "Enter this code on the page you opened on your iPad:"),
             (64, 640, code),
             (10, 600, "If you did not ask for this, ignore it; nothing is set up until the code is entered.")]
    stream = "BT\n" + "\n".join(
        f"/F1 {size} Tf 1 0 0 1 72 {y} Tm ({text}) Tj" for size, y, text in lines) + "\nET"
    body = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream.encode()),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for n, obj in enumerate(body, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (n, obj)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(body) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(body) + 1, xref)
    return bytes(out)


def send_verification(request_id: int) -> None:
    """Print a fresh code under the requested school ID and remember its MAC.

    Only the holder of that account can log in at the campus printer, release the
    job and read the code, so entering it proves they own the account. The older
    code stops working. Raises TooManyRequests (limits) or VerificationError.
    """
    from . import cups                       # lazy: cups pulls in subprocess setup
    row = _get(request_id)
    now = int(time.time())
    if row["status"] != "pending" or row["verified_at"]:
        return
    if row["verify_sends"] >= MAX_SENDS or (
            row["verify_sent_at"] and now - row["verify_sent_at"] < RESEND_DELAY_S):
        raise TooManyRequests
    code = _new_code()
    with get_conn() as conn:                 # count the attempt before printing, so a failure still counts
        conn.execute(
            "UPDATE print_keys SET verify_mac=?, verify_sends=verify_sends+1, verify_sent_at=?, "
            "verify_attempts=0 WHERE id=?", (_verify_mac(request_id, code), now, request_id))
    path = settings.spool_dir / f"verify-{request_id}-{secrets.token_hex(4)}.pdf"
    try:
        path.write_bytes(verification_pdf(code, row["school_id"]))
        cups.submit_job(str(path), job_title="School Print verification code", owner=row["school_id"])
    except Exception as e:  # noqa: BLE001
        raise VerificationError(str(e)) from e
    finally:
        path.unlink(missing_ok=True)


def check_code(request_id: int, code: str) -> str:
    """'ok' (and approve if configured), 'wrong', 'locked' (too many tries) or 'none'."""
    row = _get(request_id)
    if row["status"] != "pending" or not row["verify_mac"]:
        return "none"
    if row["verify_attempts"] >= MAX_ATTEMPTS:
        return "locked"
    with get_conn() as conn:
        conn.execute("UPDATE print_keys SET verify_attempts=verify_attempts+1 WHERE id=?", (request_id,))
    if not hmac.compare_digest(_verify_mac(request_id, code.strip()), row["verify_mac"]):
        return "wrong"
    with get_conn() as conn:
        conn.execute("UPDATE print_keys SET verified_at=?, verify_mac=NULL WHERE id=?",
                     (int(time.time()), request_id))
    if settings.airprint_auto_approve_verified:
        approve(request_id)
    return "ok"


def find_by_code(code: str) -> dict | None:
    if not re.fullmatch(r"[0-9a-f]{32}", code):
        return None
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM print_keys WHERE code_hash=?", (_hash(code),)).fetchone()
    return dict(row) if row else None


def list_requests(status: str | None = None) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM print_keys" + (" WHERE status=?" if status else "") + " ORDER BY id",
            (status,) if status else ()).fetchall()
    return [dict(r) for r in rows]


def _get(request_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM print_keys WHERE id=?", (request_id,)).fetchone()
    if row is None:
        raise KeyError(request_id)
    return dict(row)


def approve(request_id: int) -> str:
    """Approve a request (creating the student's user if needed); return its token."""
    row = _get(request_id)
    if row["status"] == "revoked":
        raise ValueError("request was revoked; ask the student to register again")
    if row["status"] == "approved":
        return token_for(row)
    user = get_user_by_username(row["school_id"])
    user_id = user["id"] if user else create_user(
        row["school_id"], secrets.token_urlsafe(32), display_name=row["display_name"] or None)
    with get_conn() as conn:
        conn.execute(
            "UPDATE print_keys SET status='approved', user_id=?, approved_at=? WHERE id=?",
            (user_id, int(time.time()), request_id))
    return token_for(_get(request_id))


def revoke(request_id: int) -> None:
    _get(request_id)
    with get_conn() as conn:
        conn.execute("UPDATE print_keys SET status='revoked' WHERE id=?", (request_id,))


def resolve_token(token: str) -> dict | None:
    """The approved row a token belongs to, or None. Constant-time on the MAC."""
    m = re.fullmatch(r"(\d{1,9})-([0-9a-f]{32})", token or "")
    if not m:
        return None
    try:
        row = _get(int(m.group(1)))
    except KeyError:
        return None
    if row["status"] != "approved" or not hmac.compare_digest(m.group(2), _mac(row)):
        return None
    now = int(time.time())
    if now - (row["last_used"] or 0) > 60:           # not a write per IPP request
        with get_conn() as conn:
            conn.execute("UPDATE print_keys SET last_used=? WHERE id=?", (now, row["id"]))
    return row


def build_profile(row: dict, host: str, port: int) -> bytes:
    """An AirPrint configuration profile that adds this student's printer by address."""
    ns = uuid.NAMESPACE_URL
    ident = f"com.school.airprint.{row['id']}"
    return plistlib.dumps({
        "PayloadType": "Configuration",
        "PayloadVersion": 1,
        "PayloadIdentifier": ident,
        "PayloadUUID": str(uuid.uuid5(ns, ident)).upper(),
        "PayloadDisplayName": f"School Print ({row['school_id']})",
        "PayloadDescription": "Adds the school print relay as an AirPrint printer. "
                              f"Everything you print from this iPad is released under {row['school_id']}.",
        "PayloadOrganization": "School Printer",
        "PayloadRemovalDisallowed": False,
        "PayloadContent": [{
            "PayloadType": "com.apple.airprint",
            "PayloadVersion": 1,
            "PayloadIdentifier": ident + ".printer",
            "PayloadUUID": str(uuid.uuid5(ns, ident + ".printer")).upper(),
            "PayloadDisplayName": "School Print",
            "AirPrint": [{
                "IPAddress": host,
                "ResourcePath": f"ipp/print/{token_for(row)}",
                "Port": port,
                "ForceTLS": False,
            }],
        }],
    })


# ---------------------------------------------------------------- pages

_CSS = """
:root{color-scheme:light dark;--bg:#fff;--fg:#1c1c1e;--mut:#6e6e73;--acc:#0a66d6;--card:#f2f2f7}
@media(prefers-color-scheme:dark){:root{--bg:#000;--fg:#f5f5f7;--mut:#98989d;--acc:#4da3ff;--card:#1c1c1e}}
body{margin:0;background:var(--bg);color:var(--fg);font:17px/1.45 -apple-system,system-ui,sans-serif}
main{max-width:34rem;margin:0 auto;padding:1.5rem 1rem 3rem}
h1{font-size:1.6rem;margin:.2rem 0 1rem}h2{font-size:1.1rem;margin:1.6rem 0 .4rem}
p,li{color:var(--fg)}.mut{color:var(--mut);font-size:.9rem}
input{width:100%;box-sizing:border-box;padding:.7rem;margin:.25rem 0 1rem;font:inherit;border-radius:10px;
border:1px solid var(--mut);background:var(--card);color:var(--fg)}
.btn{display:block;text-align:center;background:var(--acc);color:#fff;border:0;border-radius:12px;
padding:.85rem;font:inherit;font-weight:600;text-decoration:none;width:100%;box-sizing:border-box}
code{display:block;word-break:break-all;background:var(--card);padding:.6rem;border-radius:8px;font-size:.85rem}
"""


def _page(title: str, body: str) -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(title)}</title><style>{_CSS}</style></head>'
            f'<body><main><h1>{html.escape(title)}</h1>{body}</main></body></html>')


def register_page(error: str = "") -> str:
    err = f'<p style="color:#d70015">{html.escape(error)}</p>' if error else ""
    return _page("Print from your iPad", f"""{err}
<p>Request a printer that prints under <b>your own school account</b>, straight from the Share → Print
menu. An administrator approves each request.</p>
<form method="post" action="/register">
<label>School ID<input name="school_id" required minlength="2" maxlength="64" autocapitalize="none"
 autocorrect="off" placeholder="the name you log in with at the printer"></label>
<label>Your name<input name="name" maxlength="80" placeholder="so the administrator knows it is you"></label>
<button class="btn" type="submit">Request access</button></form>
<p class="mut">Jobs you print count against your own school account. Only request your own ID.</p>""")


NOTICES = {
    "send": "We could not print the code just now. Wait a minute and tap the button to try again.",
    "resent": "A new code was printed. The earlier code no longer works.",
    "wrong": "That code is incorrect. Check the page you released and try again.",
    "locked": "Too many wrong codes. Print a new one with the button below.",
    "limit": "Too many codes have been printed for this request. Ask the administrator.",
}


def status_page(row: dict, code: str, host: str, notice: str = "") -> str:
    sid = html.escape(row["school_id"])
    note = (f'<p style="color:#d70015"><b>{html.escape(NOTICES[notice])}</b></p>'
            if notice in NOTICES else "")
    if row["status"] == "pending" and settings.airprint_verify and not row["verified_at"]:
        return _page("Check your school account", f"""{note}
<p>We printed a one-page <b>code</b> to the campus printer under <b>{sid}</b>.
Only you can collect it:</p>
<ol><li>At a campus printer, log in with your school account.</li>
<li>Release the job called <b>School Print verification code</b>.</li>
<li>Type the 6-digit code from the page here.</li></ol>
<form method="post" action="/register/{code}/verify">
<input name="code" inputmode="numeric" pattern="[0-9]{{6}}" maxlength="6" required autocomplete="off"
 placeholder="6-digit code">
<button class="btn" type="submit">Verify</button></form>
<form method="post" action="/register/{code}/resend" style="margin-top:1rem">
<button class="btn" style="background:var(--card);color:var(--fg)" type="submit">Print a new code</button></form>
<p class="mut">Not at school right now? Keep this page's address and come back; the code stays valid.</p>""")
    if row["status"] == "pending":
        return _page("Waiting for approval", f"""{note}<p>Your request for <b>{sid}</b> has been recorded.
Ask the administrator to approve it, then reopen this page.</p>
<p class="mut">Keep this page's address: it is how you will get your printer.</p>""")
    if row["status"] == "revoked":
        return _page("Access removed", f"<p>Access for <b>{sid}</b> on this device has been removed.</p>")
    token = html.escape(token_for(row))
    return _page("Your printer is ready", f"""{note}<p>Printing as <b>{sid}</b>.</p>
<h2>1. Install the printer</h2>
<a class="btn" href="/register/{code}/profile.mobileconfig">Download printer profile</a>
<ol><li>Tap <b>Allow</b> when asked to download a profile.</li>
<li>Open <b>Settings → Profile Downloaded</b> and tap <b>Install</b>. It is marked
“Not Signed” because it comes from this school project, not a company.</li>
<li>In any app: <b>Share → Print → Select Printer → School Print</b>.</li></ol>
<h2>2. Or use a Shortcut</h2>
<p>If your iPad will not install profiles: add the Shortcut, then use <b>Share → Print at School</b>
in any app. It already contains your key.</p>
<a class="btn" href="/register/{code}/print.shortcut">Add Print Shortcut</a>
<p class="mut">Or paste this key into a Shortcut you built yourself:</p>
<code>{token}</code>
<p class="mut">Keep the key private. Anyone who has it can print as {sid}.</p>""")
