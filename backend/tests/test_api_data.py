"""Layer C: backend data tests.

Drives the real FastAPI routers (auth, channel, notifications, predictions)
against a disposable database and checks what ends up in the tables. The
YouTube lookup and the forecast model are faked; everything else is real.
"""

import io

import bcrypt
import pytest
from PIL import Image

PASSWORD = "correct-horse-1"

CHANNEL = {
    "channel_id": "UCabc123",
    "title": "My Channel",
    "description": "About my channel",
    "country": "LK",
    "published_at": "2020-01-01T00:00:00Z",
    "thumbnail_url": "https://img.example/t.jpg",
    "banner_url": "https://img.example/b.jpg",
    "subscriber_count": 1234,
    "view_count": 99999,
    "video_count": 42,
    "subscriber_hidden": False,
    "fetched_at": "2026-01-01T00:00:00+00:00",
}

CSV_BYTES = b"a,b" + bytes([10]) + b"1,2" + bytes([10])

FORECAST = {
    "point_estimate_7d": 10_000.0,
    "range_7d": {"low": 8_000.0, "high": 14_000.0},
    "channel_baseline": 8_000.0,
    "curve": [{"day": d, "views": d * 1_000} for d in range(1, 8)],
    "shape_params": {"t0": 2.5},
    "used_channel_context": True,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def fake_youtube(api):
    """Default: every channel URL resolves to CHANNEL. Tests override as needed."""
    api.monkeypatch.setattr(api.backend.channel_router, "resolve_channel", lambda url: dict(CHANNEL))
    return api


def fail_youtube(api, message="couldn't find a YouTube channel"):
    error = api.backend.channel_router.YouTubeResolutionError

    def _raise(url):
        raise error(message)

    api.monkeypatch.setattr(api.backend.channel_router, "resolve_channel", _raise)


def signup(api, email="ann@example.com", password=PASSWORD, channel_url="https://youtube.com/@ann"):
    return api.client.post(
        "/auth/signup",
        json={"full_name": "Ann", "email": email, "password": password, "channel_url": channel_url},
    )


def make_user(api, email="ann@example.com"):
    """Signs up and returns (user_id, auth headers)."""
    response = signup(api, email=email)
    assert response.status_code == 200, response.text
    body = response.json()
    return body["user"]["id"], {"Authorization": f"Bearer {body['access_token']}"}


def rows(api, query, params=()):
    api.cur.execute(query, params)
    return api.cur.fetchall()


def png_bytes(color="red") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(buffer, format="PNG")
    return buffer.getvalue()


def post_prediction(api, headers, *, draft=False, thumbnail=True, **form):
    data = {"title": "My next video", "save_as_draft": "true" if draft else "false", **form}
    files = {"thumbnail": ("thumb.png", png_bytes(), "image/png")} if thumbnail else None
    return api.client.post("/predictions", data=data, files=files, headers=headers)


def fake_forecast(api, result=None, raises=None):
    calls = []

    def _run(state, **kwargs):
        calls.append(kwargs)
        if raises is not None:
            raise raises
        return dict(result or FORECAST)

    api.monkeypatch.setattr(api.backend.predictions_router, "run_forecast_on_image", _run)
    return calls


# ---------------------------------------------------------------------------
# Signup and stored credentials
# ---------------------------------------------------------------------------

def test_signup_stores_bcrypt_hash_never_the_password(api):
    assert signup(api).status_code == 200
    (stored,) = rows(api, "SELECT password_hash FROM users")[0]
    assert stored != PASSWORD and PASSWORD not in stored
    assert stored.startswith("$2")
    assert bcrypt.checkpw(PASSWORD.encode(), stored.encode())


def test_signup_response_never_exposes_the_password_hash(api):
    body = signup(api).json()
    assert "password_hash" not in body["user"] and "password" not in body["user"]
    assert body["token_type"] == "bearer" and body["access_token"]


def test_signup_writes_welcome_and_channel_notifications(api):
    user_id, _ = make_user(api)
    types = sorted(t for (t,) in rows(api, "SELECT type FROM notifications WHERE user_id = %s", (user_id,)))
    assert types == ["channel_fetch_success", "welcome"]


def test_signup_stores_channel_snapshot_with_documented_keys(api):
    make_user(api)
    (data,) = rows(api, "SELECT channel_data FROM users")[0]
    documented = {
        "title", "description", "thumbnail_url", "banner_url", "country", "published_at",
        "subscriber_count", "view_count", "video_count", "subscriber_hidden", "channel_id", "fetched_at",
    }
    assert documented <= set(data)
    assert data["channel_id"] == "UCabc123"
    assert rows(api, "SELECT channel_fetch_error FROM users") == [(None,)]


def test_signup_copies_channel_subscriber_count_to_the_user(api):
    make_user(api)
    assert rows(api, "SELECT subscribers FROM users") == [(1234,)]


def test_signup_with_hidden_subscriber_count_keeps_subscribers_at_zero(api):
    api.monkeypatch.setattr(
        api.backend.channel_router, "resolve_channel",
        lambda url: {**CHANNEL, "subscriber_count": None, "subscriber_hidden": True},
    )
    make_user(api)
    assert rows(api, "SELECT subscribers FROM users") == [(0,)]


def test_signup_survives_a_failed_channel_lookup_and_records_the_error(api):
    fail_youtube(api, "no such channel")
    user_id, _ = make_user(api)
    assert rows(api, "SELECT channel_data, channel_fetch_error, subscribers FROM users") == [
        (None, "no such channel", 0)
    ]
    types = sorted(t for (t,) in rows(api, "SELECT type FROM notifications WHERE user_id = %s", (user_id,)))
    assert types == ["channel_fetch_error", "welcome"]


def test_duplicate_email_is_a_clean_400_and_creates_nothing(api):
    make_user(api)
    response = signup(api)
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]
    assert rows(api, "SELECT COUNT(*) FROM users") == [(1,)]
    assert rows(api, "SELECT COUNT(*) FROM notifications WHERE type = 'welcome'") == [(1,)]


