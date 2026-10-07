"""Admin dashboard API: its own login, platform overview, user management and
the audit log.

Admins are separate accounts (the `admins` table, created with
`python -m tools.create_admin`) with their own tokens; see security.py. Every
route except login depends on get_current_admin, and every write is recorded
in admin_audit_log in the same transaction as the change it describes.
Queries are shaped for few round trips (see admin_common.py).
"""

import threading
import time
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.security import OAuth2PasswordRequestForm

from db import get_cursor
from inference import get_state
from models import (
    AdminAuditList,
    AdminAuthResponse,
    AdminDailyPoint,
    AdminDeleteUserRequest,
    AdminForecastSplit,
    AdminOut,
    AdminOverview,
    AdminPredictionCounts,
    AdminSystemStatus,
    AdminUserCounts,
    AdminUserDetail,
    AdminUserList,
)
from routers.admin_common import AUDIT_ROWS_SQL, like_pattern, record_action
from routers.channel import channel_out, refresh_user_channel
from security import create_admin_token, get_current_admin, verify_password
from storage import delete_upload

router = APIRouter(prefix="/admin", tags=["admin"])

# ---- Login throttling ---------------------------------------------------------
# In-process, per email: enough to stop password guessing against the one
# high-value login. Resets on restart, and is per worker.

MAX_FAILED_LOGINS = 5
FAILED_LOGIN_WINDOW_SECONDS = 15 * 60

_failed_logins: dict[str, list[float]] = {}
_failed_logins_lock = threading.Lock()


def _recent_failures(email: str, now: float) -> list[float]:
    recent = [t for t in _failed_logins.get(email, []) if now - t < FAILED_LOGIN_WINDOW_SECONDS]
    _failed_logins[email] = recent
    return recent


def _check_throttle(email: str) -> None:
    with _failed_logins_lock:
        if len(_recent_failures(email, time.monotonic())) >= MAX_FAILED_LOGINS:
            raise HTTPException(status_code=429, detail="Too many failed attempts. Try again in 15 minutes.")


def _record_failure(email: str) -> None:
    with _failed_logins_lock:
        now = time.monotonic()
        _recent_failures(email, now).append(now)


def _clear_failures(email: str) -> None:
    with _failed_logins_lock:
        _failed_logins.pop(email, None)


def admin_out(admin: dict) -> AdminOut:
    return AdminOut(
        id=admin["id"], full_name=admin["full_name"], email=admin["email"], last_login_at=admin.get("last_login_at"),
    )


# ---- Auth ---------------------------------------------------------------------

SELECT_ADMIN_BY_EMAIL_SQL = """
    SELECT id, full_name, email, password_hash, is_active, last_login_at, created_at
    FROM admins
    WHERE email = %(email)s
"""

RECORD_LOGIN_SQL = """
    WITH touched AS (
        UPDATE admins SET last_login_at = NOW() WHERE id = %(id)s RETURNING id
    )
    INSERT INTO admin_audit_log (admin_id, action, details)
    SELECT id, 'admin.login', '{}'::jsonb FROM touched
"""


@router.post("/auth/login", response_model=AdminAuthResponse)
def admin_login(form_data: OAuth2PasswordRequestForm = Depends()):
    email = form_data.username.strip().lower()
    _check_throttle(email)

    with get_cursor() as cur:
        cur.execute(SELECT_ADMIN_BY_EMAIL_SQL, {"email": email})
        columns = [col.name for col in cur.description]
        row = cur.fetchone()

    admin = dict(zip(columns, row)) if row else None
    # A disabled admin gets the same answer as a wrong password.
    if admin is None or not admin["is_active"] or not verify_password(form_data.password, admin["password_hash"]):
        _record_failure(email)
        raise HTTPException(status_code=401, detail="Incorrect email or password")

    _clear_failures(email)
    with get_cursor(commit=True) as cur:
        cur.execute(RECORD_LOGIN_SQL, {"id": admin["id"]})

    return AdminAuthResponse(access_token=create_admin_token(admin["id"]), admin=admin_out(admin))


@router.get("/auth/me", response_model=AdminOut)
def admin_me(admin: dict = Depends(get_current_admin)):
    return admin_out(admin)


# ---- Overview -------------------------------------------------------------------

