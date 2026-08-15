"""Main FastAPI app."""
from __future__ import annotations

import logging
import os
import secrets
import shutil
import time
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
    create_access_token,
    create_user,
    decode_token,
    get_user_by_username,
    hash_password,
    update_last_login,
    verify_password,
)
from .config import settings
from .cups import PrintSettings, get_job_state, submit_job
from .db import get_conn, init_db
from .deps import admin_only, current_user
from .pdftools import (
    is_pdf, needs_conversion, office_extension_for_mime,
    pdf_info, render_page_thumbnail,
)
from .schemas import (
    HealthResponse,
    JobOut,
    JobStatusUpdate,
    LoginRequest,
    PagePreviewResponse,
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

limiter = Limiter(key_func=get_remote_address)


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
    @limiter.limit("10/minute")
    async def login(request: Request, body: LoginRequest) -> TokenResponse:
        user = get_user_by_username(body.username)
        if not user or not verify_password(body.password, user["password_hash"]):
            # constant-ish time on miss
            verify_password("x", "$argon2id$v=19$m=65536,t=3,p=4$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAA")
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
        if not user["is_active"]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "account disabled")
        update_last_login(user["id"])
        token = create_access_token(str(user["id"]), {"u": user["username"], "a": bool(user["is_admin"])})
        return TokenResponse(
            access_token=token,
            expires_in=settings.access_token_expire_minutes * 60,
            user_id=user["id"],
            username=user["username"],
            is_admin=bool(user["is_admin"]),
        )

    # ---------------- Users (admin) ----------------
    @app.post("/users", response_model=UserOut, tags=["users"], status_code=201)
    async def create_user_endpoint(
        body: UserCreate,
        _: Annotated[dict, Depends(admin_only)],
    ) -> UserOut:
        from .auth import get_user_by_username as _get
        if _get(body.username):
            raise HTTPException(409, "username already exists")
        uid = create_user(
            body.username, body.password,
            display_name=body.display_name, is_admin=body.is_admin,
        )
        from .auth import get_user
        return UserOut(**get_user(uid))

    @app.get("/users", response_model=list[UserOut], tags=["users"])
    async def list_users_endpoint(
        _: Annotated[dict, Depends(admin_only)],
    ) -> list[UserOut]:
        with get_conn() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
        return [UserOut(**dict(r)) for r in rows]

    @app.get("/users/me", response_model=UserOut, tags=["users"])
    async def me(user: Annotated[dict, Depends(current_user)]) -> UserOut:
        return UserOut(**user)

    @app.patch("/users/{user_id}", response_model=UserOut, tags=["users"])
    async def update_user_endpoint(
        user_id: int,
        body: UserUpdate,
        admin: Annotated[dict, Depends(admin_only)],
    ) -> UserOut:
        from .auth import get_user
        target = get_user(user_id)
        if not target:
            raise HTTPException(404, "user not found")
        sets, args = [], []
        if body.password is not None:
            sets.append("password_hash = ?"); args.append(hash_password(body.password))
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
        return UserOut(**get_user(user_id))

    @app.delete("/users/{user_id}", tags=["users"], status_code=204)
    async def delete_user_endpoint(
        user_id: int,
        admin: Annotated[dict, Depends(admin_only)],
    ) -> Response:
        if user_id == admin["id"]:
            raise HTTPException(400, "cannot delete yourself")
        from .auth import get_user
        if not get_user(user_id):
            raise HTTPException(404, "user not found")
        with get_conn() as conn:
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return Response(status_code=204)

    # ---------------- Print ----------------
    @app.post("/print", response_model=JobOut, tags=["print"], status_code=201)
    @limiter.limit("30/minute")
    async def upload_and_print(
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
        if orientation and orientation not in {"portrait", "landscape", "auto", ""}:
            raise HTTPException(422, "orientation must be portrait/landscape/auto")
        if layout_direction and layout_direction not in {"ltr", "rtl", ""}:
            raise HTTPException(422, "layout_direction must be ltr/rtl")
        if scaling and scaling not in {"fit", "actual", "shrink"} and not scaling.isdigit():
            raise HTTPException(422, "scaling must be fit/actual/shrink or a number 1-200")

        mime = file.content_type or "application/octet-stream"
        if settings.allowed_mimes and mime not in settings.allowed_mimes:
            raise HTTPException(415, f"unsupported file type: {mime}")

        # stream to spool, enforce size limit
        suffix = Path(file.filename or "upload").suffix.lower()[:16]
        spool_name = f"{int(time.time()*1000)}_{secrets.token_hex(4)}_{user['id']}{suffix}"
        spool_path = settings.spool_dir / spool_name
        max_bytes = settings.max_upload_mb * 1024 * 1024
        size = 0
        try:
            with spool_path.open("wb") as f:
                while chunk := file.file.read(64 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        f.close()
                        spool_path.unlink(missing_ok=True)
                        raise HTTPException(413, f"file too large (>{settings.max_upload_mb} MB)")
                    f.write(chunk)
        finally:
            file.file.close()

        # Determine if this Office file needs conversion.
        convert_it = needs_conversion(mime)
        if convert_it:
            # STUB: iMac relay not wired up yet. Store the file, mark it
            # pending-conversion, and return immediately. When the iMac arrives,
            # this branch will ship the file to the watched folder instead.
            with get_conn() as conn:
                cur = conn.execute(
                    """INSERT INTO jobs
                       (user_id, filename, mime_type, size_bytes, copies, duplex, color_mode,
                        page_range, paper_size, orientation, scaling, layout_direction,
                        cups_job_id, status, error, needs_conversion, spool_path, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL,'pending-conversion',
                               'Office conversion will be available when the iMac is connected.',
                               1, ?, ?)""",
                    (user["id"], file.filename or spool_name, mime, size,
                     copies, int(duplex), color_mode,
                     page_range or None, paper_size or None, orientation or None,
                     scaling or None, layout_direction or None,
                     str(spool_path), int(time.time())),
                )
                job_id = cur.lastrowid
                row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            d = dict(row); d["username"] = user["username"]
            return JobOut(**d)

        # PDF (or image/text) path — submit directly to CUPS.
        ps = PrintSettings(
            copies=copies, duplex=duplex, color_mode=color_mode,
            page_range=page_range or None,
            paper_size=paper_size or None,
            orientation=orientation or None,
            scaling=scaling or None,
            layout_direction=layout_direction or None,
        )
        try:
            cups_id, lp_out = submit_job(
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
                 cups_id, job_status, err, str(spool_path), int(time.time())),
            )
            job_id = cur.lastrowid
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        d = dict(row); d["username"] = user["username"]
        return JobOut(**d)

    # ---------------- PDF inspection / preview ----------------

    @app.post("/pdf-info", response_model=PdfInfoResponse, tags=["print"])
    @limiter.limit("30/minute")
    async def get_pdf_info(
        request: Request,
        user: Annotated[dict, Depends(current_user)],
        file: UploadFile = File(..., description="PDF to inspect"),
    ) -> PdfInfoResponse:
        """Upload a PDF and get back its page count + page size, without printing.

        The iOS client uses this to know how many pages the page-range picker
        should offer. The file is streamed to a temp spool slot and can be
        immediately queried via /preview.
        """
        mime = file.content_type or "application/octet-stream"
        suffix = Path(file.filename or "upload").suffix.lower()[:16]
        spool_name = f"info_{int(time.time()*1000)}_{secrets.token_hex(4)}_{user['id']}{suffix}"
        spool_path = settings.spool_dir / spool_name
        max_bytes = settings.max_upload_mb * 1024 * 1024
        size = 0
        try:
            with spool_path.open("wb") as f:
                while chunk := file.file.read(64 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        f.close(); spool_path.unlink(missing_ok=True)
                        raise HTTPException(413, "file too large")
                    f.write(chunk)
        finally:
            file.file.close()

        if not is_pdf(spool_path):
            # Not a PDF — clean up and tell the client.
            spool_path.unlink(missing_ok=True)
            return PdfInfoResponse(
                pages=0, page_size=None, is_pdf=False,
                needs_conversion=needs_conversion(mime),
            )
        meta = pdf_info(spool_path)
        return PdfInfoResponse(
            pages=meta.pages, page_size=meta.page_size,
            is_pdf=True, spool_file=spool_name,
        )

    @app.get("/preview", response_model=PagePreviewResponse, tags=["print"])
    @limiter.limit("600/minute")
    async def get_preview(
        request: Request,
        user: Annotated[dict, Depends(current_user)],
        spool_file: str = Query(..., description="spool filename returned by /pdf-info upload"),
        page: int = Query(1, ge=1),
        width: int = Query(300, ge=64, le=1024),
    ) -> PagePreviewResponse:
        """Render a single page of a previously-uploaded PDF as a JPEG thumbnail."""
        # sanitize: only allow a bare filename inside the spool dir
        safe = Path(spool_file).name
        spool_path = settings.spool_dir / safe
        if not spool_path.exists():
            raise HTTPException(404, "preview file not found (may have expired)")
        if not is_pdf(spool_path):
            raise HTTPException(400, "not a PDF")
        result = render_page_thumbnail(spool_path, page=page, width_px=width)
        if result is None:
            raise HTTPException(500, "could not render page")
        data, w, h = result
        import base64
        return PagePreviewResponse(
            page=page,
            image_base64=base64.b64encode(data).decode("ascii"),
            width=w, height=h,
        )

    @app.get("/jobs", response_model=list[JobOut], tags=["print"])
    async def list_my_jobs(
        user: Annotated[dict, Depends(current_user)],
        limit: int = Query(20, ge=1, le=100),
        include_all: bool = Query(False, description="admin: list all users"),
    ) -> list[JobOut]:
        admin = bool(user["is_admin"]) and include_all
        sql = (
            "SELECT j.*, u.username FROM jobs j JOIN users u ON u.id = j.user_id "
            + ("" if admin else "WHERE j.user_id = ? ")
            + "ORDER BY j.id DESC LIMIT ?"
        )
        params: tuple = () if admin else (user["id"], limit)
        if admin:
            params = (limit,)
        with get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [JobOut(**dict(r)) for r in rows]

    @app.get("/jobs/{job_id}", response_model=JobOut, tags=["print"])
    async def get_job_endpoint(
        job_id: int,
        user: Annotated[dict, Depends(current_user)],
    ) -> JobOut:
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
        return JobOut(**d)

    @app.post("/jobs/{job_id}/refresh", response_model=JobOut, tags=["print"])
    async def refresh_job(
        job_id: int,
        user: Annotated[dict, Depends(current_user)],
    ) -> JobOut:
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
            if d["cups_job_id"] and d["status"] not in ("done", "cancelled", "error"):
                st = get_job_state(d["cups_job_id"])
                if st == "done":
                    conn.execute(
                        "UPDATE jobs SET status='done', printed_at=? WHERE id=?",
                        (int(time.time()), job_id),
                    )
                    d["status"] = "done"
                    d["printed_at"] = int(time.time())
                elif st != "unknown":
                    d["status"] = st
                    conn.execute("UPDATE jobs SET status=? WHERE id=?", (st, job_id))
                row2 = conn.execute(
                    "SELECT j.*, u.username FROM jobs j JOIN users u ON u.id = j.user_id WHERE j.id = ?",
                    (job_id,),
                ).fetchone()
                d = dict(row2)
        return JobOut(**d)

    @app.delete("/jobs/{job_id}", tags=["print"], status_code=204)
    async def cancel_job_endpoint(
        job_id: int,
        user: Annotated[dict, Depends(current_user)],
    ) -> Response:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                raise HTTPException(404, "job not found")
            if row["user_id"] != user["id"] and not user["is_admin"]:
                raise HTTPException(403, "forbidden")
            if row["cups_job_id"]:
                from .cups import cancel_job
                cancel_job(row["cups_job_id"])
            conn.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (job_id,))
        return Response(status_code=204)

    # ---------------- Misc ----------------
    @app.get("/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        import socket
        reachable = False
        try:
            # Try to talk to CUPS socket
            s = socket.socket(socket.AF_UNIX); s.settimeout(1)
            s.connect("/run/cups/cups.sock"); s.close()
            reachable = True
        except OSError:
            reachable = False
        return HealthResponse(status="ok", version=__version__, queue=settings.cups_queue, cups_reachable=reachable)

    return app


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