@pytest.mark.parametrize(
    "override",
    [{"email": "not-an-email"}, {"password": "short"}, {"full_name": None}, {"channel_url": None}],
    ids=["bad-email", "short-password", "missing-name", "missing-channel-url"],
)
def test_invalid_signup_is_rejected_and_writes_nothing(api, override):
    payload = {"full_name": "Ann", "email": "ann@example.com", "password": PASSWORD,
               "channel_url": "https://youtube.com/@ann", **override}
    payload = {k: v for k, v in payload.items() if v is not None}
    assert api.client.post("/auth/signup", json=payload).status_code == 422
    assert rows(api, "SELECT COUNT(*) FROM users") == [(0,)]


# ---------------------------------------------------------------------------
# Login, token and profile
# ---------------------------------------------------------------------------

def test_login_succeeds_with_correct_credentials(api):
    make_user(api)
    response = api.client.post("/auth/login", data={"username": "ann@example.com", "password": PASSWORD})
    assert response.status_code == 200 and response.json()["access_token"]


@pytest.mark.parametrize("email, password", [("ann@example.com", "wrong-password"), ("nobody@example.com", PASSWORD)])
def test_login_failures_are_indistinguishable(api, email, password):
    make_user(api)
    response = api.client.post("/auth/login", data={"username": email, "password": password})
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect email or password"


@pytest.mark.parametrize("token", [None, "garbage", "a.b.c"], ids=["missing", "garbage", "malformed-jwt"])
def test_protected_endpoint_rejects_missing_or_bad_tokens(api, token):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    assert api.client.get("/auth/me", headers=headers).status_code == 401


def test_token_of_a_deleted_user_is_rejected(api):
    user_id, headers = make_user(api)
    api.cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
    api.conn.commit()
    assert api.client.get("/auth/me", headers=headers).status_code == 401


def test_profile_update_changes_only_the_provided_fields(api):
    _, headers = make_user(api)
    response = api.client.patch("/auth/me", json={"monthly_views": 5000}, headers=headers)
    assert response.status_code == 200
    assert rows(api, "SELECT full_name, subscribers, monthly_views FROM users") == [("Ann", 1234, 5000)]


