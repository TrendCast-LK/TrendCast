"""Function tests: use cases driven through the HTTP API (black box).

Covers the gaps left by test_api_data.py: dashboard, trends, the public
data/forecast endpoints in main.py, static uploads, the authorization matrix,
JWT edge cases and the remaining prediction validation rules. The YouTube
lookup and the forecast model are faked; the routers and database are real.
"""

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest

from test_api_data import (
    CHANNEL,
    FORECAST,
    PASSWORD,
    fake_forecast,
    fake_youtube,  # noqa: F401  (autouse fixture: every channel URL resolves to CHANNEL)
    make_user,
    png_bytes,
    post_prediction,
    rows,
    signup,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]


def insert_prediction(api, user_id, title="t", category=None, status="complete", views=None, confidence=None):
    api.cur.execute(
        "INSERT INTO predictions (user_id, title, category, status, predicted_views, confidence) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (user_id, title, category, status, views, confidence),
    )
    api.conn.commit()


def token_for(user_id, secret="test-secret", expires=timedelta(hours=1)):
    return jwt.encode({"sub": str(user_id), "exp": datetime.now(timezone.utc) + expires}, secret, algorithm="HS256")


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# F13 Dashboard
# ---------------------------------------------------------------------------

def test_dashboard_summary_returns_the_users_own_baseline(api):
    _, headers = make_user(api)
    api.client.patch("/auth/me", json={"monthly_views": 5000}, headers=headers)
    body = api.client.get("/dashboard/summary", headers=headers).json()
    assert body == {"full_name": "Ann", "subscribers": CHANNEL["subscriber_count"], "monthly_views": 5000}


def test_dashboard_summary_is_per_user(api):
    make_user(api, "ann@example.com")
    _, bob = make_user(api, "bob@example.com")
    api.client.patch("/auth/me", json={"full_name": "Bob", "subscribers": 7}, headers=bob)
    body = api.client.get("/dashboard/summary", headers=bob).json()
    assert body["full_name"] == "Bob" and body["subscribers"] == 7


# ---------------------------------------------------------------------------
# F14 Trends
# ---------------------------------------------------------------------------

def test_trends_for_a_user_with_no_predictions_is_all_zero_not_an_error(api):
    _, headers = make_user(api)
    response = api.client.get("/trends/summary", headers=headers)
    assert response.status_code == 200
    assert response.json() == {
        "total_predictions": 0,
        "draft_predictions": 0,
        "completed_predictions": 0,
        "average_predicted_views": 0.0,
        "average_confidence": None,
        "best_category": None,
        "timeline": [],
        "category_breakdown": [],
    }


def test_trends_counts_drafts_and_completed_separately(api):
    uid, headers = make_user(api)
    insert_prediction(api, uid, "d1", status="draft")
    insert_prediction(api, uid, "d2", status="draft")
    insert_prediction(api, uid, "c1", category="Gaming", views=1000, confidence=0.5)
    body = api.client.get("/trends/summary", headers=headers).json()
    assert (body["total_predictions"], body["draft_predictions"], body["completed_predictions"]) == (3, 2, 1)


def test_trends_averages_only_include_completed_predictions(api):
    uid, headers = make_user(api)
    insert_prediction(api, uid, "d", status="draft", views=999_999, confidence=0.99)
    insert_prediction(api, uid, "a", views=1000, confidence=0.4)
    insert_prediction(api, uid, "b", views=3000, confidence=0.6)
    body = api.client.get("/trends/summary", headers=headers).json()
    assert body["average_predicted_views"] == 2000
    assert body["average_confidence"] == pytest.approx(0.5)
    assert [p["title"] for p in body["timeline"]] == ["a", "b"]  # drafts excluded, oldest first


def test_trends_picks_the_category_with_the_highest_average_views(api):
    uid, headers = make_user(api)
    insert_prediction(api, uid, "g1", category="Gaming", views=1000)
    insert_prediction(api, uid, "g2", category="Gaming", views=2000)
    insert_prediction(api, uid, "m1", category="Music", views=9000)
    insert_prediction(api, uid, "n1", category=None, views=50_000)  # uncategorised never counts
    body = api.client.get("/trends/summary", headers=headers).json()
    assert body["best_category"] == "Music"
    assert body["category_breakdown"] == [
        {"category": "Music", "count": 1, "average_views": 9000},
        {"category": "Gaming", "count": 2, "average_views": 1500},
    ]


