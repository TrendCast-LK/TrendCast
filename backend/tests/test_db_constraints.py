"""Layer B: constraint and relational integrity tests.

Each negative case runs a single statement that the database must reject;
the positive cases confirm valid data (including boundary values) is accepted.
"""

import pytest
from psycopg2 import DataError, errors

import db_helpers as h

Check = errors.CheckViolation
Fk = errors.ForeignKeyViolation
Unique = errors.UniqueViolation
NotNull = errors.NotNullViolation
TooLong = errors.StringDataRightTruncation


def _duplicate_channel(c):
    h.channel(c)
    h.channel(c)


def _duplicate_email(c):
    h.user(c, email="dup@example.com")
    h.user(c, email="dup@example.com")


def _feature(c, **over):
    h.video(c)
    h.insert(c, "video_features", video_id="vid001", **over)


REJECTED = [
    # channel_stats
    pytest.param(lambda c: h.channel(c, total_views=-1), Check, id="channel-negative-total_views"),
    pytest.param(lambda c: h.channel(c, subscriber_count=-1), Check, id="channel-negative-subscribers"),
    pytest.param(lambda c: h.channel(c, video_count=-1), Check, id="channel-negative-video_count"),
    pytest.param(_duplicate_channel, Unique, id="channel-duplicate-id"),
    pytest.param(lambda c: h.channel(c, channel_title=None), NotNull, id="channel-null-title"),
    # videos
    pytest.param(lambda c: h.video(c, current_interval_hours=0), Check, id="video-interval-zero"),
    pytest.param(lambda c: h.video(c, current_interval_hours=-1), Check, id="video-interval-negative"),
    pytest.param(lambda c: h.video(c, parent=False), Fk, id="video-unknown-channel"),
    pytest.param(lambda c: h.video(c, status="paused"), Check, id="video-invalid-status"),
    pytest.param(lambda c: h.video(c, status="x" * 17), TooLong, id="video-status-over-16-chars"),
    pytest.param(lambda c: h.video(c, published_at=None), NotNull, id="video-null-published_at"),
    # view_timeseries
    pytest.param(lambda c: h.timeseries(c, view_count=-1), Check, id="timeseries-negative-views"),
    pytest.param(lambda c: h.timeseries(c, like_count=-1), Check, id="timeseries-negative-likes"),
    pytest.param(lambda c: h.timeseries(c, comment_count=-1), Check, id="timeseries-negative-comments"),
    pytest.param(lambda c: h.timeseries(c, video_id="ghost", parent=False), Fk, id="timeseries-unknown-video"),
    # users
    pytest.param(_duplicate_email, Unique, id="user-duplicate-email"),
    pytest.param(lambda c: h.user(c, subscribers=-1), Check, id="user-negative-subscribers"),
    pytest.param(lambda c: h.user(c, monthly_views=-1), Check, id="user-negative-monthly_views"),
    pytest.param(lambda c: h.user(c, password_hash=None), NotNull, id="user-null-password_hash"),
    # predictions
    pytest.param(lambda c: h.prediction(c, status="pending"), Check, id="prediction-invalid-status"),
    pytest.param(lambda c: h.prediction(c, user_id=999), Fk, id="prediction-unknown-user"),
    pytest.param(lambda c: h.prediction(c, title=None), NotNull, id="prediction-null-title"),
    # notifications
    pytest.param(lambda c: h.notification(c, user_id=999), Fk, id="notification-unknown-user"),
    pytest.param(lambda c: h.notification(c, message=None), NotNull, id="notification-null-message"),
    pytest.param(lambda c: h.notification(c, type="not-a-real-type"), Check, id="notification-invalid-type"),
    # video_features
    pytest.param(lambda c: _feature(c, title_embedding=h.vector(3)), DataError, id="features-title-wrong-dim"),
    pytest.param(lambda c: _feature(c, thumbnail_embedding=h.vector(768)), DataError, id="features-thumbnail-wrong-dim"),
    pytest.param(
        lambda c: h.insert(c, "video_features", video_id="ghost"), Fk, id="features-unknown-video"
    ),
]


@pytest.mark.parametrize("action, error", REJECTED)
def test_invalid_data_is_rejected(cur, action, error):
    with pytest.raises(error):
        action(cur)


# ---------------------------------------------------------------------------
# Valid data and boundaries are accepted
# ---------------------------------------------------------------------------

BEYOND_INT32 = 2**31 + 5


def test_channel_counts_accept_zero_and_values_beyond_int32(cur):
    h.channel(cur, "UC_zero", total_views=0, subscriber_count=0, video_count=0)
    h.channel(cur, "UC_big", total_views=BEYOND_INT32, subscriber_count=BEYOND_INT32)
    cur.execute("SELECT total_views, subscriber_count FROM channel_stats WHERE channel_id = 'UC_big'")
    assert cur.fetchone() == (BEYOND_INT32, BEYOND_INT32)


