"""Password hashing, JWT issuing/verification, and the current-user dependency."""

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer

from config import JWT_SECRET_KEY
from db import get_cursor

JWT_ALGORITHM = "HS256"
JWT_EXPIRES_MINUTES = 60 * 24 * 7  # 7 days

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

USER_BY_ID_SQL = """
    SELECT id, full_name, email, password_hash, subscribers, monthly_views,
           channel_url, channel_data, channel_fetch_error, created_at
    FROM users
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
    return user