@pytest.mark.parametrize("payload", [{"subscribers": -1}, {"monthly_views": -5}])
def test_profile_update_rejects_negative_baselines_and_leaves_row_unchanged(api, payload):
    _, headers = make_user(api)
    assert api.client.patch("/auth/me", json=payload, headers=headers).status_code == 422
    assert rows(api, "SELECT subscribers, monthly_views FROM users") == [(1234, 0)]


def test_change_password_with_wrong_current_password_changes_nothing(api):
    _, headers = make_user(api)
    (before,) = rows(api, "SELECT password_hash FROM users")[0]
    response = api.client.post(
        "/auth/change-password",
        json={"current_password": "wrong-password", "new_password": "brand-new-pass"},
        headers=headers,
    )
    assert response.status_code == 400
    assert rows(api, "SELECT password_hash FROM users") == [(before,)]


def test_change_password_replaces_the_hash_and_old_password_stops_working(api):
    _, headers = make_user(api)
    (before,) = rows(api, "SELECT password_hash FROM users")[0]
    response = api.client.post(
        "/auth/change-password",
        json={"current_password": PASSWORD, "new_password": "brand-new-pass"},
        headers=headers,
    )
    assert response.status_code == 200
    (after,) = rows(api, "SELECT password_hash FROM users")[0]
    assert after != before and bcrypt.checkpw(b"brand-new-pass", after.encode())
    assert api.client.post("/auth/login", data={"username": "ann@example.com", "password": PASSWORD}).status_code == 401


# ---------------------------------------------------------------------------
# Channel refresh
# ---------------------------------------------------------------------------

def test_refresh_success_updates_snapshot_and_clears_a_previous_error(api):
    fail_youtube(api, "temporary outage")
    _, headers = make_user(api)
    assert rows(api, "SELECT channel_fetch_error FROM users") == [("temporary outage",)]

    api.monkeypatch.setattr(
        api.backend.channel_router, "resolve_channel", lambda url: {**CHANNEL, "subscriber_count": 5000}
    )
    response = api.client.post("/channel/refresh", headers=headers)
    assert response.status_code == 200 and response.json()["subscriber_count"] == 5000
    (data, error) = rows(api, "SELECT channel_data, channel_fetch_error FROM users")[0]
    assert data["subscriber_count"] == 5000 and error is None


def test_failed_refresh_keeps_the_last_good_snapshot(api):
    _, headers = make_user(api)
    fail_youtube(api, "temporary outage")
    api.client.post("/channel/refresh", headers=headers)
    (data,) = rows(api, "SELECT channel_data FROM users")[0]
    assert data is not None and data["channel_id"] == "UCabc123"
    assert rows(api, "SELECT channel_fetch_error FROM users") == [("temporary outage",)]


def test_failed_refresh_still_returns_the_stored_snapshot_with_the_error(api):
    _, headers = make_user(api)
    fail_youtube(api, "temporary outage")
    body = api.client.post("/channel/refresh", headers=headers).json()
    assert body["title"] == "My Channel" and body["channel_id"] == "UCabc123"
    assert body["fetch_error"] == "temporary outage"


def test_failed_refresh_writes_an_error_notification(api):
    user_id, headers = make_user(api)
    fail_youtube(api, "temporary outage")
    api.client.post("/channel/refresh", headers=headers)
    assert rows(api, "SELECT COUNT(*) FROM notifications WHERE user_id = %s AND type = 'channel_fetch_error'",
                (user_id,)) == [(1,)]


# ---------------------------------------------------------------------------
# Notifications: ownership and read state
# ---------------------------------------------------------------------------

def test_users_only_see_their_own_notifications(api):
    _, ann = make_user(api, "ann@example.com")
    _, bob = make_user(api, "bob@example.com")
    for headers in (ann, bob):
        body = api.client.get("/notifications", headers=headers).json()
        assert body["unread_count"] == 2 and len(body["notifications"]) == 2
    assert rows(api, "SELECT COUNT(*) FROM notifications") == [(4,)]


