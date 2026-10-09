"""Layer B: constraint and relational integrity tests.

Each negative case runs a single statement that the database must reject;
the positive cases confirm valid data (including boundary values) is accepted.
"""

import pytest
from psycopg2 import errors

import db_helpers as h

Check = errors.CheckViolation
Fk = errors.ForeignKeyViolation
Unique = errors.UniqueViolation
NotNull = errors.NotNullViolation


def _duplicate_email(c):
    h.user(c, email="dup@example.com")
    h.user(c, email="dup@example.com")


REJECTED = [
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
]


@pytest.mark.parametrize("action, error", REJECTED)
def test_invalid_data_is_rejected(cur, action, error):
    with pytest.raises(error):
        action(cur)


# ---------------------------------------------------------------------------
# Valid data and boundaries are accepted
# ---------------------------------------------------------------------------

BEYOND_INT32 = 2**31 + 5


def test_user_baselines_accept_values_beyond_int32(cur):
    uid = h.user(cur, subscribers=BEYOND_INT32, monthly_views=BEYOND_INT32)
    cur.execute("SELECT subscribers, monthly_views FROM users WHERE id = %s", (uid,))
    assert cur.fetchone() == (BEYOND_INT32, BEYOND_INT32)


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


# ---------------------------------------------------------------------------
# Cascades
# ---------------------------------------------------------------------------

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