def test_timeseries_counts_accept_zero_and_values_beyond_int32(cur):
    h.timeseries(cur, view_count=0, like_count=0, comment_count=0)
    h.timeseries(cur, view_count=BEYOND_INT32)
    assert h.count(cur, "view_timeseries") == 2


def test_user_baselines_accept_values_beyond_int32(cur):
    uid = h.user(cur, subscribers=BEYOND_INT32, monthly_views=BEYOND_INT32)
    cur.execute("SELECT subscribers, monthly_views FROM users WHERE id = %s", (uid,))
    assert cur.fetchone() == (BEYOND_INT32, BEYOND_INT32)


@pytest.mark.parametrize("status", ["active", "archived", "deleted"])
def test_video_accepts_each_documented_status(cur, status):
    h.video(cur, status=status)
    assert h.count(cur, "videos", "status = %s", (status,)) == 1


def test_video_defaults(cur):
    h.video(cur)
    cur.execute("SELECT status, current_interval_hours FROM videos")
    status, interval = cur.fetchone()
    assert (status, float(interval)) == ("active", 6.0)


def test_video_accepts_smallest_positive_interval(cur):
    h.video(cur, current_interval_hours="0.01")
    assert h.count(cur, "videos") == 1


@pytest.mark.parametrize("status", ["draft", "complete"])
def test_prediction_accepts_each_documented_status(cur, status):
    h.prediction(cur, status=status)
    assert h.count(cur, "predictions", "status = %s", (status,)) == 1


def test_prediction_defaults_to_draft_with_empty_tags(cur):
    h.prediction(cur)
    cur.execute("SELECT status, tags FROM predictions")
    assert cur.fetchone() == ("draft", [])


def test_notification_defaults_to_unread(cur):
    h.notification(cur)
    cur.execute("SELECT read FROM notifications")
    assert cur.fetchone() == (False,)


@pytest.mark.parametrize("kind", ["welcome", "channel_fetch_success", "channel_fetch_error", "prediction_complete"])
def test_notification_accepts_each_documented_type(cur, kind):
    h.notification(cur, type=kind)
    assert h.count(cur, "notifications", "type = %s", (kind,)) == 1


def test_user_channel_data_round_trips_as_jsonb(cur):
    from psycopg2.extras import Json

    snapshot = {"title": "My Channel", "subscriber_hidden": False, "video_count": 12}
    uid = h.user(cur, channel_data=Json(snapshot))
    cur.execute("SELECT channel_data FROM users WHERE id = %s", (uid,))
    assert cur.fetchone()[0] == snapshot


def test_video_features_accept_correct_dimensions_and_nulls(cur):
    _feature(cur, title_embedding=h.vector(768), thumbnail_embedding=h.vector(512))
    h.video(cur, "vid002")
    h.insert(cur, "video_features", video_id="vid002")
    assert h.count(cur, "video_features") == 2


# ---------------------------------------------------------------------------
# Cascades
# ---------------------------------------------------------------------------

def _seed_channel_tree(cur):
    h.video(cur)
    h.timeseries(cur, view_count=1)
    h.insert(cur, "video_features", video_id="vid001", title_embedding=h.vector(768))


def test_deleting_channel_cascades_to_videos_timeseries_and_features(cur):
    _seed_channel_tree(cur)
    cur.execute("DELETE FROM channel_stats WHERE channel_id = 'UC001'")
    assert [h.count(cur, t) for t in ("videos", "view_timeseries", "video_features")] == [0, 0, 0]


def test_deleting_video_cascades_to_timeseries_and_features_only(cur):
    _seed_channel_tree(cur)
    cur.execute("DELETE FROM videos WHERE video_id = 'vid001'")
    assert [h.count(cur, t) for t in ("view_timeseries", "video_features")] == [0, 0]
    assert h.count(cur, "channel_stats") == 1


def test_deleting_user_cascades_to_predictions_and_notifications(cur):
    uid = h.user(cur)
    other = h.user(cur, email="other@example.com")
    h.prediction(cur, user_id=uid)
    h.notification(cur, user_id=uid)
    h.prediction(cur, user_id=other)
    cur.execute("DELETE FROM users WHERE id = %s", (uid,))
    assert h.count(cur, "predictions", "user_id = %s", (uid,)) == 0
    assert h.count(cur, "notifications", "user_id = %s", (uid,)) == 0
    assert h.count(cur, "predictions", "user_id = %s", (other,)) == 1
