"""Layer E: the read-only data-quality checks (tools/data_quality.py).

A healthy dataset must pass every check; each scenario then breaks exactly one
thing and must trip exactly the matching check.
"""

import json
from datetime import datetime, timedelta, timezone

import psycopg2
import pytest
from psycopg2.extras import Json

import db_helpers as h
from tools import data_quality as dq

NOW = datetime.now(timezone.utc)


def ago(**delta):
    return NOW - timedelta(**delta)


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

def add_video(cur, video_id, channel_id="UC1", published_at=None, embedded=True, parent=True, **over):
    values = {"title": "T", "thumbnail_url": "http://t", "category_id": "10", **over}
    h.video(cur, video_id=video_id, channel_id=channel_id, published_at=published_at or ago(days=3),
            parent=parent, **values)
    if embedded:
        h.insert(cur, "video_features", video_id=video_id)


def add_snapshot(cur, video_id, scraped_at, views):
    h.insert(cur, "view_timeseries", video_id=video_id, scraped_at=scraped_at, view_count=views)


def seed_healthy(cur):
    h.channel(cur, "UC1", published_at=ago(days=900), processed_at=ago(hours=2))
    for video_id in ("v1", "v2"):
        add_video(cur, video_id)
        for hours_ago, views in ((48, 10), (24, 100), (1, 500)):
            add_snapshot(cur, video_id, ago(hours=hours_ago), views)
    uid = h.user(cur, channel_url="https://youtube.com/@x", channel_data=Json({"channel_id": "UC1"}))
    h.prediction(cur, user_id=uid, status="complete", predicted_views=1000, trajectory=Json([]), v_inf=1000.0, tau=2.0)
    h.prediction(cur, user_id=uid, status="draft")
    h.notification(cur, user_id=uid)
    return uid


def without_foreign_keys(cur):
    """Lets a test plant rows the foreign keys would normally forbid."""
    cur.execute("SET session_replication_role = replica")


@pytest.fixture
def healthy(conn, cur):
    seed_healthy(cur)
    conn.commit()
    return cur


def failing(conn, **kwargs):
    """Runs the checks on the committed data over a read-only connection."""
    conn.commit()
    ro = dq.connect_readonly(conn_dsn(conn))
    try:
        with ro.cursor() as ro_cur:
            results = dq.run_checks(ro_cur, **kwargs)
    finally:
        ro.close()
    return {r.check.name: r for r in results if not r.passed}


@pytest.fixture(autouse=True)
def _bind_dsn(db_dsn):
    global conn_dsn
    conn_dsn = db_dsn


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------

def test_healthy_dataset_passes_every_check(conn, healthy):
    assert failing(conn) == {}


def test_empty_database_passes_every_check(conn):
    assert failing(conn) == {}


def test_every_check_has_a_unique_name_and_valid_severity():
    names = [c.name for c in dq.CHECKS]
    assert len(names) == len(set(names))
    assert {c.severity for c in dq.CHECKS} <= {dq.ERROR, dq.WARN}


# ---------------------------------------------------------------------------
# One broken thing per scenario
# ---------------------------------------------------------------------------

def orphan_video(cur):
    without_foreign_keys(cur)
    add_video(cur, "orphan", channel_id="UC_missing", parent=False)


def orphan_snapshot(cur):
    without_foreign_keys(cur)
    add_snapshot(cur, "no_such_video", ago(hours=1), 5)


def orphan_embedding(cur):
    without_foreign_keys(cur)
    h.insert(cur, "video_features", video_id="no_such_video")


def orphan_prediction(cur):
    without_foreign_keys(cur)
    h.insert(cur, "predictions", user_id=999, title="ghost")


def orphan_notification(cur):
    without_foreign_keys(cur)
    h.insert(cur, "notifications", user_id=999, type="welcome", title="t", message="m")


def duplicate_snapshot(cur):
    stamp = ago(hours=5)
    add_snapshot(cur, "v1", stamp, 200)  # between the 100 and 500 snapshots, so views stay monotonic
    add_snapshot(cur, "v1", stamp, 200)


def future_snapshot(cur):
    add_snapshot(cur, "v1", NOW + timedelta(days=1), 900)