def test_mark_read_only_affects_the_owners_notification(api):
    ann_id, ann = make_user(api, "ann@example.com")
    bob_id, bob = make_user(api, "bob@example.com")
    (bobs_notification,) = rows(api, "SELECT id FROM notifications WHERE user_id = %s LIMIT 1", (bob_id,))[0]

    assert api.client.post(f"/notifications/{bobs_notification}/read", headers=ann).status_code == 404
    assert rows(api, "SELECT read FROM notifications WHERE id = %s", (bobs_notification,)) == [(False,)]

    assert api.client.post(f"/notifications/{bobs_notification}/read", headers=bob).status_code == 200
    assert rows(api, "SELECT read FROM notifications WHERE id = %s", (bobs_notification,)) == [(True,)]


def test_mark_all_read_only_affects_the_caller(api):
    ann_id, ann = make_user(api, "ann@example.com")
    bob_id, _ = make_user(api, "bob@example.com")
    assert api.client.post("/notifications/read-all", headers=ann).status_code == 200
    assert rows(api, "SELECT COUNT(*) FROM notifications WHERE user_id = %s AND NOT read", (ann_id,)) == [(0,)]
    assert rows(api, "SELECT COUNT(*) FROM notifications WHERE user_id = %s AND NOT read", (bob_id,)) == [(2,)]
    assert api.client.get("/notifications", headers=ann).json()["unread_count"] == 0


def test_marking_an_unknown_notification_is_404(api):
    _, headers = make_user(api)
    assert api.client.post("/notifications/999999/read", headers=headers).status_code == 404


# ---------------------------------------------------------------------------
# Predictions: drafts
# ---------------------------------------------------------------------------

def test_draft_never_calls_the_model_and_has_empty_prediction_fields(api):
    _, headers = make_user(api)
    calls = fake_forecast(api)
    response = post_prediction(api, headers, draft=True, thumbnail=False, tags="a, b,, c")
    assert response.status_code == 200, response.text
    assert calls == []
    assert rows(
        api,
        "SELECT status, predicted_views, confidence, change_vs_avg, trajectory, v_inf, tau, "
        "used_channel_context, tags FROM predictions",
    ) == [("draft", None, None, None, None, None, None, None, ["a", "b", "c"])]


def test_draft_writes_no_prediction_notification(api):
    _, headers = make_user(api)
    fake_forecast(api)
    post_prediction(api, headers, draft=True, thumbnail=False)
    assert rows(api, "SELECT COUNT(*) FROM notifications WHERE type = 'prediction_complete'") == [(0,)]


# ---------------------------------------------------------------------------
# Predictions: complete runs
# ---------------------------------------------------------------------------

def test_complete_prediction_stores_the_model_outputs(api):
    user_id, headers = make_user(api)
    calls = fake_forecast(api)
    response = post_prediction(api, headers, category="Gaming", target_date="2026-02-03", target_time="18:30")
    assert response.status_code == 200, response.text

    (row,) = rows(
        api,
        "SELECT user_id, status, category, target_date::text, target_time, predicted_views, "
        "change_vs_avg, trajectory, v_inf, tau, used_channel_context, thumbnail_path FROM predictions",
    )
    assert row[:7] == (user_id, "complete", "Gaming", "2026-02-03", "18:30", 10_000, 0.25)
    assert row[7] == FORECAST["curve"]
    assert row[8:11] == (10_000.0, 2.5, True)
    assert row[11].startswith("/uploads/") and row[11].endswith(".png")
    assert calls[0]["channel_id"] == "UCabc123"


def test_complete_prediction_confidence_is_within_bounds(api):
    _, headers = make_user(api)
    fake_forecast(api)
    post_prediction(api, headers)
    (confidence,) = rows(api, "SELECT confidence FROM predictions")[0]
    assert 0.05 <= confidence <= 0.95


def test_complete_prediction_writes_a_prediction_complete_notification(api):
    user_id, headers = make_user(api)
    fake_forecast(api)
    post_prediction(api, headers)
    assert rows(api, "SELECT COUNT(*) FROM notifications WHERE user_id = %s AND type = 'prediction_complete'",
                (user_id,)) == [(1,)]


