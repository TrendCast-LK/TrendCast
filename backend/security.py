"""Password hashing, JWT issuing/verification, and the current-user / current-admin dependencies.

App users and admins are separate accounts with separate tokens: admin tokens
carry aud=ADMIN_TOKEN_AUDIENCE, which the user check rejects (PyJWT refuses an
aud claim it was not told to expect), and the admin check requires it."""

import threading
import time
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer

from config import JWT_SECRET_KEY
from db import get_cursor

JWT_ALGORITHM = "HS256"
JWT_EXPIRES_MINUTES = 60 * 24 * 7  # 7 days
ADMIN_JWT_EXPIRES_MINUTES = 60 * 8  # one working day
ADMIN_TOKEN_AUDIENCE = "trendcast-admin"

ACCOUNT_DISABLED = "This account has been disabled. Contact support if you think this is a mistake."

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
admin_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/admin/auth/login", scheme_name="AdminOAuth2")

USER_BY_ID_SQL = """
    SELECT id, full_name, email, password_hash, subscribers, monthly_views,
           channel_url, channel_data, channel_fetch_error, created_at, is_active
    FROM users
    WHERE id = %(id)s
"""

ADMIN_BY_ID_SQL = """
    SELECT id, full_name, email, password_hash, is_active, last_login_at, created_at
    FROM admins
    WHERE id = %(id)s
"""


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_access_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRES_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def _row_to_user(row, columns) -> dict:
    return dict(zip(columns, row))


def get_user_by_id(user_id: int) -> dict | None:
    with get_cursor() as cur:
        cur.execute(USER_BY_ID_SQL, {"id": user_id})
        columns = [col.name for col in cur.description]
        row = cur.fetchone()
    return _row_to_user(row, columns) if row else None


def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    credentials_error = HTTPException(status_code=401, detail="Could not validate credentials")

    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise credentials_error

    user = get_user_by_id(user_id)
    if user is None:
        raise credentials_error
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail=ACCOUNT_DISABLED)
    return user


def create_admin_token(admin_id: int) -> str:
    payload = {
        "sub": str(admin_id),
        "aud": ADMIN_TOKEN_AUDIENCE,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ADMIN_JWT_EXPIRES_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


# Admin rows are cached briefly: each lookup is a database round trip, and the
# dashboard makes several requests per page. Disabling an admin with
# tools/create_admin.py therefore takes up to this long to lock out a token
# already issued (a new login is refused at once).
ADMIN_CACHE_SECONDS = 30
_admin_cache: dict[int, tuple[float, dict]] = {}
_admin_cache_lock = threading.Lock()


def clear_admin_cache() -> None:
    with _admin_cache_lock:
        _admin_cache.clear()


def _cached_admin(admin_id: int) -> dict | None:
    now = time.monotonic()
    with _admin_cache_lock:
        hit = _admin_cache.get(admin_id)
        if hit and now - hit[0] < ADMIN_CACHE_SECONDS:
            return hit[1]
    admin = get_admin_by_id(admin_id)
    with _admin_cache_lock:
        if admin is None:
            _admin_cache.pop(admin_id, None)
        else:
            _admin_cache[admin_id] = (now, admin)
    return admin


def get_admin_by_id(admin_id: int) -> dict | None:
    with get_cursor() as cur:
        cur.execute(ADMIN_BY_ID_SQL, {"id": admin_id})
        columns = [col.name for col in cur.description]
        row = cur.fetchone()
    return dict(zip(columns, row)) if row else None


def get_current_admin(token: str = Depends(admin_oauth2_scheme)) -> dict:
    credentials_error = HTTPException(status_code=401, detail="Could not validate admin credentials")

    try:
        payload = jwt.decode(
            token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM], audience=ADMIN_TOKEN_AUDIENCE,
            options={"require": ["exp", "sub", "aud"]},
        )
        admin_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise credentials_error

    admin = _cached_admin(admin_id)
    if admin is None or not admin["is_active"]:
        raise credentials_error
    return admin