def future_video(cur):
    add_video(cur, "v_future", published_at=NOW + timedelta(days=2))


def future_channel(cur):
    h.channel(cur, "UC_future", published_at=NOW + timedelta(days=2), processed_at=ago(hours=1))


def decreasing_views(cur):
    add_snapshot(cur, "v1", ago(minutes=30), 400)  # v1 was at 500 half an hour earlier


def snapshot_before_publish(cur):
    add_video(cur, "v_new", published_at=ago(hours=1))
    add_snapshot(cur, "v_new", ago(days=1), 3)


def stale_channel(cur):
    h.channel(cur, "UC_stale", processed_at=ago(days=30), published_at=ago(days=900))


def missing_metadata(cur):
    for i in range(3):
        add_video(cur, f"bare{i}", title=None)


def missing_embeddings(cur):
    for i in range(3):
        add_video(cur, f"unembedded{i}", embedded=False)


def complete_without_outputs(cur):
    h.insert(cur, "predictions", user_id=_first_user(cur), title="x", status="complete")


def draft_with_outputs(cur):
    h.insert(cur, "predictions", user_id=_first_user(cur), title="x", status="draft", predicted_views=5)


def case_variant_emails(cur):
    h.user(cur, email="Person@example.com")
    h.user(cur, email="person@example.com")


def never_fetched_channel(cur):
    h.user(cur, email="new@example.com", channel_url="https://youtube.com/@y")


def _first_user(cur):
    cur.execute("SELECT id FROM users ORDER BY id LIMIT 1")
    return cur.fetchone()[0]


SCENARIOS = [
    pytest.param(orphan_video, "orphan_videos", id="orphan-video"),
    pytest.param(orphan_snapshot, "orphan_view_timeseries", id="orphan-snapshot"),
    pytest.param(orphan_embedding, "orphan_video_features", id="orphan-embedding"),
    pytest.param(orphan_prediction, "orphan_predictions", id="orphan-prediction"),
    pytest.param(orphan_notification, "orphan_notifications", id="orphan-notification"),
    pytest.param(duplicate_snapshot, "duplicate_snapshots", id="duplicate-snapshot"),
    pytest.param(future_snapshot, "future_snapshots", id="future-snapshot"),
    pytest.param(future_video, "future_publish_dates", id="future-video"),
    pytest.param(future_channel, "future_publish_dates", id="future-channel"),
    pytest.param(decreasing_views, "decreasing_view_counts", id="decreasing-views"),
    pytest.param(snapshot_before_publish, "snapshot_before_publish", id="snapshot-before-publish"),
    pytest.param(stale_channel, "stale_channels", id="stale-channel"),
    pytest.param(missing_metadata, "incomplete_video_metadata", id="missing-metadata"),
    pytest.param(missing_embeddings, "incomplete_video_metadata", id="missing-embeddings"),
    pytest.param(complete_without_outputs, "complete_predictions_missing_outputs", id="complete-without-outputs"),
    pytest.param(draft_with_outputs, "draft_predictions_with_outputs", id="draft-with-outputs"),
    pytest.param(case_variant_emails, "duplicate_emails_case_insensitive", id="case-variant-emails"),
    pytest.param(never_fetched_channel, "users_channel_never_fetched", id="never-fetched-channel"),
]


@pytest.mark.parametrize("break_it, expected", SCENARIOS)
def test_each_problem_trips_exactly_its_check(conn, healthy, break_it, expected):
    break_it(healthy)
    assert set(failing(conn)) == {expected}


def test_failing_check_reports_count_and_sample(conn, healthy):
    for i in range(8):
        add_video(healthy, f"bare{i}", title=None)
    result = failing(conn)["incomplete_video_metadata"]
    assert result.count == 1  # one offending field: title
    assert result.sample[0][0] == "title"


def test_orphan_sample_names_the_offending_rows(conn, healthy):
    orphan_video(healthy)
    result = failing(conn)["orphan_videos"]
    assert result.count == 1 and result.sample == [("orphan", "UC_missing")]


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