def test_trends_ignores_a_completed_prediction_with_no_confidence(api):
    uid, headers = make_user(api)
    insert_prediction(api, uid, "a", views=100, confidence=None)
    insert_prediction(api, uid, "b", views=100, confidence=0.8)
    assert api.client.get("/trends/summary", headers=headers).json()["average_confidence"] == pytest.approx(0.8)


def test_trends_only_counts_the_callers_predictions(api):
    ann, ann_headers = make_user(api, "ann@example.com")
    bob, _ = make_user(api, "bob@example.com")
    insert_prediction(api, bob, "bobs", category="Gaming", views=1)
    body = api.client.get("/trends/summary", headers=ann_headers).json()
    assert body["total_predictions"] == 0 and body["timeline"] == []


def test_trends_reflect_a_prediction_created_through_the_api(api):
    _, headers = make_user(api)
    fake_forecast(api)
    assert post_prediction(api, headers, category="Gaming").status_code == 200
    body = api.client.get("/trends/summary", headers=headers).json()
    assert body["completed_predictions"] == 1 and body["best_category"] == "Gaming"
    assert body["timeline"] == [{"title": "My next video", "predicted_views": 10_000}]


# ---------------------------------------------------------------------------
# Authorization matrix: anonymous / bad token on every protected route
# ---------------------------------------------------------------------------

PROTECTED_ROUTES = [
    ("get", "/auth/me"),
    ("patch", "/auth/me"),
    ("post", "/auth/change-password"),
    ("get", "/channel/me"),
    ("post", "/channel/refresh"),
    ("get", "/notifications"),
    ("post", "/notifications/1/read"),
    ("post", "/notifications/read-all"),
    ("get", "/dashboard/summary"),
    ("get", "/trends/summary"),
    ("get", "/predictions"),
    ("post", "/predictions"),
    ("get", "/predictions/1"),
    ("delete", "/predictions/1"),
]


@pytest.mark.parametrize("method, path", PROTECTED_ROUTES)
def test_every_protected_route_rejects_anonymous_callers(api, method, path):
    assert getattr(api.client, method)(path).status_code == 401


@pytest.mark.parametrize("method, path", PROTECTED_ROUTES)
def test_every_protected_route_rejects_a_forged_token(api, method, path):
    uid, _ = make_user(api)
    forged = bearer(token_for(uid, secret="not-the-real-secret"))
    assert getattr(api.client, method)(path, headers=forged).status_code == 401


def test_expired_token_is_rejected(api):
    uid, _ = make_user(api)
    expired = bearer(token_for(uid, expires=timedelta(seconds=-5)))
    assert api.client.get("/auth/me", headers=expired).status_code == 401


def test_token_without_a_subject_or_with_a_non_numeric_one_is_rejected(api):
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    no_sub = jwt.encode({"exp": exp}, "test-secret", algorithm="HS256")
    bad_sub = jwt.encode({"sub": "abc", "exp": exp}, "test-secret", algorithm="HS256")
    for token in (no_sub, bad_sub):
        assert api.client.get("/auth/me", headers=bearer(token)).status_code == 401


def test_token_using_the_none_algorithm_is_rejected(api):
    uid, _ = make_user(api)
    unsigned = jwt.encode({"sub": str(uid)}, None, algorithm="none")
    assert api.client.get("/auth/me", headers=bearer(unsigned)).status_code == 401


def test_a_valid_token_gets_the_users_profile(api):
    uid, headers = make_user(api)
    body = api.client.get("/auth/me", headers=headers).json()
    assert body["id"] == uid and body["email"] == "ann@example.com"


# ---------------------------------------------------------------------------
# F1/F2/F5 Auth and profile gaps
# ---------------------------------------------------------------------------

def test_login_returns_the_same_user_as_signup(api):
    uid, _ = make_user(api)
    response = api.client.post("/auth/login", data={"username": "ann@example.com", "password": PASSWORD})
    assert response.status_code == 200 and response.json()["user"]["id"] == uid


def test_signup_then_login_token_works_on_protected_routes(api):
    make_user(api)
    token = api.client.post("/auth/login", data={"username": "ann@example.com", "password": PASSWORD}).json()[
        "access_token"
    ]
    assert api.client.get("/dashboard/summary", headers=bearer(token)).status_code == 200