# One statement for the whole page. Before the ensemble went live every
# forecast saved used_channel_context = TRUE, so the model split only means
# something from `since` on (the frontend passes its ENSEMBLE_SINCE). The
# daily series covers the last 30 UTC days, oldest first, zero-filled.
OVERVIEW_SQL = """
    WITH days AS (
        SELECT generate_series(
            (NOW() AT TIME ZONE 'UTC')::date - 29,
            (NOW() AT TIME ZONE 'UTC')::date,
            INTERVAL '1 day'
        )::date AS day
    ),
    signups AS (
        SELECT (created_at AT TIME ZONE 'UTC')::date AS day, COUNT(*) AS n
        FROM users
        WHERE created_at >= NOW() - INTERVAL '31 days'
        GROUP BY 1
    ),
    preds AS (
        SELECT
            (created_at AT TIME ZONE 'UTC')::date AS day,
            COUNT(*) AS n,
            COUNT(*) FILTER (WHERE status = 'complete' AND used_channel_context IS TRUE
                             AND created_at >= COALESCE(%(since)s::timestamptz, '-infinity')) AS ensemble,
            COUNT(*) FILTER (WHERE status = 'complete' AND used_channel_context IS FALSE
                             AND created_at >= COALESCE(%(since)s::timestamptz, '-infinity')) AS catboost_only
        FROM predictions
        WHERE created_at >= NOW() - INTERVAL '31 days'
        GROUP BY 1
    )
    SELECT
        (SELECT row_to_json(u) FROM (
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE is_active) AS active,
                COUNT(*) FILTER (WHERE NOT is_active) AS disabled,
                COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days') AS new_7d,
                COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days') AS new_30d,
                COUNT(*) FILTER (WHERE channel_data->>'channel_id' IS NOT NULL) AS with_channel,
                COUNT(*) FILTER (WHERE channel_fetch_error IS NOT NULL) AS with_fetch_error
            FROM users
        ) u),
        (SELECT row_to_json(p) FROM (
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status = 'complete') AS complete,
                COUNT(*) FILTER (WHERE status = 'draft') AS draft,
                COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days') AS last_7d
            FROM predictions
        ) p),
        (SELECT row_to_json(f) FROM (
            SELECT
                COUNT(*) FILTER (WHERE used_channel_context IS TRUE) AS ensemble,
                COUNT(*) FILTER (WHERE used_channel_context IS FALSE) AS catboost_only
            FROM predictions
            WHERE status = 'complete' AND created_at >= COALESCE(%(since)s::timestamptz, '-infinity')
        ) f),
        COALESCE((SELECT json_agg(d ORDER BY d.day) FROM (
            SELECT days.day,
                   COALESCE(signups.n, 0) AS signups,
                   COALESCE(preds.n, 0) AS predictions,
                   COALESCE(preds.ensemble, 0) AS ensemble,
                   COALESCE(preds.catboost_only, 0) AS catboost_only
            FROM days
            LEFT JOIN signups USING (day)
            LEFT JOIN preds USING (day)
        ) d), '[]'::json)
"""


def system_status() -> AdminSystemStatus:
    state = get_state()
    return AdminSystemStatus(
        model_ready=bool(getattr(state, "ready", False)),
        model_error=getattr(state, "error", None),
        model_device=getattr(state, "device", None),
        model_load_time_seconds=getattr(state, "load_time_seconds", None),
    )


@router.get("/overview", response_model=AdminOverview)
def get_overview(ensemble_since: datetime | None = None, admin: dict = Depends(get_current_admin)):
    with get_cursor() as cur:
        cur.execute(OVERVIEW_SQL, {"since": ensemble_since})
        users, predictions, split, daily = cur.fetchone()

    return AdminOverview(
        users=AdminUserCounts(**users),
        predictions=AdminPredictionCounts(**predictions),
        forecasts=AdminForecastSplit(since=ensemble_since, **split),
        daily=[AdminDailyPoint(**d) for d in daily],
        system=system_status(),
    )


# ---- Users --------------------------------------------------------------------

LIST_USERS_SQL = """
    WITH filtered AS (
        SELECT u.id, u.full_name, u.email, u.is_active, u.created_at, u.subscribers, u.channel_url,
               u.channel_data->>'title' AS channel_title,
               u.channel_data->>'thumbnail_url' AS channel_thumbnail_url,
               u.channel_fetch_error IS NOT NULL AS has_fetch_error
        FROM users u
        WHERE (%(pattern)s::text IS NULL
               OR u.full_name ILIKE %(pattern)s
               OR u.email ILIKE %(pattern)s
               OR u.channel_data->>'title' ILIKE %(pattern)s)
          AND (%(active)s::boolean IS NULL OR u.is_active = %(active)s::boolean)
    ),
    page AS (
        SELECT f.*, s.prediction_count, s.last_prediction_at
        FROM (
            SELECT * FROM filtered ORDER BY created_at DESC, id DESC LIMIT %(limit)s OFFSET %(offset)s
        ) f
        CROSS JOIN LATERAL (
            SELECT COUNT(*) AS prediction_count, MAX(created_at) AS last_prediction_at
            FROM predictions WHERE user_id = f.id
        ) s
    )
    SELECT (SELECT COUNT(*) FROM filtered),
           COALESCE((SELECT json_agg(page ORDER BY created_at DESC, id DESC) FROM page), '[]'::json)
"""


