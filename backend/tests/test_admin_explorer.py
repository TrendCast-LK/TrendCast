"""Admin dashboard, phase 2: the predictions explorer and the channel history
cache page. Same harness as test_admin.py (real routers and database, YouTube
faked, model stubbed)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_admin import admin_headers, reset_admin_state  # noqa: F401  (autouse fixture)
from test_api_data import (
    CHANNEL,
    fake_youtube,  # noqa: F401  (autouse fixture)
    make_user,
    post_prediction,
    rows,
)


def add_prediction(api, user_id, title="t", *, status="complete", category=None, views=None, confidence=None,
                   context=None, age_days=0):
    api.cur.execute(
        "INSERT INTO predictions (user_id, title, status, category, predicted_views, confidence, "
        "used_channel_context, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW() - make_interval(days => %s)) "
        "RETURNING id",
        (user_id, title, status, category, views, confidence, context, age_days),
    )
    pid = api.cur.fetchone()[0]
    api.conn.commit()
    return pid


def explore(api, headers, **params):
    response = api.client.get("/admin/predictions", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Predictions explorer
# ---------------------------------------------------------------------------

def test_explorer_lists_every_users_predictions_newest_first(api):
    headers = admin_headers(api)
    ann, _ = make_user(api, "ann@example.com")
    bob, _ = make_user(api, "bob@example.com")
    old = add_prediction(api, ann, "old", age_days=3)
    new = add_prediction(api, bob, "new")

    body = explore(api, headers)
    assert body["total"] == 2
    assert [p["id"] for p in body["items"]] == [new, old]
    assert body["items"][0]["user_email"] == "bob@example.com" and body["items"][0]["user_name"] == "Ann"


def test_explorer_filters(api):
    headers = admin_headers(api)
    ann, _ = make_user(api, "ann@example.com")
    bob, _ = make_user(api, "bob@example.com")
    gaming = add_prediction(api, ann, "Speedrun", category="Gaming", views=100, context=True)
    draft = add_prediction(api, ann, "Draft idea", status="draft", category="Music")
    catboost = add_prediction(api, bob, "Cover song", category="Music", views=50, context=False)
    old = add_prediction(api, bob, "Old one", views=10, context=True, age_days=40)

    def ids(**params):
        return [p["id"] for p in explore(api, headers, **params)["items"]]

    assert ids(q="speed") == [gaming]
    assert set(ids(q="bob@")) == {catboost, old}
    assert ids(status="draft") == [draft]
    assert set(ids(category="Music")) == {draft, catboost}
    assert set(ids(user_id=ann)) == {gaming, draft}
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    assert ids(model="ensemble", ensemble_since=since) == [gaming]  # "old" predates the ensemble
    assert ids(model="catboost_only", ensemble_since=since) == [catboost]
    today = datetime.now(timezone.utc).date()
    assert old not in ids(date_from=(today - timedelta(days=7)).isoformat())
    assert ids(date_to=(today - timedelta(days=30)).isoformat()) == [old]


def test_explorer_rejects_unknown_filter_values(api):
    headers = admin_headers(api)
    assert api.client.get("/admin/predictions", params={"status": "weird"}, headers=headers).status_code == 422
    assert api.client.get("/admin/predictions", params={"model": "gpt"}, headers=headers).status_code == 422


def test_explorer_paginates_but_summarises_the_whole_filtered_set(api):
    headers = admin_headers(api)
    uid, _ = make_user(api)
    for i, views in enumerate([5, 50, 500, 5000, 50000]):
        add_prediction(api, uid, f"p{i}", views=views, confidence=0.1 * (i + 1) + 0.05, category="Gaming")
    add_prediction(api, uid, "draft", status="draft")

    body = explore(api, headers, limit=2)
    assert body["total"] == 6 and len(body["items"]) == 2
    s = body["summary"]
    assert (s["count"], s["complete"]) == (6, 5)
    assert s["median_views"] == 500
    assert s["avg_confidence"] == pytest.approx(0.35)
    assert [(b["low"], b["high"], b["count"]) for b in s["views_histogram"]] == [
        (1, 10, 1), (10, 100, 1), (100, 1000, 1), (1000, 10000, 1), (10000, 100000, 1),
    ]
    assert len(s["confidence_histogram"]) == 10  # empty buckets included
    assert [b["count"] for b in s["confidence_histogram"]] == [0, 1, 1, 1, 1, 1, 0, 0, 0, 0]
    assert s["categories"] == [{"category": "Gaming", "count": 5}]
    assert body["all_categories"] == ["Gaming"]


def test_explorer_with_no_predictions_is_empty_not_an_error(api):
    headers = admin_headers(api)
    body = explore(api, headers)
    assert body["total"] == 0 and body["items"] == [] and body["all_categories"] == []
    assert body["summary"]["median_views"] is None and body["summary"]["views_histogram"] == []


def test_prediction_detail_includes_owner_and_curve(api):
    headers = admin_headers(api)
    uid, user_headers = make_user(api)
    created = post_prediction(api, user_headers, draft=True, category="Gaming", tags="a,b").json()

    body = api.client.get(f"/admin/predictions/{created['id']}", headers=headers).json()
    assert body["user_id"] == uid and body["user_email"] == "ann@example.com"
    assert body["thumbnail_url"] == created["thumbnail_url"]
    assert body["trajectory"] == [] and body["status"] == "draft"
    assert api.client.get("/admin/predictions/999", headers=headers).status_code == 404


def test_admin_delete_removes_the_prediction_and_its_files_and_is_audited(api):
    headers = admin_headers(api)
    uid, user_headers = make_user(api)
    created = post_prediction(api, user_headers, draft=True).json()
    file = api.uploads / Path(created["thumbnail_url"]).name
    assert file.exists()

    assert api.client.delete(f"/admin/predictions/{created['id']}", headers=headers).status_code == 204
    assert rows(api, "SELECT COUNT(*) FROM predictions")[0][0] == 0
    assert not file.exists()
    details = rows(api, "SELECT details FROM admin_audit_log WHERE action = 'prediction.delete'")[0][0]
    assert details == {"title": created["title"], "user_id": uid, "user_email": "ann@example.com"}
    assert api.client.delete(f"/admin/predictions/{created['id']}", headers=headers).status_code == 404


# ---------------------------------------------------------------------------
# Channel history cache
# ---------------------------------------------------------------------------

class ReadyState:
    ready = True
    encoder_signature = "enc-v2"


def model_ready(api):
    import routers.admin_cache as admin_cache

    api.monkeypatch.setattr(admin_cache, "get_state", lambda: ReadyState())
    queued = []
    api.monkeypatch.setattr(admin_cache.channel_cache, "schedule_warm", lambda tasks, cid: queued.append(cid))
    return queued


def add_cache_entry(api, channel_id, *, encoder="enc-v2", warmed_hours_ago=1, error=None, videos=0):
    warmed = None if warmed_hours_ago is None else datetime.now(timezone.utc) - timedelta(hours=warmed_hours_ago)
    api.cur.execute(
        "INSERT INTO channel_history_cache (channel_id, encoder, warmed_at, last_error) VALUES (%s, %s, %s, %s)",
        (channel_id, encoder, warmed, error),
    )
    for i in range(videos):
        api.cur.execute(
            "INSERT INTO channel_history_videos (channel_id, video_id, published_at, view_count, text_embedding) "
            "VALUES (%s, %s, NOW() - make_interval(days => %s), 10, %s)",
            (channel_id, f"v{i}", i, [0.0] * 512),
        )
    api.conn.commit()


def cache(api, headers, **params):
    response = api.client.get("/admin/cache", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_cache_list_classifies_entries_and_includes_linked_channels_without_one(api):
    headers = admin_headers(api)
    model_ready(api)
    make_user(api)  # links CHANNEL, which has no cache row
    add_cache_entry(api, "UCfresh", videos=2)
    add_cache_entry(api, "UCold", warmed_hours_ago=48)
    add_cache_entry(api, "UCenc", encoder="enc-v1")
    add_cache_entry(api, "UCbroken", warmed_hours_ago=None, error="quota exceeded")

    body = cache(api, headers)
    by_id = {r["channel_id"]: r for r in body["items"]}
    assert by_id["UCfresh"]["status"] == "fresh" and by_id["UCfresh"]["video_count"] == 2
    assert by_id["UCold"]["status"] == "stale"
    assert by_id["UCenc"]["status"] == "stale" and by_id["UCenc"]["encoder_matches"] is False
    assert by_id["UCbroken"]["status"] == "never" and by_id["UCbroken"]["last_error"] == "quota exceeded"
    linked = by_id[CHANNEL["channel_id"]]
    assert linked["has_entry"] is False and linked["linked_users"] == 1 and linked["channel_title"] == CHANNEL["title"]
    assert body["summary"] == {
        "total": 5, "fresh": 1, "stale": 2, "never": 2, "with_error": 1, "hits_7d": 0, "forecasts_7d": 0,
    }
    assert body["items"][0]["channel_id"] == "UCbroken"  # errors first
    assert body["model_ready"] is True and body["ttl_hours"] == 24


def test_cache_list_filters(api):
    headers = admin_headers(api)
    model_ready(api)
    add_cache_entry(api, "UCfresh")
    add_cache_entry(api, "UCold", warmed_hours_ago=48)
    add_cache_entry(api, "UCbroken", error="boom")

    def ids(**params):
        return {r["channel_id"] for r in cache(api, headers, **params)["items"]}

    assert ids(status="stale") == {"UCold"}
    assert ids(status="error") == {"UCbroken"}
    assert ids(q="old") == {"UCold"}


def test_without_a_loaded_model_encoder_mismatch_is_unknown_not_stale(api):
    headers = admin_headers(api)  # the stub model state is not ready
    add_cache_entry(api, "UCenc", encoder="anything")
    body = cache(api, headers)
    assert body["model_ready"] is False
    assert body["items"][0]["status"] == "fresh" and body["items"][0]["encoder_matches"] is None


def test_cache_hit_rate_counts_recent_complete_forecasts(api):
    headers = admin_headers(api)
    uid, _ = make_user(api)
    add_prediction(api, uid, "hit", context=True)
    add_prediction(api, uid, "miss", context=False)
    add_prediction(api, uid, "old hit", context=True, age_days=10)
    add_prediction(api, uid, "draft", status="draft")
    summary = cache(api, headers)["summary"]
    assert (summary["hits_7d"], summary["forecasts_7d"]) == (1, 2)


def test_warming_needs_the_model(api):
    headers = admin_headers(api)
    add_cache_entry(api, "UCold", warmed_hours_ago=48)
    assert api.client.post("/admin/cache/UCold/warm", headers=headers).status_code == 503
    assert api.client.post("/admin/cache/warm-stale", headers=headers).status_code == 503


def test_warm_one_channel_queues_it_and_is_audited(api):
    headers = admin_headers(api)
    queued = model_ready(api)
    make_user(api)
    response = api.client.post(f"/admin/cache/{CHANNEL['channel_id']}/warm", headers=headers)
    assert response.status_code == 202 and queued == [CHANNEL["channel_id"]]
    details = rows(api, "SELECT details FROM admin_audit_log WHERE action = 'cache.warm'")[0][0]
    assert details == {"channel_id": CHANNEL["channel_id"]}


def test_warming_an_unknown_channel_is_refused(api):
    headers = admin_headers(api)
    queued = model_ready(api)
    assert api.client.post("/admin/cache/UCnobody/warm", headers=headers).status_code == 404
    assert queued == []


def test_warm_stale_queues_everything_not_fresh(api):
    headers = admin_headers(api)
    queued = model_ready(api)
    make_user(api)
    add_cache_entry(api, "UCfresh")
    add_cache_entry(api, "UCold", warmed_hours_ago=48)
    add_cache_entry(api, "UCfreshbroken", error="boom")

    body = api.client.post("/admin/cache/warm-stale", headers=headers).json()
    assert set(body["channel_ids"]) == {"UCold", "UCfreshbroken", CHANNEL["channel_id"]}
    assert body["channel_ids"][0] == CHANNEL["channel_id"]  # most-linked first
    assert sorted(queued) == sorted(body["channel_ids"]) and body["queued"] == 3
    assert rows(api, "SELECT COUNT(*) FROM admin_audit_log WHERE action = 'cache.warm_stale'")[0][0] == 1


def test_purge_drops_the_entry_and_its_videos(api):
    headers = admin_headers(api)
    add_cache_entry(api, "UCfresh", videos=3)
    assert api.client.delete("/admin/cache/UCfresh", headers=headers).status_code == 204
    assert rows(api, "SELECT COUNT(*) FROM channel_history_videos")[0][0] == 0
    assert rows(api, "SELECT COUNT(*) FROM admin_audit_log WHERE action = 'cache.purge'")[0][0] == 1
    assert api.client.delete("/admin/cache/UCfresh", headers=headers).status_code == 404