def test_signup_email_is_case_sensitive_today(api):
    """Documents current behaviour: A@x.com and a@x.com are different accounts.

    Flagged as a finding in the report; change the expectation if the app
    normalises emails later.
    """
    assert signup(api, "Ann@Example.com").status_code == 200
    assert signup(api, "ann@example.com").status_code == 200


def test_profile_update_ignores_fields_it_does_not_own(api):
    uid, headers = make_user(api)
    before = rows(api, "SELECT email, password_hash FROM users WHERE id=%s", (uid,))[0]
    response = api.client.patch(
        "/auth/me",
        json={"email": "evil@example.com", "id": 999, "password_hash": "x", "full_name": "New Name"},
        headers=headers,
    )
    assert response.status_code == 200 and response.json()["full_name"] == "New Name"
    after = rows(api, "SELECT email, password_hash FROM users WHERE id=%s", (uid,))[0]
    assert before == after


def test_empty_profile_update_is_a_no_op(api):
    _, headers = make_user(api)
    response = api.client.patch("/auth/me", json={}, headers=headers)
    assert response.status_code == 200 and response.json()["full_name"] == "Ann"


def test_change_password_rejects_a_short_new_password(api):
    _, headers = make_user(api)
    response = api.client.post(
        "/auth/change-password", json={"current_password": PASSWORD, "new_password": "short"}, headers=headers
    )
    assert response.status_code == 422


def test_channel_me_reports_the_stored_snapshot(api):
    _, headers = make_user(api)
    body = api.client.get("/channel/me", headers=headers).json()
    assert body["channel_id"] == CHANNEL["channel_id"] and body["title"] == CHANNEL["title"]
    assert body["channel_url"] == "https://youtube.com/@ann" and body["fetch_error"] is None


# ---------------------------------------------------------------------------
# F8 Notifications gaps
# ---------------------------------------------------------------------------

def test_unread_count_drops_as_notifications_are_read(api):
    _, headers = make_user(api)
    first = api.client.get("/notifications", headers=headers).json()
    total = len(first["notifications"])
    assert total >= 2 and first["unread_count"] == total  # welcome + channel_fetch_success

    api.client.post(f"/notifications/{first['notifications'][0]['id']}/read", headers=headers)
    assert api.client.get("/notifications", headers=headers).json()["unread_count"] == total - 1

    api.client.post("/notifications/read-all", headers=headers)
    assert api.client.get("/notifications", headers=headers).json()["unread_count"] == 0


def test_notifications_are_listed_newest_first(api):
    _, headers = make_user(api)
    stamps = [n["created_at"] for n in api.client.get("/notifications", headers=headers).json()["notifications"]]
    assert stamps == sorted(stamps, reverse=True)


# ---------------------------------------------------------------------------
# F9-F12 Prediction gaps
# ---------------------------------------------------------------------------

def test_dataset_over_the_limit_is_413_and_persists_nothing(api):
    _, headers = make_user(api)
    api.backend.predictions_router.MAX_DATASET_BYTES = 100
    try:
        response = api.client.post(
            "/predictions",
            data={"title": "t", "save_as_draft": "true"},
            files={"dataset": ("d.csv", b"x" * 101, "text/csv")},
            headers=headers,
        )
    finally:
        api.backend.predictions_router.MAX_DATASET_BYTES = 50 * 1024 * 1024
    assert response.status_code == 413
    assert rows(api, "SELECT count(*) FROM predictions")[0][0] == 0 and list(api.uploads.iterdir()) == []


def test_thumbnail_exactly_at_the_limit_is_accepted_and_one_byte_over_is_413(api):
    _, headers = make_user(api)
    limit = len(png_bytes())
    api.backend.predictions_router.MAX_THUMBNAIL_BYTES = limit
    try:
        ok = api.client.post(
            "/predictions",
            data={"title": "t", "save_as_draft": "true"},
            files={"thumbnail": ("a.png", png_bytes(), "image/png")},
            headers=headers,
        )
        over = api.client.post(
            "/predictions",
            data={"title": "t", "save_as_draft": "true"},
            files={"thumbnail": ("a.png", png_bytes() + b"\0", "image/png")},
            headers=headers,
        )
    finally:
        api.backend.predictions_router.MAX_THUMBNAIL_BYTES = 10 * 1024 * 1024
    assert ok.status_code == 200 and over.status_code == 413


