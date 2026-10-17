"""Pydantic request/response schemas."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: int
    username: str
    is_admin: bool


class UserCreate(BaseModel):
    username: str = Field(..., min_length=2, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    password: str = Field(..., min_length=6, max_length=256)
    display_name: Optional[str] = None
    is_admin: bool = False


class UserOut(BaseModel):
    id: int
    username: str
    display_name: Optional[str]
    is_admin: bool
    is_active: bool
    created_at: int
    last_login: Optional[int]


class UserUpdate(BaseModel):
    password: Optional[str] = Field(None, min_length=6, max_length=256)
    display_name: Optional[str] = None
    is_admin: Optional[bool] = None
    is_active: Optional[bool] = None


class PasswordChange(BaseModel):
    """Self-service update via PATCH /users/me.

    Changing the password requires proving knowledge of the current one, and
    invalidates every JWT already issued to the account.
    """
    current_password: Optional[str] = Field(None, max_length=256)
    new_password: Optional[str] = Field(None, min_length=6, max_length=256)
    display_name: Optional[str] = Field(None, max_length=128)


class JobOut(BaseModel):
    id: int
    user_id: int
    username: Optional[str] = None
    filename: str
    mime_type: Optional[str]
    size_bytes: int
    copies: int
    duplex: bool
    color_mode: str
    page_range: Optional[str] = None
    paper_size: Optional[str] = None
    orientation: Optional[str] = None
    scaling: Optional[str] = None
    layout_direction: Optional[str] = None
    cups_job_id: Optional[int]
    # queued | converting | pending | processing | done | cancelled | error.
    # 'converting' means an Office file is being turned into a PDF and has not
    # reached CUPS yet, so cups_job_id is still null.
    status: str
    error: Optional[str]
    needs_conversion: bool = False
    # spool_path is deliberately NOT exposed: it leaked server filesystem paths
    # to every client and no client ever used it (L4).
    created_at: int
    printed_at: Optional[int]


class OfficeStatus(BaseModel):
    """Converter availability; only the AppleScript backend needs a GUI/license."""
    enabled: bool
    backend: str = "applescript"
    requires_gui: bool = True
    available: bool
    gui_session: bool
    script_present: bool
    # False when Office has never been signed in on this machine. An unactivated
    # Word hangs in its first-run wizard rather than failing, so a job just
    # times out with nothing to point at.
    licensed: bool = False
    apps: dict[str, bool] = {}


class HealthResponse(BaseModel):
    status: str
    version: str
    queue: str
    cups_reachable: bool
    # True when the CUPS queue has no PPD/driver, in which case CUPS ignores
    # page-ranges, duplex, media, orientation and scaling entirely (F1).
    queue_is_raw: bool = False
    queue_options: list[str] = []
    office: Optional[OfficeStatus] = None
