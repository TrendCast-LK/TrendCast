"""Admin dashboard tests (black box, through the HTTP API).

Covers the separate admin login, the user/admin token boundary, the overview
numbers, user management (disable/enable/refresh/clear/delete) and the audit
log. The YouTube lookup is faked; the routers and database are real.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.routing import APIRoute

from test_api_data import (
    CHANNEL,
    PASSWORD,
    fail_youtube,
    fake_youtube,  # noqa: F401  (autouse fixture: every channel URL resolves to CHANNEL)
    make_user,
    post_prediction,
    rows,
)
from test_api_function import bearer, insert_prediction, main_client, token_for  # noqa: F401

from tools import create_admin as create_admin_tool

ADMIN_EMAIL = "root@trendcast.test"
ADMIN_PASSWORD = "admin-password-123"


@pytest.fixture(autouse=True)
def reset_admin_state(api):
    from security import clear_admin_cache

    api.backend.admin_router._failed_logins.clear()
    clear_admin_cache()
    yield
    api.backend.admin_router._failed_logins.clear()
    clear_admin_cache()


def make_admin(api, email=ADMIN_EMAIL, password=ADMIN_PASSWORD, name="Root"):
    return create_admin_tool.create_admin(api.conn, email, name, password)


def admin_login(api, email=ADMIN_EMAIL, password=ADMIN_PASSWORD):
    return api.client.post("/admin/auth/login", data={"username": email, "password": password})


def admin_headers(api, **kwargs):
    make_admin(api, **kwargs)
    response = admin_login(api, kwargs.get("email", ADMIN_EMAIL), kwargs.get("password", ADMIN_PASSWORD))
    assert response.status_code == 200, response.text
    return bearer(response.json()["access_token"])


def audit_actions(api, target_id=None):
    query = "SELECT action FROM admin_audit_log"
    params = ()
    if target_id is not None:
        query += " WHERE target_type = 'user' AND target_id = %s"
        params = (target_id,)
    return [r[0] for r in rows(api, query + " ORDER BY id", params)]


# ---------------------------------------------------------------------------
# Login and the token boundary
# ---------------------------------------------------------------------------

def test_admin_can_log_in_and_read_their_profile(api):
    admin_id = make_admin(api)
    response = admin_login(api)
    assert response.status_code == 200
    body = response.json()
    assert body["admin"] == {"id": admin_id, "full_name": "Root", "email": ADMIN_EMAIL, "last_login_at": None}
    me = api.client.get("/admin/auth/me", headers=bearer(body["access_token"])).json()
    assert me["email"] == ADMIN_EMAIL and me["last_login_at"] is not None
    assert audit_actions(api) == ["admin.login"]


def test_admin_email_is_case_insensitive(api):
    make_admin(api, email="Root@TrendCast.test")
    assert admin_login(api, email="ROOT@trendcast.TEST").status_code == 200


def test_wrong_password_and_unknown_email_look_identical(api):
    make_admin(api)
    wrong = admin_login(api, password="not-the-password")
    unknown = admin_login(api, email="ghost@trendcast.test")
    assert wrong.status_code == 401
    assert (wrong.status_code, wrong.json()) == (unknown.status_code, unknown.json())


def test_a_disabled_admin_cannot_log_in_or_use_an_existing_token(api):
    from security import ADMIN_CACHE_SECONDS, clear_admin_cache

    headers = admin_headers(api)
    assert api.client.get("/admin/auth/me", headers=headers).status_code == 200
    create_admin_tool.set_active(api.conn, ADMIN_EMAIL, active=False)
    assert admin_login(api).status_code == 401  # a new login is refused at once
    # An issued token keeps working until the cached admin row expires.
    assert ADMIN_CACHE_SECONDS <= 60
    clear_admin_cache()
    assert api.client.get("/admin/auth/me", headers=headers).status_code == 401


def test_repeated_failed_admin_logins_are_throttled(api):
    make_admin(api)
    statuses = [admin_login(api, password=f"guess-{i}").status_code for i in range(6)]
    assert statuses[:5] == [401] * 5 and statuses[5] == 429
    assert admin_login(api).status_code == 429  # even the right password, until the window passes


def test_an_app_user_cannot_log_in_to_the_admin_dashboard(api):
    make_user(api, "ann@example.com")
    assert admin_login(api, email="ann@example.com", password=PASSWORD).status_code == 401


def test_a_user_token_is_rejected_by_admin_routes(api):
    make_admin(api)
    uid, user_headers = make_user(api)
    assert api.client.get("/admin/overview", headers=user_headers).status_code == 401
    # even when the user's id happens to equal an admin's id
    assert api.client.get("/admin/users", headers=bearer(token_for(1))).status_code == 401


def test_an_admin_token_is_rejected_by_user_routes(api):
    headers = admin_headers(api)
    make_user(api)  # user id 1 and admin id 1 coexist
    assert api.client.get("/auth/me", headers=headers).status_code == 401
    assert api.client.get("/predictions", headers=headers).status_code == 401


def test_an_admin_token_signed_with_another_secret_is_rejected(api):
    admin_id = make_admin(api)
    forged = jwt.encode(
        {"sub": str(admin_id), "aud": "trendcast-admin", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        "not-the-real-secret", algorithm="HS256",
    )
    assert api.client.get("/admin/auth/me", headers=bearer(forged)).status_code == 401


def test_admin_tokens_expire_within_a_working_day(api):
    headers = admin_headers(api)
    claims = jwt.decode(headers["Authorization"].split()[1], options={"verify_signature": False})
    assert claims["aud"] == "trendcast-admin"
    assert claims["exp"] - datetime.now(timezone.utc).timestamp() <= 8 * 3600 + 5


ADMIN_ROUTES = [
    ("get", "/admin/auth/me"),
    ("get", "/admin/overview"),
    ("get", "/admin/users"),
    ("get", "/admin/users/1"),
    ("post", "/admin/users/1/disable"),
    ("post", "/admin/users/1/enable"),
    ("post", "/admin/users/1/refresh-channel"),
    ("post", "/admin/users/1/clear-fetch-error"),
    ("delete", "/admin/users/1"),
    ("get", "/admin/audit-log"),
    ("get", "/admin/predictions"),
    ("get", "/admin/predictions/1"),
    ("delete", "/admin/predictions/1"),
    ("get", "/admin/cache"),
    ("post", "/admin/cache/warm-stale"),
    ("post", "/admin/cache/UCx/warm"),
    ("delete", "/admin/cache/UCx"),
]


@pytest.mark.parametrize("method, path", ADMIN_ROUTES)
def test_every_admin_route_rejects_anonymous_callers(api, method, path):
    assert api.client.request(method.upper(), path).status_code == 401


def test_every_admin_route_except_login_demands_an_admin(api, main_client):
    from security import get_current_admin

    def depends_on(dependant):
        return any(d.call is get_current_admin or depends_on(d) for d in dependant.dependencies)

    admin_routes = [r for r in api.main.app.routes if isinstance(r, APIRoute) and r.path.startswith("/admin")]
    assert admin_routes
    unguarded = [r.path for r in admin_routes if r.path != "/admin/auth/login" and not depends_on(r.dependant)]
    assert unguarded == []
    assert len(admin_routes) == len(ADMIN_ROUTES) + 1  # + login; keeps ADMIN_ROUTES complete


def test_admin_responses_never_contain_password_hashes(api):
    headers = admin_headers(api)
    uid, _ = make_user(api)
    bodies = [
        admin_login(api).text,
        api.client.get("/admin/auth/me", headers=headers).text,
        api.client.get("/admin/users", headers=headers).text,
        api.client.get(f"/admin/users/{uid}", headers=headers).text,
    ]
    assert not any("password" in body or "$2b$" in body for body in bodies)


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

def test_overview_counts_users_and_predictions(api):
    headers = admin_headers(api)
    ann, _ = make_user(api, "ann@example.com")
    bob, _ = make_user(api, "bob@example.com")
    api.cur.execute("UPDATE users SET is_active = FALSE, channel_fetch_error = 'x' WHERE id = %s", (bob,))
    api.conn.commit()
    insert_prediction(api, ann, "draft", status="draft")
    insert_prediction(api, ann, "done", views=1000)

    body = api.client.get("/admin/overview", headers=headers).json()
    assert body["users"] == {
        "total": 2, "active": 1, "disabled": 1, "new_7d": 2, "new_30d": 2, "with_channel": 2, "with_fetch_error": 1,
    }
    assert body["predictions"] == {"total": 2, "complete": 1, "draft": 1, "last_7d": 2}
    assert len(body["daily"]) == 30
    today = body["daily"][-1]
    assert (today["signups"], today["predictions"]) == (2, 2)
    assert sum(d["signups"] for d in body["daily"]) == 2


def test_overview_splits_forecasts_by_model_from_the_given_date(api):
    headers = admin_headers(api)
    uid, _ = make_user(api)
    api.cur.execute(
        "INSERT INTO predictions (user_id, title, status, used_channel_context, created_at) VALUES "
        "(%(u)s, 'old', 'complete', TRUE, NOW() - INTERVAL '10 days'),"
        "(%(u)s, 'ens', 'complete', TRUE, NOW()),"
        "(%(u)s, 'cb', 'complete', FALSE, NOW()),"
        "(%(u)s, 'draft', 'draft', NULL, NOW())",
        {"u": uid},
    )
    api.conn.commit()

    all_time = api.client.get("/admin/overview", headers=headers).json()["forecasts"]
    assert (all_time["ensemble"], all_time["catboost_only"]) == (2, 1)

    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    recent = api.client.get("/admin/overview", params={"ensemble_since": since}, headers=headers).json()
    assert (recent["forecasts"]["ensemble"], recent["forecasts"]["catboost_only"]) == (1, 1)
    assert sum(d["ensemble"] for d in recent["daily"]) == 1


def test_overview_reports_model_state(api):
    headers = admin_headers(api)

    class Broken:
        ready, error, device, load_time_seconds = False, "missing artifact", "cpu", None

    api.monkeypatch.setattr(api.backend.admin_router, "get_state", lambda: Broken())
    system = api.client.get("/admin/overview", headers=headers).json()["system"]
    assert system == {
        "database": "ok", "model_ready": False, "model_error": "missing artifact",
        "model_device": "cpu", "model_load_time_seconds": None,
    }


# ---------------------------------------------------------------------------
# User list and detail
# ---------------------------------------------------------------------------

def test_user_list_searches_filters_and_paginates(api):
    headers = admin_headers(api)
    ids = [make_user(api, f"user{i}@example.com")[0] for i in range(3)]
    api.cur.execute("UPDATE users SET full_name = 'Zed' WHERE id = %s", (ids[0],))
    api.cur.execute("UPDATE users SET is_active = FALSE WHERE id = %s", (ids[1],))
    api.conn.commit()
    insert_prediction(api, ids[2], "p", views=1)

    def list_(**params):
        return api.client.get("/admin/users", params=params, headers=headers).json()

    everyone = list_()
    assert everyone["total"] == 3
    assert [u["id"] for u in everyone["items"]] == list(reversed(ids))  # newest first
    assert everyone["items"][0]["prediction_count"] == 1
    assert everyone["items"][0]["channel_title"] == CHANNEL["title"]

    assert [u["id"] for u in list_(q="zed")["items"]] == [ids[0]]
    assert [u["id"] for u in list_(q="user2@")["items"]] == [ids[2]]
    assert [u["id"] for u in list_(status="disabled")["items"]] == [ids[1]]
    assert list_(status="active")["total"] == 2

    page = list_(limit=2, offset=2)
    assert page["total"] == 3 and [u["id"] for u in page["items"]] == [ids[0]]


def test_user_search_treats_like_wildcards_literally(api):
    headers = admin_headers(api)
    make_user(api, "ann@example.com")
    for q in ("%", "_", "\\"):
        assert api.client.get("/admin/users", params={"q": q}, headers=headers).json()["total"] == 0


def test_user_detail_shows_profile_channel_and_predictions(api):
    headers = admin_headers(api)
    uid, _ = make_user(api)
    insert_prediction(api, uid, "d", status="draft")
    insert_prediction(api, uid, "c", views=500)

    body = api.client.get(f"/admin/users/{uid}", headers=headers).json()
    assert body["email"] == "ann@example.com" and body["is_active"] is True
    assert body["channel"]["channel_id"] == CHANNEL["channel_id"]
    assert (body["prediction_count"], body["complete_count"], body["draft_count"]) == (2, 1, 1)
    assert body["notification_count"] >= 1  # welcome + channel fetch
    assert [p["title"] for p in body["recent_predictions"]] == ["c", "d"]


def test_unknown_user_is_404(api):
    headers = admin_headers(api)
    assert api.client.get("/admin/users/999", headers=headers).status_code == 404
    assert api.client.post("/admin/users/999/disable", headers=headers).status_code == 404


# ---------------------------------------------------------------------------
# Disable / enable
# ---------------------------------------------------------------------------

def test_disabling_a_user_locks_them_out_until_enabled(api):
    headers = admin_headers(api)
    uid, user_headers = make_user(api)

    disabled = api.client.post(f"/admin/users/{uid}/disable", headers=headers)
    assert disabled.status_code == 200 and disabled.json()["is_active"] is False

    me = api.client.get("/auth/me", headers=user_headers)
    assert me.status_code == 403 and "disabled" in me.json()["detail"]
    assert api.client.get("/predictions", headers=user_headers).status_code == 403
    login = api.client.post("/auth/login", data={"username": "ann@example.com", "password": PASSWORD})
    assert login.status_code == 403

    assert api.client.post(f"/admin/users/{uid}/enable", headers=headers).json()["is_active"] is True
    assert api.client.get("/auth/me", headers=user_headers).status_code == 200


def test_a_disabled_user_with_the_wrong_password_still_gets_401(api):
    """The disabled state is only revealed to someone who knows the password."""
    headers = admin_headers(api)
    uid, _ = make_user(api)
    api.client.post(f"/admin/users/{uid}/disable", headers=headers)
    login = api.client.post("/auth/login", data={"username": "ann@example.com", "password": "wrong-password"})
    assert login.status_code == 401


def test_repeated_disables_are_logged_once(api):
    headers = admin_headers(api)
    uid, _ = make_user(api)
    for _ in range(2):
        api.client.post(f"/admin/users/{uid}/disable", headers=headers)
    api.client.post(f"/admin/users/{uid}/enable", headers=headers)
    assert audit_actions(api, uid) == ["user.disable", "user.enable"]
    activity = api.client.get(f"/admin/users/{uid}", headers=headers).json()["recent_activity"]
    assert [a["action"] for a in activity] == ["user.enable", "user.disable"]
    assert activity[0]["admin_email"] == ADMIN_EMAIL


# ---------------------------------------------------------------------------
# Channel actions
# ---------------------------------------------------------------------------

def test_refresh_channel_updates_the_snapshot_and_is_audited(api):
    headers = admin_headers(api)
    uid, _ = make_user(api)
    api.monkeypatch.setattr(
        api.backend.channel_router, "resolve_channel", lambda url: {**CHANNEL, "title": "Renamed"},
    )
    body = api.client.post(f"/admin/users/{uid}/refresh-channel", headers=headers).json()
    assert body["channel"]["title"] == "Renamed"
    assert audit_actions(api, uid) == ["user.refresh_channel"]


def test_a_failed_refresh_keeps_the_snapshot_and_records_the_error(api):
    headers = admin_headers(api)
    uid, _ = make_user(api)
    fail_youtube(api, "quota exceeded")
    body = api.client.post(f"/admin/users/{uid}/refresh-channel", headers=headers).json()
    assert body["channel"]["title"] == CHANNEL["title"] and body["channel"]["fetch_error"] == "quota exceeded"
    details = rows(api, "SELECT details FROM admin_audit_log WHERE action = 'user.refresh_channel'")[0][0]
    assert details == {"ok": False, "error": "quota exceeded"}


def test_clear_fetch_error(api):
    headers = admin_headers(api)
    uid, _ = make_user(api)
    api.cur.execute("UPDATE users SET channel_fetch_error = 'boom' WHERE id = %s", (uid,))
    api.conn.commit()
    body = api.client.post(f"/admin/users/{uid}/clear-fetch-error", headers=headers).json()
    assert body["channel"]["fetch_error"] is None
    api.client.post(f"/admin/users/{uid}/clear-fetch-error", headers=headers)  # nothing to clear: not logged
    assert audit_actions(api, uid) == ["user.clear_fetch_error"]


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def test_delete_needs_the_matching_email(api):
    headers = admin_headers(api)
    uid, _ = make_user(api)
    wrong = api.client.request("DELETE", f"/admin/users/{uid}", json={"confirm_email": "bob@example.com"},
                               headers=headers)
    assert wrong.status_code == 400
    assert api.client.request("DELETE", f"/admin/users/{uid}", headers=headers).status_code == 422
    assert rows(api, "SELECT COUNT(*) FROM users")[0][0] == 1


def test_delete_removes_the_user_their_data_and_files_but_keeps_the_audit_entry(api):
    headers = admin_headers(api)
    uid, user_headers = make_user(api)
    served = post_prediction(api, user_headers, draft=True).json()["thumbnail_url"]
    assert (api.uploads / Path(served).name).exists()

    response = api.client.request("DELETE", f"/admin/users/{uid}", json={"confirm_email": "ann@example.com"},
                                  headers=headers)
    assert response.status_code == 204
    for table in ("users", "predictions", "notifications"):
        assert rows(api, f"SELECT COUNT(*) FROM {table}")[0][0] == 0
    assert not (api.uploads / Path(served).name).exists()
    assert api.client.get("/auth/me", headers=user_headers).status_code == 401
    details = rows(api, "SELECT details FROM admin_audit_log WHERE action = 'user.delete' AND target_id = %s", (uid,))
    assert details[0][0]["email"] == "ann@example.com"


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def test_audit_log_lists_newest_first_and_filters_by_action(api):
    headers = admin_headers(api)
    uid, _ = make_user(api)
    api.client.post(f"/admin/users/{uid}/disable", headers=headers)

    body = api.client.get("/admin/audit-log", headers=headers).json()
    assert body["total"] == 2
    assert [e["action"] for e in body["items"]] == ["user.disable", "admin.login"]
    assert body["items"][0]["target_id"] == uid and body["items"][0]["details"] == {"email": "ann@example.com"}

    only_logins = api.client.get("/admin/audit-log", params={"action": "admin.login"}, headers=headers).json()
    assert only_logins["total"] == 1


# ---------------------------------------------------------------------------
# tools/create_admin.py
# ---------------------------------------------------------------------------

def test_create_admin_tool_lowercases_email_and_hashes_the_password(api):
    make_admin(api, email="Mixed@Case.test")
    email, password_hash = rows(api, "SELECT email, password_hash FROM admins")[0]
    assert email == "mixed@case.test" and password_hash.startswith("$2b$")


def test_create_admin_tool_password_rules():
    assert create_admin_tool.validate_password("short") is not None
    assert create_admin_tool.validate_password("x" * 73) is not None
    assert create_admin_tool.validate_password("long-enough-password") is None


def test_create_admin_tool_reset_password(api):
    make_admin(api)
    assert create_admin_tool.reset_password(api.conn, ADMIN_EMAIL.upper(), "a-brand-new-password")
    assert admin_login(api).status_code == 401
    assert admin_login(api, password="a-brand-new-password").status_code == 200
    assert not create_admin_tool.reset_password(api.conn, "ghost@trendcast.test", "a-brand-new-password")