def test_tags_are_split_trimmed_and_blank_entries_dropped(api):
    _, headers = make_user(api)
    body = post_prediction(api, headers, draft=True, tags=" a, ,b ,, c").json()
    assert body["tags"] == ["a", "b", "c"]


def test_model_receives_the_optional_form_fields(api):
    _, headers = make_user(api)
    calls = fake_forecast(api)
    response = post_prediction(
        api, headers, tags="x,y", duration="PT8M32S", description="about", category_id="20", category="Gaming"
    )
    assert response.status_code == 200
    (kwargs,) = calls
    assert kwargs["tags"] == ["x", "y"] and kwargs["duration"] == "PT8M32S"
    assert kwargs["description"] == "about" and kwargs["category_id"] == 20
    assert kwargs["channel_id"] == CHANNEL["channel_id"] and kwargs["title"] == "My next video"


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on", " true "])
def test_draft_flag_accepts_common_truthy_spellings(api, value):
    _, headers = make_user(api)
    response = api.client.post("/predictions", data={"title": "t", "save_as_draft": value}, headers=headers)
    assert response.status_code == 200 and response.json()["status"] == "draft"


def test_a_draft_needs_no_thumbnail_but_a_complete_run_does(api):
    _, headers = make_user(api)
    fake_forecast(api)
    assert post_prediction(api, headers, draft=True, thumbnail=False).status_code == 200
    assert post_prediction(api, headers, thumbnail=False).status_code == 400


def test_prediction_without_a_title_is_422(api):
    _, headers = make_user(api)
    assert api.client.post("/predictions", data={"save_as_draft": "true"}, headers=headers).status_code == 422


def test_predictions_list_is_newest_first(api):
    _, headers = make_user(api)
    for title in ("first", "second", "third"):
        post_prediction(api, headers, draft=True, title=title)
    titles = [p["title"] for p in api.client.get("/predictions", headers=headers).json()]
    assert titles == ["third", "second", "first"]


def test_deleting_a_prediction_then_fetching_it_is_404(api):
    _, headers = make_user(api)
    pid = post_prediction(api, headers, draft=True).json()["id"]
    assert api.client.delete(f"/predictions/{pid}", headers=headers).status_code == 204
    assert api.client.get(f"/predictions/{pid}", headers=headers).status_code == 404
    assert api.client.delete(f"/predictions/{pid}", headers=headers).status_code == 404


def test_deleting_a_user_cascades_to_their_predictions_and_notifications(api):
    uid, headers = make_user(api)
    post_prediction(api, headers, draft=True)
    api.cur.execute("DELETE FROM users WHERE id=%s", (uid,))
    api.conn.commit()
    assert rows(api, "SELECT count(*) FROM predictions")[0][0] == 0
    assert rows(api, "SELECT count(*) FROM notifications")[0][0] == 0


def test_prediction_id_that_is_not_a_number_is_422(api):
    _, headers = make_user(api)
    assert api.client.get("/predictions/abc", headers=headers).status_code == 422


# ---------------------------------------------------------------------------
# F15-F18 main.py: public data, forecast, uploads, CORS
# ---------------------------------------------------------------------------

@pytest.fixture
def main_client(api):
    """TestClient for the real main.app, wired to the per-test database."""
    import main
    from fastapi.testclient import TestClient

    api.main = main
    return TestClient(main.app, raise_server_exceptions=False)


def seed_channel_and_video(api):
    api.cur.execute(
        "INSERT INTO channel_stats (channel_id, channel_title, total_views, subscriber_count, video_count, published_at) "
        "VALUES ('UCzero', 'Zero', 0, 0, 0, NOW()), ('UCbig', 'Big', 1000, 100, 10, NOW())"
    )
    api.cur.execute("INSERT INTO videos (video_id, channel_id, published_at) VALUES ('vid1', 'UCbig', NOW())")
    api.cur.execute(
        "INSERT INTO view_timeseries (video_id, scraped_at, view_count, like_count, comment_count) VALUES "
        "('vid1', NOW(), 200, 2, 1), ('vid1', NOW() - INTERVAL '1 hour', 100, 1, 0)"
    )
    api.conn.commit()


def test_health_reports_a_connected_database(api, main_client):
    assert main_client.get("/health").json() == {"status": "ok", "db": "connected"}


