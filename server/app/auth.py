"""Password hashing, JWT issue/verify, user CRUD."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from .config import settings
from .db import get_conn

_pwd = CryptContext(schemes=["argon2"], deprecated="auto")

# A real argon2 hash of a random string, generated once at import time.
#
# Used to burn the same amount of CPU on a failed username lookup as on a real
# password check, closing the username-enumeration timing channel. The previous
# code used a hardcoded literal with a malformed salt/digest, so passlib raised
# ValueError and returned instantly — the mitigation did nothing.
_DUMMY_HASH = _pwd.hash("not-a-real-password-" + __name__)


def hash_password(plain: str) -> str:
    return _pwd.hash(plain)


def burn_password_time() -> None:
    """Spend one argon2 verification's worth of time, discarding the result."""
    _pwd.verify("x", _DUMMY_HASH)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _pwd.verify(plain, hashed)
    except (ValueError, TypeError):
        return False


def create_user(
    username: str,
    password: str,
    *,
    display_name: str | None = None,
    is_admin: bool = False,
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, display_name, is_admin, is_active, created_at) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (username, hash_password(password), display_name or username, int(is_admin), int(time.time())),
        )
        return cur.lastrowid


def get_user_by_username(username: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None


def get_user(user_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def update_last_login(user_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (int(time.time()), user_id))


LOGIN_MAX_FAILURES = 10
LOGIN_LOCKOUT_SECONDS = 300


def register_failed_login(user_id: int) -> None:
    """Count a failed attempt and lock the account once the threshold is hit."""
    now = int(time.time())
    with get_conn() as conn:
        conn.execute(
            """UPDATE users
                  SET failed_logins = failed_logins + 1,
                      lockout_until = CASE WHEN failed_logins + 1 >= ?
                                           THEN ? ELSE lockout_until END
                WHERE id = ?""",
            (LOGIN_MAX_FAILURES, now + LOGIN_LOCKOUT_SECONDS, user_id),
        )


def clear_failed_logins(user_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET failed_logins = 0, lockout_until = NULL WHERE id = ?",
            (user_id,),
        )


def lockout_remaining(user: dict) -> int:
    """Seconds left on this account's lockout, or 0 if not locked out."""
    until = user.get("lockout_until") or 0
    return max(0, int(until) - int(time.time()))


def bump_token_version(user_id: int) -> None:
    """Invalidate every JWT already issued to this user.

    Called on password change. Tokens carry the version they were minted with
    (the "tv" claim); current_user rejects any token whose "tv" is behind the
    row's current value.
    """
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET token_version = token_version + 1 WHERE id = ?",
            (user_id,),
        )


def _require_secret() -> str:
    """Return the signing key, refusing to sign with an empty one.

    settings.secret_key is populated by Settings.ensure_dirs() (from
    data_dir/secret_key, or freshly generated). If anything ever calls this
    before that has run, signing would silently succeed with an empty key and
    produce tokens that nothing can verify. Fail loudly instead.
    """
    if not settings.secret_key:
        raise RuntimeError(
            "JWT secret key is not initialised — settings.ensure_dirs() must run first"
        )
    return settings.secret_key


def create_access_token(subject: str, extra: dict | None = None) -> str:
    key = _require_secret()
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.access_token_expire_minutes)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, _require_secret(), algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