def test_stale_days_threshold(conn, healthy):
    h.channel(healthy, "UC_5d", processed_at=ago(days=5), published_at=ago(days=900))
    assert "stale_channels" not in failing(conn, stale_days=7)
    assert "stale_channels" in failing(conn, stale_days=3)


def test_missing_metadata_threshold_is_strictly_greater(conn, healthy):
    # healthy has 2 videos; add 8 more of which 1 lacks a title: 1 of 10 = exactly 10%
    for i in range(7):
        add_video(healthy, f"ok{i}")
    add_video(healthy, "bare", title=None)
    assert "incomplete_video_metadata" not in failing(conn, max_missing_pct=10)
    assert "incomplete_video_metadata" in failing(conn, max_missing_pct=9.9)


def test_decreasing_view_counts_only_flag_real_decreases(conn, healthy):
    add_snapshot(healthy, "v1", ago(minutes=30), 500)  # equal to the previous value
    assert "decreasing_view_counts" not in failing(conn)


# ---------------------------------------------------------------------------
# Metrics and the command line
# ---------------------------------------------------------------------------

def test_metrics_report_row_counts_and_snapshot_range(conn, healthy):
    conn.commit()
    ro = dq.connect_readonly(conn_dsn(conn))
    try:
        with ro.cursor() as ro_cur:
            metrics = dq.collect_metrics(ro_cur, min_snapshots=3)
    finally:
        ro.close()
    assert metrics["rows.videos"] == 2
    assert metrics["rows.view_timeseries"] == 6
    assert metrics["rows.users"] == 1
    assert metrics["videos_with_at_least_3_snapshots"] == 2
    assert metrics["snapshots.oldest"] < metrics["snapshots.newest"]


def test_connection_is_read_only(conn, healthy):
    conn.commit()
    ro = dq.connect_readonly(conn_dsn(conn))
    try:
        with ro.cursor() as ro_cur:
            with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
                ro_cur.execute("DELETE FROM users")
            with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
                ro_cur.execute("CREATE TABLE sneaky (id int)")
    finally:
        ro.close()
    healthy.execute("SELECT COUNT(*) FROM users")
    assert healthy.fetchone() == (1,)


def test_cli_exits_zero_on_healthy_data(conn, healthy, capsys):
    conn.commit()
    assert dq.main(["--db-url", conn_dsn(conn)]) == 0
    out = capsys.readouterr().out
    assert "[PASS] orphan_videos: 0" in out and "0 failing, 0 warning(s)" in out


def test_cli_exits_one_on_an_integrity_error(conn, healthy, capsys):
    duplicate_snapshot(healthy)
    conn.commit()
    assert dq.main(["--db-url", conn_dsn(conn)]) == 1
    out = capsys.readouterr().out
    assert "[FAIL] duplicate_snapshots: 1" in out


def test_cli_warnings_only_fail_in_strict_mode(conn, healthy, capsys):
    stale_channel(healthy)
    conn.commit()
    assert dq.main(["--db-url", conn_dsn(conn)]) == 0
    assert "[WARN] stale_channels" in capsys.readouterr().out
    assert dq.main(["--db-url", conn_dsn(conn), "--strict"]) == 1


def test_cli_json_output_is_machine_readable(conn, healthy, capsys):
    duplicate_snapshot(healthy)
    conn.commit()
    dq.main(["--db-url", conn_dsn(conn), "--json"])
    report = json.loads(capsys.readouterr().out)
    by_name = {c["name"]: c for c in report["checks"]}
    assert by_name["duplicate_snapshots"]["passed"] is False and by_name["duplicate_snapshots"]["count"] == 1
    assert by_name["orphan_videos"]["passed"] is True
    assert report["metrics"]["rows.videos"] == 2


def test_cli_without_a_database_url_exits_two(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    monkeypatch.setattr(dq, "BACKEND_ENV_PATH", tmp_path / "missing.env")
    assert dq.main([]) == 2
    assert "No database URL" in capsys.readouterr().err


def test_cli_with_an_unreachable_database_exits_two(capsys):
    assert dq.main(["--db-url", "postgresql://nobody:x@127.0.0.1:1/none?connect_timeout=2"]) == 2
    assert "Could not connect" in capsys.readouterr().err