def test_uploaded_thumbnail_is_saved_under_a_random_name(api):
    _, headers = make_user(api)
    fake_forecast(api)
    post_prediction(api, headers)
    (path,) = rows(api, "SELECT thumbnail_path FROM predictions")[0]
    saved = api.uploads / path.rsplit("/", 1)[1]
    assert saved.read_bytes() == png_bytes()
    assert "thumb" not in saved.name


@pytest.mark.parametrize(
    "error_name, status",
    [("InsufficientHistoryError", 422), ("ChannelNotFoundError", 404),
     ("QuotaExceededError", 503), ("RuntimeError", 503)],
)
def test_model_failures_map_to_http_errors_and_persist_nothing(api, error_name, status):
    _, headers = make_user(api)
    error_type = RuntimeError if error_name == "RuntimeError" else getattr(api.backend.inference, error_name)
    fake_forecast(api, raises=error_type("boom"))
    assert post_prediction(api, headers).status_code == status
    assert rows(api, "SELECT COUNT(*) FROM predictions") == [(0,)]
    assert rows(api, "SELECT COUNT(*) FROM notifications WHERE type = 'prediction_complete'") == [(0,)]


REJECTED_RUNS = [
    pytest.param(dict(thumbnail=False), id="no-thumbnail"),
    pytest.param(dict(files={"thumbnail": ("t.gif", b"GIF89a", "image/gif")}), id="unsupported-type"),
    pytest.param(dict(files={"thumbnail": ("t.png", b"not an image", "image/png")}), id="not-an-image"),
]


@pytest.mark.parametrize("case", REJECTED_RUNS)
def test_invalid_complete_run_is_rejected_and_persists_nothing(api, case):
    _, headers = make_user(api)
    fake_forecast(api)
    data = {"title": "T", "save_as_draft": "false"}
    files = case.get("files")
    response = api.client.post("/predictions", data=data, files=files, headers=headers)
    assert response.status_code == 400
    assert rows(api, "SELECT COUNT(*) FROM predictions") == [(0,)]


def test_complete_run_without_a_linked_channel_is_rejected(api):
    fail_youtube(api)
    _, headers = make_user(api)
    fake_forecast(api)
    response = post_prediction(api, headers)
    assert response.status_code == 400 and "not linked" in response.json()["detail"]
    assert rows(api, "SELECT COUNT(*) FROM predictions") == [(0,)]


def test_rejected_run_leaves_no_orphaned_upload(api):
    fail_youtube(api)
    _, headers = make_user(api)
    fake_forecast(api)
    assert post_prediction(api, headers).status_code == 400
    assert list(api.uploads.iterdir()) == []


def test_invalid_image_leaves_no_orphaned_upload(api):
    _, headers = make_user(api)
    fake_forecast(api)
    response = api.client.post(
        "/predictions", data={"title": "T", "save_as_draft": "false"},
        files={"thumbnail": ("t.png", b"not an image", "image/png")}, headers=headers,
    )
    assert response.status_code == 400
    assert list(api.uploads.iterdir()) == []


@pytest.mark.parametrize("error", ["InsufficientHistoryError", "QuotaExceededError"])
def test_model_failure_leaves_no_orphaned_upload(api, error):
    _, headers = make_user(api)
    fake_forecast(api, raises=getattr(api.backend.inference, error)("boom"))
    assert post_prediction(api, headers).status_code in (422, 503)
    assert list(api.uploads.iterdir()) == []


def test_database_failure_removes_the_saved_uploads(api):
    _, headers = make_user(api)
    fake_forecast(api)
    files = {
        "thumbnail": ("thumb.png", png_bytes(), "image/png"),
        "dataset": ("data.csv", CSV_BYTES, "text/csv"),
    }
    # a title over VARCHAR(255) passes validation and the model, then fails on INSERT
    response = api.client.post(
        "/predictions", data={"title": "x" * 300, "save_as_draft": "false"}, files=files, headers=headers,
    )
    assert response.status_code == 500
    assert rows(api, "SELECT COUNT(*) FROM predictions") == [(0,)]
    assert list(api.uploads.iterdir()) == []


