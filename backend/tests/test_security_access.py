"""Security and access control tests (black box, through the HTTP API).

Application level (S-A): who may call what, and whose data they may touch.
System level     (S-B): login, tokens, secrets and the database role boundary.
Input attacks    (S-C): injection, hostile uploads, information leakage.
Browser level    (S-D): CORS and response headers.

The real routers and database are used; the YouTube lookup and the forecast model
are faked (see conftest.py). Everything runs against the disposable Postgres.

Tests marked xfail(strict=True) record a KNOWN GAP in the application, not a test
defect: the assertion states the secure behaviour, and today it does not hold. When
the gap is fixed the test flips to XPASS, strict mode fails the run, and the marker
must be removed. The gaps are listed in SECURITY_TEST_REPORT.md.
"""

import base64
import json
import os
import subprocess
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import jwt
import pytest
from fastapi.routing import APIRoute
from PIL import Image

from test_api_data import (
    PASSWORD,
    fake_forecast,
    fake_youtube,  # noqa: F401  (autouse fixture: every channel URL resolves to CHANNEL)
    make_user,
    png_bytes,
    post_prediction,
    rows,
    signup,
)
from test_api_function import (  # noqa: F401  (main_client is a fixture)
    PROTECTED_ROUTES,
    ReadyState,
    bearer,
    insert_prediction,
    main_client,
    seed_channel_and_video,
    token_for,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent


def two_users(api):
    ann_id, ann = make_user(api, "ann@example.com")
    bob_id, bob = make_user(api, "bob@example.com")
    return ann_id, ann, bob_id, bob


def prediction_id_of(api, user_id):
    return rows(api, "SELECT id FROM predictions WHERE user_id=%s ORDER BY id LIMIT 1", (user_id,))[0][0]


# ===========================================================================
# S-A  Application-level security: access control
# ===========================================================================

# Routes that are open on purpose. Anything else in main.app must demand a login.
PUBLIC_BY_DESIGN = {("GET", "/health"), ("POST", "/auth/signup"), ("POST", "/auth/login")}

# Open today, with no login and no owner. Listed so a NEW open route cannot appear
# unnoticed; whether these should stay open is the A7 decision in the test plan.
PUBLIC_UNDECIDED = {
    ("GET", "/channels"),
    ("GET", "/videos/{video_id}/timeseries"),
    ("GET", "/channels/{channel_id}/videos"),
    ("GET", "/forecast/health"),
    ("POST", "/forecast"),
}


def _depends_on(dependant, target) -> bool:
    return any(d.call is target or _depends_on(d, target) for d in dependant.dependencies)


def test_route_inventory_every_route_is_protected_or_explicitly_public(api, main_client):
    """S-A1: a route added without get_current_user fails here until it is classified."""
    from security import get_current_user

    unclassified = []
    for route in api.main.app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            key = (method, route.path)
            if key in PUBLIC_BY_DESIGN or key in PUBLIC_UNDECIDED:
                continue
            if not _depends_on(route.dependant, get_current_user):
                unclassified.append(key)
    assert unclassified == []


def test_the_protected_route_list_used_by_the_tests_matches_the_app(api, main_client):
    """Guards PROTECTED_ROUTES itself: every listed route exists (with ids swapped for a param)."""
    paths = {r.path for r in api.main.app.routes if isinstance(r, APIRoute)}
    for _, path in PROTECTED_ROUTES:
        normalised = path.replace("/1", "/{id}")
        assert any(p.replace("{prediction_id}", "{id}").replace("{notification_id}", "{id}") == normalised
                   for p in paths | {path}), path


@pytest.mark.parametrize("header", ["Bearer ", "Bearer", "Basic dXNlcjpwYXNz", "bearer not-a-token", "Token abc"])
def test_malformed_authorization_headers_are_rejected(api, header):
    """S-A1"""
    make_user(api)
    assert api.client.get("/auth/me", headers={"Authorization": header}).status_code == 401


def test_public_data_routes_are_readable_without_a_login_today(api, main_client):
    """S-A7: documents current behaviour (public read-only pipeline data). Revisit if it should be private."""
    seed_channel_and_video(api)
    api.monkeypatch.setattr(api.main, "get_state", lambda: ReadyState())
    for path in ("/channels", "/channels/UCbig/videos", "/videos/vid1/timeseries", "/forecast/health"):
        assert main_client.get(path).status_code == 200, path


def test_another_user_cannot_read_a_prediction(api):
    """S-A2 (horizontal access control)"""
    ann_id, _, _, bob = two_users(api)
    insert_prediction(api, ann_id, title="ann secret")
    pid = prediction_id_of(api, ann_id)
    response = api.client.get(f"/predictions/{pid}", headers=bob)
    assert response.status_code == 404 and "ann secret" not in response.text


def test_another_user_cannot_delete_a_prediction(api):
    """S-A2"""
    ann_id, _, _, bob = two_users(api)
    insert_prediction(api, ann_id)
    pid = prediction_id_of(api, ann_id)
    assert api.client.delete(f"/predictions/{pid}", headers=bob).status_code == 404
    assert rows(api, "SELECT COUNT(*) FROM predictions WHERE id=%s", (pid,)) == [(1,)]


def test_prediction_lists_never_include_another_users_rows(api):
    """S-A2"""
    ann_id, ann, bob_id, bob = two_users(api)
    insert_prediction(api, ann_id, title="ann one")
    insert_prediction(api, bob_id, title="bob one")
    assert [p["title"] for p in api.client.get("/predictions", headers=bob).json()] == ["bob one"]
    assert [p["title"] for p in api.client.get("/predictions", headers=ann).json()] == ["ann one"]


def test_another_user_cannot_mark_a_notification_read(api):
    """S-A2"""
    ann_id, _, _, bob = two_users(api)
    nid = rows(api, "SELECT id FROM notifications WHERE user_id=%s", (ann_id,))[0][0]
    assert api.client.post(f"/notifications/{nid}/read", headers=bob).status_code == 404
    assert rows(api, "SELECT read FROM notifications WHERE id=%s", (nid,)) == [(False,)]


def test_read_all_only_touches_the_callers_notifications(api):
    """S-A2"""
    ann_id, _, _, bob = two_users(api)
    unread_before = rows(api, "SELECT COUNT(*) FROM notifications WHERE user_id=%s AND NOT read", (ann_id,))
    assert unread_before[0][0] > 0
    assert api.client.post("/notifications/read-all", headers=bob).status_code == 200
    assert rows(api, "SELECT COUNT(*) FROM notifications WHERE user_id=%s AND NOT read", (ann_id,)) == unread_before


def test_not_yours_and_does_not_exist_look_identical(api):
    """S-A3: ids must not reveal which records exist."""
    ann_id, _, _, bob = two_users(api)
    insert_prediction(api, ann_id)
    pid = prediction_id_of(api, ann_id)
    for method, template in (("get", "/predictions/{}"), ("delete", "/predictions/{}"),
                             ("post", "/notifications/{}/read")):
        foreign_id = pid if "predictions" in template else rows(
            api, "SELECT id FROM notifications WHERE user_id=%s", (ann_id,))[0][0]
        foreign = getattr(api.client, method)(template.format(foreign_id), headers=bob)
        missing = getattr(api.client, method)(template.format(10**9), headers=bob)
        assert (foreign.status_code, foreign.text) == (missing.status_code, missing.text), template


def test_profile_update_cannot_mass_assign_privileged_columns(api):
    """S-A4"""
    uid, headers = make_user(api)
    before = rows(api, "SELECT id, email, password_hash, channel_data, channel_url FROM users WHERE id=%s", (uid,))
    api.client.patch(
        "/auth/me",
        json={"id": 999, "email": "x@y.z", "password_hash": "x", "channel_data": {"channel_id": "UCevil"},
              "channel_url": "https://evil", "created_at": "2000-01-01"},
        headers=headers,
    )
    after = rows(api, "SELECT id, email, password_hash, channel_data, channel_url FROM users WHERE id=%s", (uid,))
    assert before == after


def test_profile_update_rejects_negative_numbers(api):
    """S-A4"""
    _, headers = make_user(api)
    for field in ("subscribers", "monthly_views"):
        assert api.client.patch("/auth/me", json={field: -1}, headers=headers).status_code == 422


@pytest.mark.xfail(strict=True, reason="FINDING S-A4b: subscribers/monthly_views have no upper bound, so a BIGINT overflow is a 500")
def test_profile_update_with_an_out_of_range_number_is_not_a_server_error(api):
    """S-A4: the column is BIGINT; an oversized value must be a 422, not a database error."""
    _, headers = make_user(api)
    response = api.client.patch("/auth/me", json={"subscribers": 10**30}, headers=headers)
    assert response.status_code < 500


def test_change_password_needs_the_current_password_and_leaves_the_hash_alone_on_failure(api):
    """S-A4"""
    uid, headers = make_user(api)
    before = rows(api, "SELECT password_hash FROM users WHERE id=%s", (uid,))
    response = api.client.post(
        "/auth/change-password", json={"current_password": "wrong-password", "new_password": "another-pass-1"},
        headers=headers,
    )
    assert response.status_code == 400
    assert rows(api, "SELECT password_hash FROM users WHERE id=%s", (uid,)) == before


def test_a_token_for_a_deleted_user_is_rejected(api):
    """S-A5"""
    uid, headers = make_user(api)
    api.cur.execute("DELETE FROM users WHERE id=%s", (uid,))
    api.conn.commit()
    assert api.client.get("/auth/me", headers=headers).status_code == 401


def test_responses_never_contain_the_password_hash(api):
    """S-A8: sensitive column exposure."""
    signup_response = signup(api)
    login_response = api.client.post("/auth/login", data={"username": "ann@example.com", "password": PASSWORD})
    headers = {"Authorization": f"Bearer {signup_response.json()['access_token']}"}
    bodies = [signup_response.text, login_response.text]
    for path in ("/auth/me", "/channel/me", "/dashboard/summary", "/notifications", "/predictions"):
        bodies.append(api.client.get(path, headers=headers).text)
    for body in bodies:
        assert "password" not in body.lower() and "$2b$" not in body


@pytest.mark.xfail(strict=True, reason="FINDING S-A6: deleting a prediction leaves its thumbnail/dataset publicly served from /uploads")
def test_deleting_a_prediction_leaves_no_stray_files(api):
    """S-A6: uploads are public by URL, so a deleted prediction's files must be deleted too."""
    _, headers = make_user(api)
    created = post_prediction(api, headers, draft=True).json()
    served = created["thumbnail_url"]
    assert served and (api.uploads / Path(served).name).exists()
    assert api.client.delete(f"/predictions/{created['id']}", headers=headers).status_code == 204
    assert not (api.uploads / Path(served).name).exists()


def test_another_users_delete_keeps_the_owners_files(api):
    """S-A6"""
    _, ann, _, bob = two_users(api)
    created = post_prediction(api, ann, draft=True).json()
    api.client.delete(f"/predictions/{created['id']}", headers=bob)
    assert (api.uploads / Path(created["thumbnail_url"]).name).exists()


# ===========================================================================
# S-B  System-level security: login, tokens, secrets, database boundary
# ===========================================================================

def test_login_does_not_reveal_whether_the_email_exists(api):
    """S-B1"""
    make_user(api)
    wrong_password = api.client.post("/auth/login", data={"username": "ann@example.com", "password": "nope-nope-1"})
    unknown_email = api.client.post("/auth/login", data={"username": "ghost@example.com", "password": "nope-nope-1"})
    assert (wrong_password.status_code, wrong_password.json()) == (unknown_email.status_code, unknown_email.json())


@pytest.mark.xfail(strict=True, reason="FINDING S-B2: no rate limiting or lockout on /auth/login")
def test_repeated_failed_logins_are_throttled(api):
    make_user(api)
    statuses = [
        api.client.post("/auth/login", data={"username": "ann@example.com", "password": f"guess-{i}-xx"}).status_code
        for i in range(15)
    ]
    assert 429 in statuses or statuses[-1] != 401


@pytest.mark.xfail(strict=True, reason="FINDING S-B3: signup reveals that an email is already registered")
def test_signup_does_not_reveal_registered_emails(api):
    signup(api)
    assert "already exists" not in signup(api).text


@pytest.mark.xfail(strict=True, reason="FINDING S-B3b: email is case-sensitive, so one person can hold many accounts")
def test_emails_differing_only_in_case_are_the_same_account(api):
    assert signup(api, "Ann@Example.com").status_code == 200
    assert signup(api, "ann@example.com").status_code == 400


@pytest.mark.xfail(strict=True, reason="FINDING S-B4a: a password over 72 bytes makes bcrypt raise, giving a 500 on signup")
def test_a_password_longer_than_bcrypts_72_byte_limit_is_not_a_server_error(api):
    """S-B4: bcrypt 5 raises on >72 bytes; the API must turn that into a clean 4xx or accept it."""
    long_password = "p" * 100
    response = signup(api, password=long_password)
    assert response.status_code in (200, 400, 422), response.text
    login = api.client.post("/auth/login", data={"username": "ann@example.com", "password": long_password})
    assert login.status_code < 500


@pytest.mark.xfail(strict=True, reason="FINDING S-B4b: passwords have no maximum length (1 MB reaches bcrypt and returns 500)")
def test_a_huge_password_is_rejected_before_the_expensive_hash(api):
    """S-B4: unbounded password length is a cheap denial-of-service lever."""
    response = signup(api, password="p" * 1_000_000)
    assert response.status_code in (400, 413, 422)


def test_a_token_with_a_tampered_subject_is_rejected(api):
    """S-B5: swap the payload to another user's id but keep the original signature."""
    ann_id, ann, bob_id, _ = two_users(api)
    header, payload, signature = ann["Authorization"].split()[1].split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["sub"] = str(bob_id)
    forged_payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    forged = f"{header}.{forged_payload}.{signature}"
    assert api.client.get("/auth/me", headers=bearer(forged)).status_code == 401


def test_a_token_signed_with_a_different_algorithm_is_rejected(api):
    """S-B5: algorithm confusion (HS512 token accepted by an HS256-only verifier)."""
    uid, _ = make_user(api)
    token = jwt.encode({"sub": str(uid), "exp": 9999999999}, "test-secret", algorithm="HS512")
    assert api.client.get("/auth/me", headers=bearer(token)).status_code == 401


@pytest.mark.xfail(strict=True, reason="FINDING S-B5b: tokens without an 'exp' claim are accepted (never expire)")
def test_a_token_with_no_expiry_is_rejected(api):
    uid, _ = make_user(api)
    token = jwt.encode({"sub": str(uid)}, "test-secret", algorithm="HS256")
    assert api.client.get("/auth/me", headers=bearer(token)).status_code == 401


@pytest.mark.xfail(strict=True, reason="FINDING S-B5c: a token issued before a password change stays valid for 7 days")
def test_old_tokens_stop_working_after_a_password_change(api):
    _, headers = make_user(api)
    changed = api.client.post(
        "/auth/change-password", json={"current_password": PASSWORD, "new_password": "brand-new-pass-1"},
        headers=headers,
    )
    assert changed.status_code == 200
    assert api.client.get("/auth/me", headers=headers).status_code == 401


def test_issued_tokens_expire_within_a_bounded_time(api):
    """S-B5: lifetime is finite and not longer than the documented 7 days."""
    uid, headers = make_user(api)
    claims = jwt.decode(headers["Authorization"].split()[1], "test-secret", algorithms=["HS256"])
    remaining = claims["exp"] - claims["iat"] if "iat" in claims else None
    from security import JWT_EXPIRES_MINUTES

    assert JWT_EXPIRES_MINUTES <= 60 * 24 * 7
    assert remaining is None or remaining <= 60 * 60 * 24 * 7


@pytest.mark.parametrize("missing", ["JWT_SECRET_KEY", "SUPABASE_DB_URL", "YOUTUBE_API_KEY"])
def test_the_backend_refuses_to_start_without_its_secrets(missing):
    """S-B6: an empty secret must be a startup error, never an app that signs tokens with ''."""
    env = {**os.environ, "SUPABASE_DB_URL": "postgresql://x", "JWT_SECRET_KEY": "s", "YOUTUBE_API_KEY": "k"}
    env[missing] = ""
    result = subprocess.run(
        [sys.executable, "-c", "import config"], cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode != 0 and missing in result.stderr


@pytest.fixture
def low_privilege_role(conn):
    """A login-less role with every table privilege: it is stopped by row-level security alone,
    the same position as Supabase's anon/authenticated roles."""
    role = f"tc_lowpriv_{uuid.uuid4().hex[:8]}"
    with conn.cursor() as cur:
        cur.execute(f'CREATE ROLE "{role}" NOLOGIN')
        cur.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        cur.execute(f'GRANT ALL ON ALL TABLES IN SCHEMA public TO "{role}"')
        cur.execute(f'GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO "{role}"')
    conn.commit()
    yield role
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("RESET ROLE")
        cur.execute(f'DROP OWNED BY "{role}"')
        cur.execute(f'DROP ROLE "{role}"')
    conn.commit()


def test_a_non_owner_role_sees_no_rows_in_any_table(conn, low_privilege_role):
    """S-B7: RLS with no policies hides every row, including users.password_hash."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (full_name, email, password_hash, channel_url) "
            "VALUES ('a', 'a@x.com', '$2b$12$hash', 'u')"
        )
        cur.execute("INSERT INTO channel_stats (channel_id, channel_title, total_views, subscriber_count, video_count) "
                    "VALUES ('UC1', 't', 1, 1, 1)")
        cur.execute("SELECT relname FROM pg_class WHERE relnamespace='public'::regnamespace AND relkind='r' ORDER BY 1")
        tables = [r[0] for r in cur.fetchall()]
        conn.commit()
        cur.execute(f'SET ROLE "{low_privilege_role}"')
        leaked = {}
        for table in tables:
            cur.execute(f'SELECT COUNT(*) FROM "{table}"')
            count = cur.fetchone()[0]
            if count:
                leaked[table] = count
    assert leaked == {}


def test_a_non_owner_role_cannot_write_or_change_data(conn, low_privilege_role):
    """S-B7"""
    import psycopg2

    with conn.cursor() as cur:
        cur.execute("INSERT INTO users (full_name, email, password_hash, channel_url) VALUES ('a','a@x.com','h','u')")
        conn.commit()
        cur.execute(f'SET ROLE "{low_privilege_role}"')
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute("INSERT INTO users (full_name, email, password_hash, channel_url) VALUES ('b','b@x.com','h','u')")
        conn.rollback()
        cur.execute(f'SET ROLE "{low_privilege_role}"')
        cur.execute("UPDATE users SET full_name='hacked'")
        assert cur.rowcount == 0
        cur.execute("DELETE FROM users")
        assert cur.rowcount == 0
        conn.rollback()
        cur.execute("RESET ROLE")
        cur.execute("SELECT full_name FROM users")
        assert cur.fetchall() == [("a",)]


def test_env_files_holding_secrets_are_not_tracked_by_git():
    """S-B8"""
    listed = subprocess.run(["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True)
    if listed.returncode != 0:
        pytest.skip("not a git checkout")
    tracked = [f for f in listed.stdout.splitlines() if Path(f).name.startswith(".env") and f.endswith(".env")]
    assert tracked == []


def test_the_env_example_holds_no_real_looking_secret():
    """S-B8: placeholders only (no long random tokens, no live connection strings)."""
    import re

    for example in REPO_ROOT.rglob(".env.example"):
        if "node_modules" in example.parts:
            continue
        text = example.read_text(encoding="utf-8")
        assert not re.search(r"AIza[0-9A-Za-z_\-]{30,}", text), f"YouTube key pattern in {example}"
        assert not re.search(r"postgres(ql)?://[^:\s]+:(?!password|your|<|\[|changeme)[^@\s]{6,}@", text), example


# ===========================================================================
# S-C  Input attacks
# ===========================================================================

SQLI_PAYLOADS = [
    "'; DROP TABLE users; --",
    "' OR '1'='1",
    "x'); DELETE FROM predictions; --",
    '"; SELECT pg_sleep(5); --',
    "\\'; TRUNCATE notifications; --",
]


@pytest.mark.parametrize("payload", SQLI_PAYLOADS)
def test_sql_injection_in_prediction_text_fields_is_stored_as_plain_text(api, payload):
    """S-C1"""
    uid, headers = make_user(api)
    notifications_before = rows(api, "SELECT COUNT(*) FROM notifications WHERE user_id=%s", (uid,))
    response = post_prediction(api, headers, draft=True, title=payload, category=payload, tags=f"{payload},b")
    assert response.status_code == 200 and response.json()["title"] == payload
    assert rows(api, "SELECT COUNT(*) FROM users") == [(1,)]
    assert rows(api, "SELECT COUNT(*) FROM notifications WHERE user_id=%s", (uid,)) == notifications_before


@pytest.mark.parametrize("payload", SQLI_PAYLOADS)
def test_sql_injection_in_login_and_profile_does_not_bypass_or_damage(api, payload):
    """S-C1"""
    make_user(api)
    assert api.client.post("/auth/login", data={"username": payload, "password": payload}).status_code == 401
    bypass = api.client.post("/auth/login", data={"username": "ann@example.com' OR '1'='1", "password": payload})
    assert bypass.status_code == 401
    _, headers = make_user(api, "bob@example.com")
    api.client.patch("/auth/me", json={"full_name": payload}, headers=headers)
    assert rows(api, "SELECT COUNT(*) FROM users") == [(2,)]
    assert rows(api, "SELECT full_name FROM users WHERE email='bob@example.com'") == [(payload,)]


@pytest.mark.parametrize("payload", SQLI_PAYLOADS)
def test_sql_injection_in_signup_email_is_rejected_by_validation(api, payload):
    """S-C1"""
    response = api.client.post(
        "/auth/signup", json={"full_name": "x", "email": payload, "password": PASSWORD, "channel_url": "u"}
    )
    assert response.status_code == 422


@pytest.mark.parametrize("payload", SQLI_PAYLOADS)
def test_sql_injection_in_public_path_parameters_is_harmless(api, main_client, payload):
    """S-C1"""
    make_user(api)
    for path in (f"/channels/{payload}/videos", f"/videos/{payload}/timeseries"):
        response = main_client.get(path)
        assert response.status_code in (200, 404) and (response.json() == [] or response.status_code == 404)
    assert rows(api, "SELECT COUNT(*) FROM users") == [(1,)]


def test_non_numeric_ids_in_paths_are_422_not_500(api):
    """S-C1"""
    _, headers = make_user(api)
    for path in ("/predictions/1;DROP TABLE users", "/predictions/1 OR 1=1", "/predictions/abc"):
        assert api.client.get(path, headers=headers).status_code in (404, 422)


def test_html_and_script_text_is_returned_as_json_data_not_markup(api):
    """S-C6: stored XSS payloads must come back inside application/json, never as HTML."""
    _, headers = make_user(api)
    payload = "<script>alert(1)</script><img src=x onerror=alert(1)>"
    created = post_prediction(api, headers, draft=True, title=payload, tags=payload)
    response = api.client.get(f"/predictions/{created.json()['id']}", headers=headers)
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["title"] == payload


@pytest.mark.xfail(strict=True, reason="FINDING S-C2: a draft's 'thumbnail' is never decoded, so any bytes are stored")
def test_a_draft_thumbnail_must_really_be_an_image(api):
    _, headers = make_user(api)
    response = api.client.post(
        "/predictions",
        data={"title": "t", "save_as_draft": "true"},
        files={"thumbnail": ("a.png", b"<html><script>alert(1)</script></html>", "image/png")},
        headers=headers,
    )
    assert response.status_code == 400


@pytest.mark.xfail(strict=True, reason="FINDING S-C2b: uploads keep the client's extension and are served from the API origin")
@pytest.mark.parametrize("filename", ["evil.html", "evil.svg", "evil.js", "evil.htm"])
def test_uploaded_files_never_keep_an_executable_web_extension(api, filename):
    _, headers = make_user(api)
    response = api.client.post(
        "/predictions",
        data={"title": "t", "save_as_draft": "true"},
        files={"dataset": (filename, b"<script>alert(1)</script>", "text/csv")},
        headers=headers,
    )
    stored = response.json()["thumbnail_url"] or ""
    dataset_path = rows(api, "SELECT dataset_path FROM predictions ORDER BY id DESC LIMIT 1")[0][0]
    assert Path(dataset_path).suffix.lower() not in {".html", ".htm", ".svg", ".js"}, stored


def test_a_dataset_filename_cannot_escape_the_uploads_directory(api):
    """S-C4"""
    _, headers = make_user(api)
    for name in ("../../evil.csv", "..\\..\\evil.csv", "a/b/c.csv", "x\x00.csv", "a" * 5000 + ".csv"):
        response = api.client.post(
            "/predictions",
            data={"title": "t", "save_as_draft": "true"},
            files={"dataset": (name, b"a,b\n1,2\n", "text/csv")},
            headers=headers,
        )
        assert response.status_code < 500, name
    for path in api.uploads.rglob("*"):
        assert api.uploads in path.parents or path == api.uploads
    assert not (api.uploads.parent / "evil.csv").exists()
    assert not (api.uploads.parent.parent / "evil.csv").exists()


def test_a_decompression_bomb_thumbnail_is_rejected_not_decoded(api):
    """S-C3: ~225 megapixels in a few KB. Pillow refuses it; the API must answer 400, not crash."""
    _, headers = make_user(api)
    fake_forecast(api)
    import io

    buffer = io.BytesIO()
    Image.new("1", (15000, 15000)).save(buffer, format="PNG", optimize=True)
    assert len(buffer.getvalue()) < 10 * 1024 * 1024
    response = api.client.post(
        "/predictions",
        data={"title": "t", "save_as_draft": "false"},
        files={"thumbnail": ("bomb.png", buffer.getvalue(), "image/png")},
        headers=headers,
    )
    assert response.status_code == 400


def test_thumbnail_content_type_is_allow_listed(api):
    """S-C2"""
    _, headers = make_user(api)
    for content_type in ("text/html", "image/svg+xml", "application/octet-stream", "image/gif"):
        response = api.client.post(
            "/predictions",
            data={"title": "t", "save_as_draft": "true"},
            files={"thumbnail": ("t.png", png_bytes(), content_type)},
            headers=headers,
        )
        assert response.status_code == 400, content_type


@pytest.mark.xfail(strict=True, reason="FINDING S-C7: 400 responses echo the raw Pillow exception text")
def test_invalid_image_errors_do_not_leak_internal_details(api):
    _, headers = make_user(api)
    fake_forecast(api)
    response = api.client.post(
        "/predictions",
        data={"title": "t", "save_as_draft": "false"},
        files={"thumbnail": ("t.png", b"not an image", "image/png")},
        headers=headers,
    )
    assert response.status_code == 400
    assert "BytesIO" not in response.text and "0x" not in response.text


def test_unhandled_errors_do_not_expose_stack_traces_or_sql(api):
    """S-C7"""
    _, headers = make_user(api)
    response = api.client.patch("/auth/me", json={"subscribers": 10**30}, headers=headers)
    body = response.text.lower()
    for marker in ("traceback", "psycopg2", "select ", "insert ", "update users", "secret", "postgres"):
        assert marker not in body, marker


@pytest.mark.xfail(strict=True, reason="FINDING S-C5a: /forecast needs no login, so anyone can spend model and YouTube quota")
def test_forecast_requires_authentication(api, main_client):
    api.monkeypatch.setattr(api.main, "get_state", lambda: ReadyState())
    api.monkeypatch.setattr(api.main, "run_forecast", lambda state, **kw: {"curve": []})
    assert main_client.post("/forecast", json={"title": "t"}).status_code == 401


@pytest.mark.xfail(strict=True, reason="FINDING S-C5b: /forecast fetches any thumbnail_url server-side (SSRF)")
@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost:5432/",
        "http://127.0.0.1:8000/health",
        "http://[::1]/",
        "http://10.0.0.1/x.jpg",
        "file:///etc/passwd",
        "gopher://127.0.0.1:6379/_INFO",
    ],
)
def test_forecast_refuses_internal_and_non_http_thumbnail_urls(api, main_client, url):
    api.monkeypatch.setattr(api.main, "get_state", lambda: ReadyState())
    reached = []
    api.monkeypatch.setattr(api.main, "run_forecast", lambda state, **kw: reached.append(kw) or {"curve": []})
    response = main_client.post("/forecast", json={"title": "t", "thumbnail_url": url})
    assert response.status_code in (400, 401, 422) and reached == []


# ===========================================================================
# S-D  Browser-facing: CORS and headers
# ===========================================================================

PREFLIGHT = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"}


@pytest.mark.parametrize(
    "origin",
    ["null", "http://localhost:5173.evil.com", "http://evil.com/http://localhost:5173",
     "https://localhost:5173", "http://localhost:9999", "http://LOCALHOST:5173"],
)
def test_cors_rejects_lookalike_and_null_origins(api, main_client, origin):
    """S-D1"""
    response = main_client.options("/auth/login", headers={"Origin": origin, **PREFLIGHT})
    assert "access-control-allow-origin" not in response.headers


def test_cors_never_answers_with_a_wildcard_while_allowing_credentials(api, main_client):
    """S-D1"""
    response = main_client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert response.headers.get("access-control-allow-origin") != "*"


@pytest.mark.xfail(strict=True, reason="FINDING S-D2: no X-Content-Type-Options / other security headers are set")
def test_responses_carry_basic_security_headers(api, main_client):
    response = main_client.get("/health")
    assert response.headers.get("x-content-type-options") == "nosniff"
