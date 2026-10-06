"""HTTP schemas and dependencies."""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import airprint
from .auth import decode_token, get_user

bearer_scheme = HTTPBearer(auto_error=False)


async def current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> dict:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    payload = decode_token(creds.credentials)
    if payload is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token")
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "malformed token")
    user = get_user(user_id)
    if user is None or not user["is_active"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "user inactive or unknown")
    # Reject tokens minted before the user's last password change (H8). Tokens
    # issued before this column existed carry no "tv" claim and default to 0,
    # which matches the default for pre-existing rows.
    if int(payload.get("tv", 0)) != int(user["token_version"] or 0):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token revoked, please sign in again")
    return user


async def admin_only(user: Annotated[dict, Depends(current_user)]) -> dict:
    if not user["is_admin"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin privileges required")
    return user


async def print_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    x_print_key: Annotated[str | None, Header()] = None,
) -> dict:
    """The submitting user, from a bearer token or an approved device key.

    The key is what an iOS Shortcut sends; it maps to the same user the
    student's AirPrint profile does.
    """
    if x_print_key is None:
        return await current_user(creds)
    row = airprint.resolve_token(x_print_key.strip())
    user = get_user(row["user_id"]) if row else None
    if user is None or not user["is_active"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid print key")
    return user
