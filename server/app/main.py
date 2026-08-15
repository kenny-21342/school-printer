"""Main FastAPI app.

Concurrency note: the request handlers below are deliberately plain `def`, not
`async def`. They all perform blocking work — streaming uploads to disk, argon2
hashing, `lp`/`lpstat` subprocesses, `pdftoppm` rendering, and SQLite calls.
FastAPI runs `async def` handlers directly on the event loop, so the previous
all-async version froze the entire server for every other user for the duration
of any upload or slow printer query. Plain `def` handlers are dispatched to
FastAPI's threadpool instead. Do not convert these back to `async def` without
first making every call inside them non-blocking.
"""
from __future__ import annotations

import base64
import logging
import secrets
import threading
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from . import __version__
from .auth import (
    burn_password_time,
    bump_token_version,
    clear_failed_logins,
    create_access_token,
    create_user,
    get_user,
    get_user_by_username,
    hash_password,
    lockout_remaining,
    register_failed_login,
    update_last_login,
    verify_password,
)
from .config import settings
from .cups import PrintSettings, get_job_state, queue_options, submit_job
from .db import get_conn, init_db
from .deps import admin_only, current_user
from .pdftools import is_pdf, needs_conversion, pdf_info, render_page_thumbnail
from .schemas import (
    HealthResponse,
    JobOut,
    LoginRequest,
    PagePreviewResponse,
    PasswordChange,
    PdfInfoResponse,
    TokenResponse,
    UserCreate,
    UserOut,
    UserUpdate,
)

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("school-printer")

# Rate limiting by IP is a coarse backstop only: behind school NAT the whole
# school shares one source address, so these limits are set high enough not to
# lock out a classroom. Login is throttled per-username instead (see /auth/login).
limiter = Limiter(key_func=get_remote_address)

# Statuses after which a job's spool file is no longer needed.
TERMINAL_STATUSES = ("done", "cancelled", "error")


# --------------------------------------------------------------------------
# Spool reaper (C3)
# --------------------------------------------------------------------------

def _delete_spool(path: str | None) -> None:
    if not path:
        return
    with suppress(OSError):
        p = Path(path)
        # Only ever unlink inside the configured spool dir.
        if p.parent.resolve() == settings.spool_dir.resolve():
            p.unlink(missing_ok=True)


def sweep_spool() -> int:
    """Delete spool files whose job is finished, plus expired preview uploads.

    Returns the number of files removed. Without this the Pi's SD card fills up:
    nothing in the original code ever deleted an uploaded file.
    """
    if settings.spool_retention_hours <= 0:
        return 0
    cutoff = int(time.time()) - settings.spool_retention_hours * 3600
    removed = 0

    with get_conn() as conn:
        stale_jobs = conn.execute(
            f"""SELECT id, spool_path FROM jobs
                 WHERE spool_path IS NOT NULL
                   AND status IN ({','.join('?' * len(TERMINAL_STATUSES))})
                   AND created_at < ?""",
            (*TERMINAL_STATUSES, cutoff),
        ).fetchall()
        stale_previews = conn.execute(
            "SELECT name FROM preview_files WHERE created_at < ?", (cutoff,)
        ).fetchall()

    for row in stale_jobs:
        _delete_spool(row["spool_path"])
        removed += 1
    for row in stale_previews:
        _delete_spool(str(settings.spool_dir / row["name"]))
        removed += 1

    with get_conn() as conn:
        if stale_jobs:
            conn.executemany(
                "UPDATE jobs SET spool_path = NULL WHERE id = ?",
                [(r["id"],) for r in stale_jobs],
            )
        if stale_previews:
            conn.executemany(
                "DELETE FROM preview_files WHERE name = ?",
                [(r["name"],) for r in stale_previews],
            )

    # Belt and braces: anything in the spool dir that no table knows about and
    # is older than the cutoff (e.g. left behind by a crash) also goes.
    with suppress(OSError):
        for f in settings.spool_dir.iterdir():
            if f.is_file() and f.stat().st_mtime < cutoff:
                with suppress(OSError):
                    f.unlink()
                    removed += 1

    if removed:
        log.info("spool sweep removed %d file(s)", removed)
    return removed


