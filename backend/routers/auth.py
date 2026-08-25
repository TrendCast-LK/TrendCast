import psycopg2
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm

from db import get_cursor
from models import (
    AuthResponse,
    ChangePasswordRequest,
    SignupRequest,
    UpdateProfileRequest,
    UserOut,
)
from routers.channel import refresh_user_channel
from routers.notifications import create_notification
from security import create_access_token, get_current_user, get_user_by_id, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

INSERT_USER_SQL = """
    INSERT INTO users (full_name, email, password_hash, channel_url)
    VALUES (%(full_name)s, %(email)s, %(password_hash)s, %(channel_url)s)
    RETURNING id
"""

SELECT_BY_EMAIL_SQL = """
    SELECT id, full_name, email, password_hash, subscribers, monthly_views,
           channel_url, channel_data, channel_fetch_error, created_at
    FROM users
    WHERE email = %(email)s
"""

UPDATE_SUBSCRIBERS_SQL = "UPDATE users SET subscribers = %(subscribers)s WHERE id = %(id)s"

UPDATE_PASSWORD_SQL = "UPDATE users SET password_hash = %(password_hash)s WHERE id = %(id)s"


def user_out(user: dict) -> UserOut:
    channel_data = user.get("channel_data") or {}
    return UserOut(
        id=user["id"],
        full_name=user["full_name"],
        email=user["email"],
        subscribers=user["subscribers"],
        monthly_views=user["monthly_views"],
        channel_url=user.get("channel_url"),
        channel_thumbnail_url=channel_data.get("thumbnail_url"),
    )


@router.post("/signup", response_model=AuthResponse)
def signup(request: SignupRequest):
    with get_cursor(commit=True) as cur:
        try:
            cur.execute(
                INSERT_USER_SQL,
                {
                    "full_name": request.full_name,
                    "email": request.email,
                    "password_hash": hash_password(request.password),
                    "channel_url": request.channel_url,
                },
            )
        except psycopg2.errors.UniqueViolation:
            raise HTTPException(status_code=400, detail="An account with that email already exists")
        user_id = cur.fetchone()[0]

    create_notification(
        user_id,
        "welcome",
        "Welcome to ViewCast",
        "Your account is ready — try running your first prediction.",
    )

    channel_data, _fetch_error = refresh_user_channel(user_id, request.channel_url)
    if channel_data and channel_data.get("subscriber_count") is not None:
        with get_cursor(commit=True) as cur:
            cur.execute(UPDATE_SUBSCRIBERS_SQL, {"subscribers": channel_data["subscriber_count"], "id": user_id})

    user = get_user_by_id(user_id)
    return AuthResponse(access_token=create_access_token(user_id), user=user_out(user))


@router.post("/login", response_model=AuthResponse)
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    with get_cursor() as cur:
        cur.execute(SELECT_BY_EMAIL_SQL, {"email": form_data.username})
        columns = [col.name for col in cur.description]
        row = cur.fetchone()

    invalid_credentials = HTTPException(status_code=401, detail="Incorrect email or password")
    if row is None:
        raise invalid_credentials

    user = dict(zip(columns, row))
    if not verify_password(form_data.password, user["password_hash"]):
        raise invalid_credentials

    return AuthResponse(access_token=create_access_token(user["id"]), user=user_out(user))


@router.get("/me", response_model=UserOut)
def me(user: dict = Depends(get_current_user)):
    return user_out(user)


@router.patch("/me", response_model=UserOut)
def update_me(request: UpdateProfileRequest, user: dict = Depends(get_current_user)):
    updates = request.model_dump(exclude_unset=True)
    if updates:
        set_clause = ", ".join(f"{field} = %({field})s" for field in updates)
        with get_cursor(commit=True) as cur:
            cur.execute(f"UPDATE users SET {set_clause} WHERE id = %(id)s", {**updates, "id": user["id"]})

    return user_out(get_user_by_id(user["id"]))


@router.post("/change-password")
def change_password(request: ChangePasswordRequest, user: dict = Depends(get_current_user)):
    if not verify_password(request.current_password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    with get_cursor(commit=True) as cur:
        cur.execute(UPDATE_PASSWORD_SQL, {"password_hash": hash_password(request.new_password), "id": user["id"]})

    return {"status": "ok"}