def test_successful_run_keeps_both_uploads(api):
    _, headers = make_user(api)
    fake_forecast(api)
    files = {
        "thumbnail": ("thumb.png", png_bytes(), "image/png"),
        "dataset": ("data.csv", CSV_BYTES, "text/csv"),
    }
    response = api.client.post("/predictions", data={"title": "T", "save_as_draft": "false"},
                               files=files, headers=headers)
    assert response.status_code == 200
    assert sorted(p.suffix for p in api.uploads.iterdir()) == [".csv", ".png"]
    (thumb, data) = rows(api, "SELECT thumbnail_path, dataset_path FROM predictions")[0]
    assert (api.uploads / thumb.rsplit("/", 1)[1]).exists() and (api.uploads / data.rsplit("/", 1)[1]).exists()


@pytest.mark.parametrize(
    "form",
    [
        {"target_date": "not-a-date"},
        {"target_date": "2026-13-45"},
        {"target_date": "2026-02-03", "target_time": "25:99"},
        {"target_date": "2026-02-03", "target_time": "abc"},
        {"target_time": "18:30:00.123456"},
    ],
    ids=["text-date", "impossible-date", "impossible-time", "text-time", "time-too-long"],
)
@pytest.mark.parametrize("draft", [True, False], ids=["draft", "complete"])
def test_malformed_schedule_is_a_422_and_persists_nothing(api, form, draft):
    _, headers = make_user(api)
    calls = fake_forecast(api)
    response = post_prediction(api, headers, draft=draft, thumbnail=not draft, **form)
    assert response.status_code == 422
    assert calls == []
    assert rows(api, "SELECT COUNT(*) FROM predictions") == [(0,)]
    assert list(api.uploads.iterdir()) == []


def test_valid_schedule_is_stored_as_given(api):
    _, headers = make_user(api)
    fake_forecast(api)
    post_prediction(api, headers, draft=True, thumbnail=False, target_date="2026-02-03", target_time="18:30")
    assert rows(api, "SELECT target_date::text, target_time FROM predictions") == [("2026-02-03", "18:30")]


def test_schedule_is_optional(api):
    _, headers = make_user(api)
    fake_forecast(api)
    assert post_prediction(api, headers, draft=True, thumbnail=False).status_code == 200
    assert rows(api, "SELECT target_date, target_time FROM predictions") == [(None, None)]


# ---------------------------------------------------------------------------
# Predictions: ownership
# ---------------------------------------------------------------------------

def test_users_only_see_and_delete_their_own_predictions(api):
    _, ann = make_user(api, "ann@example.com")
    _, bob = make_user(api, "bob@example.com")
    prediction_id = post_prediction(api, ann, draft=True, thumbnail=False).json()["id"]

    assert api.client.get("/predictions", headers=bob).json() == []
    assert api.client.get(f"/predictions/{prediction_id}", headers=bob).status_code == 404
    assert api.client.delete(f"/predictions/{prediction_id}", headers=bob).status_code == 404
    assert rows(api, "SELECT COUNT(*) FROM predictions") == [(1,)]

    assert [p["id"] for p in api.client.get("/predictions", headers=ann).json()] == [prediction_id]
    assert api.client.delete(f"/predictions/{prediction_id}", headers=ann).status_code == 204
    assert rows(api, "SELECT COUNT(*) FROM predictions") == [(0,)]


def test_protected_prediction_routes_require_a_token(api):
    assert api.client.get("/predictions").status_code == 401
    assert api.client.post("/predictions", data={"title": "T"}).status_code == 401


# ---------------------------------------------------------------------------
# Confidence heuristic
# ---------------------------------------------------------------------------

@pytest.fixture
def band(api):
    return api.backend.predictions_router._confidence_from_band


@pytest.mark.parametrize("point, low, high", [(0, 0, 10), (-5, 1, 2), (100, 50, 50), (100, 80, 60)])
def test_confidence_falls_back_to_0_3_for_degenerate_input(band, point, low, high):
    assert band(point, low, high) == 0.3


def test_confidence_is_higher_for_a_narrower_band(band):
    assert band(1000, 900, 1100) > band(1000, 500, 2500)


@pytest.mark.parametrize("width", [0.001, 1, 2, 4, 1_000_000])
def test_confidence_stays_within_bounds(band, width):
    assert 0.05 <= band(1000, 1000, 1000 + width * 1000) <= 0.95