@router.get("/users", response_model=AdminUserList)
def list_users(
    q: str | None = None,
    status: Literal["all", "active", "disabled"] = "all",
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    admin: dict = Depends(get_current_admin),
):
    params = {
        "pattern": like_pattern(q),
        "active": {"all": None, "active": True, "disabled": False}[status],
        "limit": limit,
        "offset": offset,
    }
    with get_cursor() as cur:
        cur.execute(LIST_USERS_SQL, params)
        total, items = cur.fetchone()
    return AdminUserList(total=total, items=items)


USER_DETAIL_SQL = """
    SELECT
        row_to_json(u),
        (SELECT row_to_json(s) FROM (
            SELECT
                (SELECT COUNT(*) FROM predictions WHERE user_id = u.id) AS prediction_count,
                (SELECT COUNT(*) FROM predictions WHERE user_id = u.id AND status = 'complete') AS complete_count,
                (SELECT COUNT(*) FROM predictions WHERE user_id = u.id AND status = 'draft') AS draft_count,
                (SELECT COUNT(*) FROM notifications WHERE user_id = u.id) AS notification_count,
                (SELECT COUNT(*) FROM notifications WHERE user_id = u.id AND NOT read) AS unread_notification_count
        ) s),
        COALESCE((SELECT json_agg(p) FROM (
            SELECT id, title, category, status, predicted_views, confidence, used_channel_context, created_at
            FROM predictions
            WHERE user_id = u.id
            ORDER BY created_at DESC, id DESC
            LIMIT 20
        ) p), '[]'::json),
        COALESCE((SELECT json_agg(a) FROM (
            """ + AUDIT_ROWS_SQL + """
            WHERE l.target_type = 'user' AND l.target_id = u.id
            ORDER BY l.created_at DESC, l.id DESC
            LIMIT 20
        ) a), '[]'::json)
    FROM (
        SELECT id, full_name, email, is_active, created_at, subscribers, monthly_views,
               channel_url, channel_data, channel_fetch_error
        FROM users
        WHERE id = %(id)s
    ) u
"""


def user_detail(cur, user_id: int) -> AdminUserDetail:
    """One round trip; run on the same cursor as a write to see its result."""
    cur.execute(USER_DETAIL_SQL, {"id": user_id})
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    user, stats, predictions, activity = row
    return AdminUserDetail(
        **{k: user[k] for k in ("id", "full_name", "email", "is_active", "created_at", "subscribers", "monthly_views")},
        channel=channel_out(user["channel_url"], user["channel_data"], user["channel_fetch_error"]),
        **stats,
        recent_predictions=predictions,
        recent_activity=activity,
    )


@router.get("/users/{user_id}", response_model=AdminUserDetail)
def get_user(user_id: int, admin: dict = Depends(get_current_admin)):
    with get_cursor() as cur:
        return user_detail(cur, user_id)


# Locks the row, flips the flag and logs it in one statement; nothing is
# logged when the flag already had that value (a repeated click).
SET_ACTIVE_SQL = """
    WITH target AS (
        SELECT id, email, is_active FROM users WHERE id = %(id)s FOR UPDATE
    ),
    changed AS (
        UPDATE users SET is_active = %(active)s
        FROM target
        WHERE users.id = target.id AND target.is_active <> %(active)s
        RETURNING users.id
    ),
    logged AS (
        INSERT INTO admin_audit_log (admin_id, action, target_type, target_id, details)
        SELECT %(admin_id)s, %(action)s, 'user', target.id, jsonb_build_object('email', target.email)
        FROM target
        WHERE EXISTS (SELECT 1 FROM changed)
    )
    SELECT COUNT(*) FROM target
"""


def _set_active(user_id: int, active: bool, admin: dict) -> AdminUserDetail:
    with get_cursor(commit=True) as cur:
        cur.execute(SET_ACTIVE_SQL, {
            "id": user_id, "active": active, "admin_id": admin["id"],
            "action": "user.enable" if active else "user.disable",
        })
        if cur.fetchone()[0] == 0:
            raise HTTPException(status_code=404, detail="User not found")
        return user_detail(cur, user_id)


@router.post("/users/{user_id}/disable", response_model=AdminUserDetail)
def disable_user(user_id: int, admin: dict = Depends(get_current_admin)):
    return _set_active(user_id, False, admin)