def test_channels_endpoint_returns_enriched_kpis_and_survives_zero_denominators(api, main_client):
    seed_channel_and_video(api)
    response = main_client.get("/channels")
    assert response.status_code == 200
    by_id = {c["channel_id"]: c for c in response.json()}
    assert by_id["UCbig"]["avg_views_per_video"] == 100 and by_id["UCbig"]["views_per_subscriber"] == 10
    assert by_id["UCbig"]["size_tier"].startswith("Micro")
    zero = by_id["UCzero"]
    assert zero["avg_views_per_video"] == 0 and zero["views_per_subscriber"] == 0 and zero["engagement_ratio"] == 0


def test_channel_videos_lists_only_that_channels_videos(api, main_client):
    seed_channel_and_video(api)
    body = main_client.get("/channels/UCbig/videos").json()
    assert [v["video_id"] for v in body] == ["vid1"] and body[0]["status"] == "active"


@pytest.mark.parametrize("path", ["/channels/UCnobody/videos", "/videos/nope/timeseries"])
def test_unknown_ids_give_an_empty_list_not_an_error(api, main_client, path):
    response = main_client.get(path)
    assert response.status_code == 200 and response.json() == []


def test_video_timeseries_is_oldest_first(api, main_client):
    seed_channel_and_video(api)
    body = main_client.get("/videos/vid1/timeseries").json()
    assert [p["view_count"] for p in body] == [100, 200]


class ReadyState:
    ready, error, device, load_time_seconds = True, None, "cpu", 1.5


class BrokenState:
    ready, error, device, load_time_seconds = False, "missing artifact: x", "cpu", None


def test_forecast_health_reports_model_state(api, main_client):
    api.monkeypatch.setattr(api.main, "get_state", lambda: BrokenState())
    assert main_client.get("/forecast/health").json() == {
        "ready": False, "error": "missing artifact: x", "device": "cpu", "load_time_seconds": None,
    }


def test_forecast_is_503_when_the_model_is_not_loaded(api, main_client):
    api.monkeypatch.setattr(api.main, "get_state", lambda: BrokenState())
    response = main_client.post("/forecast", json={"title": "t"})
    assert response.status_code == 503 and "missing artifact" in response.json()["detail"]


def test_forecast_returns_the_model_result(api, main_client):
    api.monkeypatch.setattr(api.main, "get_state", lambda: ReadyState())
    captured = {}

    def run(state, **kwargs):
        captured.update(kwargs)
        return {"curve": FORECAST["curve"], "point_estimate_7d": 10_000.0}

    api.monkeypatch.setattr(api.main, "run_forecast", run)
    response = main_client.post("/forecast", json={"title": "t", "channel_id": "UCx"})
    assert response.status_code == 200 and response.json()["point_estimate_7d"] == 10_000.0
    assert captured["title"] == "t" and captured["channel_id"] == "UCx"


def test_forecast_maps_a_thumbnail_download_failure_to_400(api, main_client):
    api.monkeypatch.setattr(api.main, "get_state", lambda: ReadyState())

    def run(state, **kwargs):
        raise api.main.ThumbnailDownloadError("thumbnail URL could not be fetched")

    api.monkeypatch.setattr(api.main, "run_forecast", run)
    response = main_client.post("/forecast", json={"title": "t", "thumbnail_url": "http://x/y.jpg"})
    assert response.status_code == 400


def test_forecast_requires_a_title(api, main_client):
    assert main_client.post("/forecast", json={}).status_code == 422


def test_uploaded_files_are_served_and_traversal_is_blocked(api, main_client):
    name = "function-test-upload.png"
    target = Path(api.main.UPLOADS_DIR) / name
    target.write_bytes(png_bytes())
    try:
        assert main_client.get(f"/uploads/{name}").status_code == 200
        assert main_client.get("/uploads/does-not-exist.png").status_code == 404
        for attack in ("/uploads/../config.py", "/uploads/%2e%2e/config.py", "/uploads/..%2fconfig.py"):
            response = main_client.get(attack)
            assert response.status_code in (400, 404) and "JWT_SECRET_KEY" not in response.text
    finally:
        target.unlink(missing_ok=True)


def test_cors_allows_the_dev_frontend_and_no_other_origin(api, main_client):
    preflight = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"}
    ok = main_client.options("/auth/login", headers={"Origin": "http://localhost:5173", **preflight})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = main_client.options("/auth/login", headers={"Origin": "https://evil.example", **preflight})
    assert "access-control-allow-origin" not in bad.headers


# ---------------------------------------------------------------------------
# Layer B: pure business-rule functions
# ---------------------------------------------------------------------------