def _start_reaper(stop: threading.Event) -> threading.Thread:
    def loop() -> None:
        while not stop.wait(settings.spool_sweep_interval_s):
            try:
                sweep_spool()
            except Exception:
                log.exception("spool sweep failed")

    t = threading.Thread(target=loop, name="spool-reaper", daemon=True)
    t.start()
    return t


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop = threading.Event()
    with suppress(Exception):
        sweep_spool()  # one sweep at boot to clear whatever accumulated
    _start_reaper(stop)
    yield
    stop.set()


def create_app() -> FastAPI:
    settings.ensure_dirs()
    init_db()
    _bootstrap_admin()

    app = FastAPI(
        title="School Printer Relay",
        version=__version__,
        description="iOS upload → CUPS → FUJIFILM Xerox (bw)",
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # CORS — allow any origin since iOS app has no fixed origin. Tighten at school.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    # ---------------- Auth ----------------
    @app.post("/auth/login", response_model=TokenResponse, tags=["auth"])
    @limiter.limit("120/minute")
    def login(request: Request, body: LoginRequest) -> TokenResponse:
        user = get_user_by_username(body.username)
        if not user:
            # Burn a real argon2 verification so a missing username takes the
            # same time as a wrong password (M12).
            burn_password_time()
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")

        remaining = lockout_remaining(user)
        if remaining:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"too many failed attempts; try again in {remaining}s",
            )

        if not verify_password(body.password, user["password_hash"]):
            register_failed_login(user["id"])
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
        if not user["is_active"]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "account disabled")

        clear_failed_logins(user["id"])
        update_last_login(user["id"])
        token = create_access_token(
            str(user["id"]),
            {
                "u": user["username"],
                "a": bool(user["is_admin"]),
                "tv": int(user["token_version"] or 0),
            },
        )
        return TokenResponse(
            access_token=token,
            expires_in=settings.access_token_expire_minutes * 60,
            user_id=user["id"],
            username=user["username"],
            is_admin=bool(user["is_admin"]),
        )

    # ---------------- Users ----------------
    @app.post("/users", response_model=UserOut, tags=["users"], status_code=201)
    def create_user_endpoint(
        body: UserCreate,
        _: Annotated[dict, Depends(admin_only)],
    ) -> UserOut:
        if get_user_by_username(body.username):
            raise HTTPException(409, "username already exists")
        uid = create_user(
            body.username, body.password,
            display_name=body.display_name, is_admin=body.is_admin,
        )
        return UserOut(**get_user(uid))

    @app.get("/users", response_model=list[UserOut], tags=["users"])
    def list_users_endpoint(
        _: Annotated[dict, Depends(admin_only)],
    ) -> list[UserOut]:
        with get_conn() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
        return [UserOut(**dict(r)) for r in rows]

    @app.get("/users/me", response_model=UserOut, tags=["users"])
    def me(user: Annotated[dict, Depends(current_user)]) -> UserOut:
        return UserOut(**user)

    @app.patch("/users/me", response_model=UserOut, tags=["users"])
    def update_me(
        body: PasswordChange,
        user: Annotated[dict, Depends(current_user)],
    ) -> UserOut:
        """Self-service password / display name change (L7).

        Previously only admins could change any password, so a student had no
        way to rotate their own credentials.
        """
        if body.new_password is not None:
            if not verify_password(body.current_password or "", user["password_hash"]):
                raise HTTPException(403, "current password is incorrect")
            with get_conn() as conn:
                conn.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (hash_password(body.new_password), user["id"]),
                )
            bump_token_version(user["id"])
        if body.display_name is not None:
            with get_conn() as conn:
                conn.execute(
                    "UPDATE users SET display_name = ? WHERE id = ?",
                    (body.display_name, user["id"]),
                )
        return UserOut(**get_user(user["id"]))

    @app.patch("/users/{user_id}", response_model=UserOut, tags=["users"])
    def update_user_endpoint(
        user_id: int,
        body: UserUpdate,
        admin: Annotated[dict, Depends(admin_only)],
    ) -> UserOut:
        target = get_user(user_id)
        if not target:
            raise HTTPException(404, "user not found")
        # Don't let the last route back in be closed off (L8).
        if user_id == admin["id"]:
            if body.is_admin is False:
                raise HTTPException(400, "cannot remove your own admin rights")
            if body.is_active is False:
                raise HTTPException(400, "cannot disable your own account")

        sets, args = [], []
        password_changed = False
        if body.password is not None:
            sets.append("password_hash = ?"); args.append(hash_password(body.password))
            password_changed = True
        if body.display_name is not None:
            sets.append("display_name = ?"); args.append(body.display_name)
        if body.is_admin is not None:
            sets.append("is_admin = ?"); args.append(int(body.is_admin))
        if body.is_active is not None:
            sets.append("is_active = ?"); args.append(int(body.is_active))
        if sets:
            args.append(user_id)
            with get_conn() as conn:
                conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", args)
        if password_changed:
            bump_token_version(user_id)
        return UserOut(**get_user(user_id))

    @app.delete("/users/{user_id}", tags=["users"], status_code=204)
    def delete_user_endpoint(
        user_id: int,
        admin: Annotated[dict, Depends(admin_only)],
    ) -> Response:
        if user_id == admin["id"]:
            raise HTTPException(400, "cannot delete yourself")
        if not get_user(user_id):
            raise HTTPException(404, "user not found")
        with get_conn() as conn:
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return Response(status_code=204)

    # ---------------- Print ----------------
    @app.post("/print", response_model=JobOut, tags=["print"], status_code=201)
    @limiter.limit("120/minute")
    def upload_and_print(
        request: Request,
        user: Annotated[dict, Depends(current_user)],
        file: UploadFile = File(..., description="File to print"),
        copies: int = Form(1),
        duplex: bool = Form(False),
        color_mode: str = Form("monochrome"),
        page_range: str = Form(""),
        paper_size: str = Form(""),
        orientation: str = Form(""),
        scaling: str = Form(""),
        layout_direction: str = Form(""),
    ) -> JobOut:
        if color_mode not in {"monochrome", "color"}:
            raise HTTPException(422, "color_mode must be 'monochrome' or 'color'")
        if not (1 <= copies <= 50):
            raise HTTPException(422, "copies must be 1..50")
        if orientation and orientation not in {"portrait", "landscape", "auto"}:
            raise HTTPException(422, "orientation must be portrait/landscape/auto")
        if layout_direction and layout_direction not in {"ltr", "rtl"}:
            raise HTTPException(422, "layout_direction must be ltr/rtl")
        # Bounds match the client and what cups.py will actually emit (M4).
        if scaling and scaling not in {"fit", "actual", "shrink"}:
            if not scaling.isdigit() or not (1 <= int(scaling) <= settings.max_scaling_percent):
                raise HTTPException(
                    422,
                    f"scaling must be fit/actual/shrink or a number 1-{settings.max_scaling_percent}",
                )

        mime = file.content_type or "application/octet-stream"

        # Office files are rejected up front (C5). The Pi cannot convert them
        # and the iMac relay does not exist, so the old behaviour — accept,
        # store, mark 'pending-conversion', never process — silently swallowed
        # the document with no error the student would ever see.
        if needs_conversion(mime):
            raise HTTPException(
                415,
                "Word/Excel/PowerPoint printing isn't available yet — "
                "please export the document to PDF and print that instead.",
            )
        if settings.allowed_mimes and mime not in settings.allowed_mimes:
            raise HTTPException(415, f"unsupported file type: {mime}")

        # Stream to spool, enforcing the size limit as we go.
        suffix = Path(file.filename or "upload").suffix.lower()[:16]
        spool_name = f"{int(time.time()*1000)}_{secrets.token_hex(4)}_{user['id']}{suffix}"
        spool_path = settings.spool_dir / spool_name
        size = _stream_to_spool(file, spool_path)

        ps = PrintSettings(
            copies=copies, duplex=duplex, color_mode=color_mode,
            page_range=page_range or None,
            paper_size=paper_size or None,
            orientation=orientation or None,
            scaling=scaling or None,
            layout_direction=layout_direction or None,
        )
        try:
            cups_id, _lp_out = submit_job(
                str(spool_path),
                settings_obj=ps,
                job_title=f"{user['username']}: {file.filename or 'doc'}",
            )
            job_status = "pending"
            err = None
        except Exception as e:
            cups_id = None
            job_status = "error"
            err = str(e)[:500]
            log.exception("lp submit failed")
            # The job will never print; don't leave the upload on disk.
            _delete_spool(str(spool_path))

        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO jobs
                   (user_id, filename, mime_type, size_bytes, copies, duplex, color_mode,
                    page_range, paper_size, orientation, scaling, layout_direction,
                    cups_job_id, status, error, needs_conversion, spool_path, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)""",
                (user["id"], file.filename or spool_name, mime, size,
                 copies, int(duplex), color_mode,
                 page_range or None, paper_size or None, orientation or None,
                 scaling or None, layout_direction or None,
                 cups_id, job_status, err,
                 None if job_status == "error" else str(spool_path), int(time.time())),
            )
            job_id = cur.lastrowid
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        d = dict(row); d["username"] = user["username"]
        return JobOut(**d)

    # ---------------- PDF inspection / preview ----------------

    @app.post("/pdf-info", response_model=PdfInfoResponse, tags=["print"])
    @limiter.limit("120/minute")
    def get_pdf_info(
        request: Request,
        user: Annotated[dict, Depends(current_user)],
        file: UploadFile = File(..., description="PDF to inspect"),
    ) -> PdfInfoResponse:
        """Upload a PDF and get back its page count + page size, without printing.

        The spool copy is retained so /preview can render pages from it, and is
        registered in preview_files so /preview can check ownership and the
        reaper can expire it.
        """
        mime = file.content_type or "application/octet-stream"
        suffix = Path(file.filename or "upload").suffix.lower()[:16]
        spool_name = f"info_{int(time.time()*1000)}_{secrets.token_hex(4)}_{user['id']}{suffix}"
        spool_path = settings.spool_dir / spool_name
        _stream_to_spool(file, spool_path)

        if not is_pdf(spool_path):
            # Not a PDF — nothing to render, so don't keep the upload around.
            _delete_spool(str(spool_path))
            return PdfInfoResponse(
                pages=0, page_size=None, is_pdf=False,
                needs_conversion=needs_conversion(mime),
            )

        with get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO preview_files (name, user_id, created_at) VALUES (?,?,?)",
                (spool_name, user["id"], int(time.time())),
            )

        meta = pdf_info(spool_path)
        return PdfInfoResponse(
            pages=meta.pages, page_size=meta.page_size,
            is_pdf=True, spool_file=spool_name,
        )

    @app.get("/preview", response_model=PagePreviewResponse, tags=["print"])
    @limiter.limit("600/minute")
    def get_preview(
        request: Request,
        user: Annotated[dict, Depends(current_user)],
        spool_file: str = Query(..., description="spool filename returned by /pdf-info upload"),
        page: int = Query(1, ge=1),
        width: int = Query(300, ge=64, le=1024),
    ) -> PagePreviewResponse:
        """Render a single page of a previously-uploaded PDF as a JPEG thumbnail."""
        # Only ever a bare filename inside the spool dir.
        safe = Path(spool_file).name
        # Ownership check (H7): without this any authenticated user could render
        # any other user's uploaded document as page images.
        with get_conn() as conn:
            owner = conn.execute(
                "SELECT user_id FROM preview_files WHERE name = ?", (safe,)
            ).fetchone()
        if not owner:
            raise HTTPException(404, "preview file not found (may have expired)")
        if owner["user_id"] != user["id"] and not user["is_admin"]:
            raise HTTPException(403, "forbidden")

        spool_path = settings.spool_dir / safe
        if not spool_path.exists():
            raise HTTPException(404, "preview file not found (may have expired)")
        if not is_pdf(spool_path):
            raise HTTPException(400, "not a PDF")
        result = render_page_thumbnail(spool_path, page=page, width_px=width)
        if result is None:
            raise HTTPException(500, "could not render page")
        data, w, h = result
        return PagePreviewResponse(
            page=page,
            image_base64=base64.b64encode(data).decode("ascii"),
            width=w, height=h,
        )

    @app.get("/jobs", response_model=list[JobOut], tags=["print"])
    def list_my_jobs(
        user: Annotated[dict, Depends(current_user)],
        limit: int = Query(20, ge=1, le=100),
        include_all: bool = Query(False, description="admin: list all users"),
    ) -> list[JobOut]:
        admin = bool(user["is_admin"]) and include_all
        if admin:
            sql = ("SELECT j.*, u.username FROM jobs j JOIN users u ON u.id = j.user_id "
                   "ORDER BY j.id DESC LIMIT ?")
            params: tuple = (limit,)
        else:
            sql = ("SELECT j.*, u.username FROM jobs j JOIN users u ON u.id = j.user_id "
                   "WHERE j.user_id = ? ORDER BY j.id DESC LIMIT ?")
            params = (user["id"], limit)
        with get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [JobOut(**dict(r)) for r in rows]

    @app.get("/jobs/{job_id}", response_model=JobOut, tags=["print"])
    def get_job_endpoint(
        job_id: int,
        user: Annotated[dict, Depends(current_user)],
    ) -> JobOut:
        return JobOut(**_load_job_for(job_id, user))

    @app.post("/jobs/{job_id}/refresh", response_model=JobOut, tags=["print"])
    def refresh_job(
        job_id: int,
        user: Annotated[dict, Depends(current_user)],
    ) -> JobOut:
        d = _load_job_for(job_id, user)

        # The lpstat call is deliberately OUTSIDE any open DB connection: it can
        # take up to 10s per invocation, and holding the connection across it
        # was blocking database access server-wide (F2).
        if not d["cups_job_id"] or d["status"] in TERMINAL_STATUSES:
            return JobOut(**d)

        st = get_job_state(d["cups_job_id"])
        if st == "unknown":
            return JobOut(**d)

        with get_conn() as conn:
            if st == "done":
                conn.execute(
                    "UPDATE jobs SET status='done', printed_at=? WHERE id=?",
                    (int(time.time()), job_id),
                )
            else:
                conn.execute("UPDATE jobs SET status=? WHERE id=?", (st, job_id))

        if st in TERMINAL_STATUSES:
            _release_spool(job_id)
        return JobOut(**_load_job_for(job_id, user))

    @app.delete("/jobs/{job_id}", tags=["print"], status_code=204)
    def cancel_job_endpoint(
        job_id: int,
        user: Annotated[dict, Depends(current_user)],
    ) -> Response:
        d = _load_job_for(job_id, user)
        if d["cups_job_id"]:
            from .cups import cancel_job
            cancel_job(d["cups_job_id"])
        with get_conn() as conn:
            conn.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (job_id,))
        _release_spool(job_id)
        return Response(status_code=204)

    # ---------------- Misc ----------------
    @app.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        import socket
        # Try both the Debian package socket and the snap socket — which one
        # exists depends on how CUPS was installed on this Pi.
        reachable = False
        for sock_path in ("/run/cups/cups.sock", "/var/snap/cups/common/run/cups.sock"):
            try:
                s = socket.socket(socket.AF_UNIX); s.settimeout(1)
                s.connect(sock_path); s.close()
                reachable = True
                break
            except OSError:
                continue
        opts = queue_options()
        return HealthResponse(
            status="ok",
            version=__version__,
            queue=settings.cups_queue,
            cups_reachable=reachable,
            # Empty list ⇒ raw queue ⇒ print options are silently ignored (F1).
            queue_is_raw=(len(opts) == 0),
            queue_options=opts,
        )

    return app


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _stream_to_spool(file: UploadFile, spool_path: Path) -> int:
    """Stream an upload to disk, enforcing the size cap. Returns bytes written.

    Any failure removes the partial file — the original only cleaned up on the
    size-limit path, so a disconnect or a full disk leaked the partial (L12).
    """
    max_bytes = settings.max_upload_mb * 1024 * 1024
    size = 0
    try:
        with spool_path.open("wb") as f:
            while chunk := file.file.read(64 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(413, f"file too large (>{settings.max_upload_mb} MB)")
                f.write(chunk)
    except BaseException:
        _delete_spool(str(spool_path))
        raise
    finally:
        file.file.close()
    return size


def _load_job_for(job_id: int, user: dict) -> dict:
    """Fetch a job, enforcing ownership. Raises 404/403."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT j.*, u.username FROM jobs j JOIN users u ON u.id = j.user_id WHERE j.id = ?",
            (job_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "job not found")
    d = dict(row)
    if d["user_id"] != user["id"] and not user["is_admin"]:
        raise HTTPException(403, "forbidden")
    return d


def _release_spool(job_id: int) -> None:
    """Delete a finished job's spool file and forget the path."""
    with get_conn() as conn:
        row = conn.execute("SELECT spool_path FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row or not row["spool_path"]:
        return
    _delete_spool(row["spool_path"])
    with get_conn() as conn:
        conn.execute("UPDATE jobs SET spool_path = NULL WHERE id = ?", (job_id,))


def _bootstrap_admin() -> None:
    """Create the admin user from SP_BOOTSTRAP_ADMIN_PASS on first run."""
    pw = settings.bootstrap_admin_pass
    if not pw:
        return
    if get_user_by_username(settings.bootstrap_admin_user):
        return
    log.info("bootstrapping admin user %r", settings.bootstrap_admin_user)
    create_user(
        settings.bootstrap_admin_user, pw,
        display_name="Administrator", is_admin=True,
    )


app = create_app()