@router.post("/users/{user_id}/enable", response_model=AdminUserDetail)
def enable_user(user_id: int, admin: dict = Depends(get_current_admin)):
    return _set_active(user_id, True, admin)


@router.post("/users/{user_id}/refresh-channel", response_model=AdminUserDetail)
def refresh_channel(user_id: int, background_tasks: BackgroundTasks, admin: dict = Depends(get_current_admin)):
    """Same as the user's own /channel/refresh: re-resolves the channel URL,
    notifies the user, and warms the forecast's channel history cache."""
    with get_cursor() as cur:
        cur.execute("SELECT channel_url FROM users WHERE id = %(id)s", {"id": user_id})
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    if not row[0]:
        raise HTTPException(status_code=400, detail="This user has no linked channel")

    channel_data, fetch_error = refresh_user_channel(user_id, row[0], background_tasks)
    with get_cursor(commit=True) as cur:
        record_action(cur, admin, "user.refresh_channel", target_type="user", target_id=user_id,
                      details={"ok": channel_data is not None, "error": fetch_error})
        return user_detail(cur, user_id)


CLEAR_FETCH_ERROR_SQL = """
    WITH target AS (
        SELECT id, channel_fetch_error FROM users WHERE id = %(id)s FOR UPDATE
    ),
    changed AS (
        UPDATE users SET channel_fetch_error = NULL
        FROM target
        WHERE users.id = target.id AND target.channel_fetch_error IS NOT NULL
        RETURNING users.id
    ),
    logged AS (
        INSERT INTO admin_audit_log (admin_id, action, target_type, target_id, details)
        SELECT %(admin_id)s, 'user.clear_fetch_error', 'user', target.id,
               jsonb_build_object('error', target.channel_fetch_error)
        FROM target
        WHERE EXISTS (SELECT 1 FROM changed)
    )
    SELECT COUNT(*) FROM target
"""


@router.post("/users/{user_id}/clear-fetch-error", response_model=AdminUserDetail)
def clear_fetch_error(user_id: int, admin: dict = Depends(get_current_admin)):
    with get_cursor(commit=True) as cur:
        cur.execute(CLEAR_FETCH_ERROR_SQL, {"id": user_id, "admin_id": admin["id"]})
        if cur.fetchone()[0] == 0:
            raise HTTPException(status_code=404, detail="User not found")
        return user_detail(cur, user_id)


SELECT_USER_FOR_DELETE_SQL = """
    SELECT u.email, u.full_name, u.channel_url,
           ARRAY(SELECT path FROM predictions p,
                 LATERAL unnest(ARRAY[p.thumbnail_path, p.dataset_path]) AS path
                 WHERE p.user_id = u.id AND path IS NOT NULL)
    FROM users u
    WHERE u.id = %(id)s
    FOR UPDATE
"""

DELETE_USER_SQL = "DELETE FROM users WHERE id = %(id)s"


@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: int, request: AdminDeleteUserRequest, admin: dict = Depends(get_current_admin)):
    """Deletes the account and (by cascade) its predictions and notifications,
    then removes the uploaded files those predictions pointed at."""
    with get_cursor(commit=True) as cur:
        cur.execute(SELECT_USER_FOR_DELETE_SQL, {"id": user_id})
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="User not found")
        email, full_name, channel_url, uploads = row
        if request.confirm_email.strip() != email:
            raise HTTPException(status_code=400, detail="The confirmation email does not match this user")
        cur.execute(DELETE_USER_SQL, {"id": user_id})
        record_action(cur, admin, "user.delete", target_type="user", target_id=user_id, details={
            "email": email, "full_name": full_name, "channel_url": channel_url,
        })

    # After the commit: a failed delete must not leave predictions pointing at missing files.
    for path in uploads:
        delete_upload(path)


# ---- Audit log ----------------------------------------------------------------

LIST_AUDIT_SQL = """
    WITH filtered AS (
        """ + AUDIT_ROWS_SQL + """
        WHERE (%(action)s::text IS NULL OR l.action = %(action)s::text)
    ),
    page AS (
        SELECT * FROM filtered ORDER BY created_at DESC, id DESC LIMIT %(limit)s OFFSET %(offset)s
    )
    SELECT (SELECT COUNT(*) FROM filtered),
           COALESCE((SELECT json_agg(page ORDER BY created_at DESC, id DESC) FROM page), '[]'::json)
"""


@router.get("/audit-log", response_model=AdminAuditList)
def list_audit_log(
    action: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin: dict = Depends(get_current_admin),
):
    with get_cursor() as cur:
        cur.execute(LIST_AUDIT_SQL, {"action": action or None, "limit": limit, "offset": offset})
        total, items = cur.fetchone()
    return AdminAuditList(total=total, items=items)

