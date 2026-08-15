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
    password: Optional[str] = None
    display_name: Optional[str] = None
    is_admin: Optional[bool] = None
    is_active: Optional[bool] = None


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
    status: str
    error: Optional[str]
    needs_conversion: bool = False
    spool_path: Optional[str] = None
    created_at: int
    printed_at: Optional[int]


class JobStatusUpdate(BaseModel):
    status: str
    error: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    version: str
    queue: str
    cups_reachable: bool


class PdfInfoResponse(BaseModel):
    pages: int
    page_size: Optional[str] = None
    is_pdf: bool
    spool_file: Optional[str] = None
    needs_conversion: bool = False


class PagePreviewResponse(BaseModel):
    page: int
    image_base64: str
    width: int
    height: int