def test_parse_schedule_and_scheduled_upload_time(backend):
    from datetime import date, time

    parse = backend.predictions_router._parse_schedule
    when = backend.predictions_router._scheduled_upload_time
    assert parse(None, None) == (None, None)
    assert parse("2026-05-01", "09:30") == (date(2026, 5, 1), time(9, 30))
    assert when(date(2026, 5, 1), time(9, 30)) == datetime(2026, 5, 1, 9, 30, tzinfo=timezone.utc)
    assert when(date(2026, 5, 1), None) == datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)
    assert when(None, None).tzinfo is not None


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.youtube.com/@ann", ("handle", "ann")),
        ("https://www.youtube.com/channel/UCabc123", ("id", "UCabc123")),
        ("https://www.youtube.com/user/annuser", ("username", "annuser")),
        ("https://www.youtube.com/c/AnnChannel", ("query", "AnnChannel")),
        ("  https://youtube.com/@ann/videos  ", ("handle", "ann")),
        ("@ann", ("handle", "ann")),
        ("annchannel", ("query", "annchannel")),
    ],
)
def test_channel_url_locator(backend, url, expected):
    import youtube

    assert youtube._locator_from_url(url) == expected


@pytest.mark.parametrize("url", ["", "   ", "https://youtube.com/", "https://youtube.com"])
def test_channel_url_locator_rejects_urls_with_no_channel(backend, url):
    import youtube

    with pytest.raises(youtube.YouTubeResolutionError):
        youtube._locator_from_url(url)


def test_storage_saves_under_a_random_name_and_keeps_only_the_extension(api):
    storage = api.backend.storage
    a = storage.save_upload(b"1", "../../evil name.PNG")
    b = storage.save_upload(b"2", "../../evil name.PNG")
    assert a != b and a.startswith("/uploads/") and a.endswith(".PNG") and "evil" not in a
    assert (api.uploads / Path(a).name).read_bytes() == b"1"
    assert storage.save_upload(b"3", None).count(".") == 0  # no filename -> no extension


def test_storage_delete_is_idempotent_and_confined_to_the_uploads_dir(api):
    storage = api.backend.storage
    served = storage.save_upload(b"x", "a.png")
    storage.delete_upload(served)
    storage.delete_upload(served)  # already gone
    storage.delete_upload(None)
    outside = api.uploads.parent / "keep-me.txt"
    outside.write_text("keep")
    storage.delete_upload("/uploads/../keep-me.txt")  # only the basename is used
    assert outside.exists()


@pytest.mark.parametrize(
    "age, hours", [(0, 0.083), (0.99, 0.083), (1, 0.083), (1.01, 0.25), (2, 0.25), (2.01, 1.0), (500, 1.0)]
)
def test_polling_interval_decays_with_video_age(age, hours):
    etl = BACKEND_DIR.parent / "youtube-etl-pipeline" / "youtube_extractor"
    pytest.importorskip("googleapiclient")
    sys.path.insert(0, str(etl))
    try:
        spec = importlib.util.spec_from_file_location("job2_under_test", etl / "job2_timeseries_collector.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except ImportError as exc:
        pytest.skip(f"ETL job dependencies unavailable: {exc}")
    finally:
        sys.path.remove(str(etl))
    assert module.select_interval_hours(age) == hours


@pytest.mark.parametrize(
    "text, seconds",
    [("PT8M32S", 512), ("PT1H", 3600), ("PT1H2M3S", 3723), ("PT45S", 45), ("pt5m", 300)],
)
def test_iso_duration_parsing(text, seconds):
    inference = _real_inference()
    assert inference._parse_duration_seconds(text) == seconds


@pytest.mark.parametrize("text", [None, "", "8:32", "garbage"])
def test_iso_duration_parsing_returns_nan_for_unusable_input(text):
    import math

    assert math.isnan(_real_inference()._parse_duration_seconds(text))


def _real_inference():
    """Loads backend/inference.py itself (the suite stubs it out); skips if ML deps are missing."""
    for dep in ("joblib", "numpy", "catboost", "sentence_transformers"):
        pytest.importorskip(dep)
    spec = importlib.util.spec_from_file_location("inference_under_test", BACKEND_DIR / "inference.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["inference_under_test"] = module  # @dataclass looks the module up by name
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"inference module could not be imported: {exc}")
    finally:
        sys.modules.pop("inference_under_test", None)
    return module
