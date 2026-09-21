"""Layer F: a backup must restore to an identical database.

pg_dump / pg_restore run inside the disposable Postgres container, so their
version always matches the server. The same procedure is documented in
DB_TESTING.md for the live database.
"""

import subprocess
import uuid

import psycopg2
import pytest
from psycopg2 import errors
from psycopg2.extras import Json

import db_helpers as h
from conftest import CONTAINER


def in_container(*args):
    subprocess.run(["docker", "exec", CONTAINER["name"], *args], check=True, capture_output=True, text=True)


@pytest.fixture(autouse=True)
def _needs_container():
    if not CONTAINER["name"]:
        pytest.skip("backup/restore tests need the Docker-managed test server")


def seed_every_table(cur):
    h.channel(cur, "UC1", title="Chan", country="LK", total_views=2**31 + 7, subscriber_count=5, video_count=2,
              channel_description="Ünïcödé — description")
    for i in range(3):
        h.video(cur, f"v{i}", "UC1", title=f"Video {i}", tags=["a", "b c"], category_id="10")
        for n in range(4):
            h.insert(cur, "view_timeseries", video_id=f"v{i}", view_count=n * 100, like_count=n, comment_count=1)
    h.insert(cur, "video_features", video_id="v0", title_embedding=h.vector(768, 0.25), thumbnail_embedding=h.vector(512, 0.5))
    h.insert(cur, "video_features", video_id="v1")
    uid = h.user(cur, channel_data=Json({"channel_id": "UC1", "title": "Chan", "nested": {"k": [1, 2]}}), subscribers=2**31 + 1)
    h.prediction(cur, user_id=uid, status="complete", predicted_views=1234, trajectory=Json([{"day": 1, "views": 5}]),
                 v_inf=1234.0, tau=2.5, used_channel_context=True, tags=["x"])
    h.notification(cur, user_id=uid, type="prediction_complete")
    h.insert(cur, "channel_stats_archive", channel_id="UCold", channel_title="Old")
    h.insert(cur, "videos_archive", video_id="vold", channel_id="UCold", published_at=h.NOW)
    h.insert(cur, "view_timeseries_archive", video_id="vold", scraped_at=h.NOW)


def public_tables(cur):
    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")
    return [r[0] for r in cur.fetchall()]


@pytest.fixture
def backup_and_restore(conn, cur, create_database):
    """Seeds a database, dumps it, restores the dump into an empty one; returns (source cur, restored conn)."""
    seed_every_table(cur)
    conn.commit()
    source_db = conn.info.dbname

    restored = create_database(from_template=False)
    restored_db = restored.info.dbname
    dump = f"/tmp/{uuid.uuid4().hex}.dump"
    in_container("pg_dump", "-U", "postgres", "-Fc", "-f", dump, source_db)
    in_container("pg_restore", "-U", "postgres", "--exit-on-error", "-d", restored_db, dump)
    in_container("rm", "-f", dump)
    yield cur, restored
    restored.rollback()


def test_restored_schema_is_identical(backup_and_restore):
    source_cur, restored = backup_and_restore
    with restored.cursor() as restored_cur:
        problems = h.diff_schema(h.schema_fingerprint(source_cur), h.schema_fingerprint(restored_cur))
    assert problems == []


IN_LIST_CONSTRAINTS = [
    ("videos", lambda c, value: h.video(c, video_id="new", channel_id="UC1", status=value),
     ["active", "archived", "deleted"], "paused", errors.CheckViolation),
    ("predictions", lambda c, value: h.prediction(c, user_id=1, status=value),
     ["draft", "complete"], "pending", errors.CheckViolation),
    ("notifications", lambda c, value: h.notification(c, user_id=1, type=value),
     ["welcome", "channel_fetch_success", "channel_fetch_error", "prediction_complete"], "bogus", errors.CheckViolation),
]


@pytest.mark.parametrize("table, insert_with, allowed, rejected, error", IN_LIST_CONSTRAINTS,
                         ids=[c[0] for c in IN_LIST_CONSTRAINTS])
def test_in_list_constraints_still_behave(backup_and_restore, table, insert_with, allowed, rejected, error):
    _, restored = backup_and_restore
    for value in allowed:
        with restored.cursor() as cur:
            if table == "videos":
                cur.execute("DELETE FROM videos WHERE video_id = 'new'")
            insert_with(cur, value)
        restored.rollback()
    with restored.cursor() as cur:
        with pytest.raises(error):
            insert_with(cur, rejected)
    restored.rollback()


def test_every_table_has_the_same_row_count(backup_and_restore):
    source_cur, restored = backup_and_restore
    tables = public_tables(source_cur)
    with restored.cursor() as restored_cur:
        assert public_tables(restored_cur) == tables
        source_counts = {t: h.count(source_cur, t) for t in tables}
        restored_counts = {t: h.count(restored_cur, t) for t in tables}
    assert restored_counts == source_counts
    # the comparison is only meaningful if the source had data everywhere
    assert all(count > 0 for count in source_counts.values()), source_counts


def test_every_row_survives_unchanged(backup_and_restore):
    source_cur, restored = backup_and_restore
    tables = public_tables(source_cur)
    with restored.cursor() as restored_cur:
        assert h.snapshot_rows(restored_cur, tables) == h.snapshot_rows(source_cur, tables)


def test_special_values_survive(backup_and_restore):
    _, restored = backup_and_restore
    with restored.cursor() as cur:
        cur.execute("SELECT total_views, channel_description FROM channel_stats")
        assert cur.fetchone() == (2**31 + 7, "Ünïcödé — description")
        cur.execute("SELECT channel_data FROM users")
        assert cur.fetchone()[0]["nested"] == {"k": [1, 2]}
        cur.execute("SELECT tags FROM videos WHERE video_id = 'v0'")
        assert cur.fetchone()[0] == ["a", "b c"]
        cur.execute("SELECT title_embedding::text = %s, thumbnail_embedding::text = %s FROM video_features WHERE video_id = 'v0'",
                    (h.vector(768, 0.25), h.vector(512, 0.5)))
        assert cur.fetchone() == (True, True)


def test_id_sequences_continue_after_the_restored_maximum(backup_and_restore):
    _, restored = backup_and_restore
    with restored.cursor() as cur:
        cur.execute("SELECT MAX(id) FROM view_timeseries")
        (highest_snapshot,) = cur.fetchone()
        h.insert(cur, "view_timeseries", video_id="v0", view_count=1)
        cur.execute("SELECT MAX(id) FROM view_timeseries")
        assert cur.fetchone()[0] > highest_snapshot

        cur.execute("SELECT MAX(id) FROM users")
        (highest_user,) = cur.fetchone()
        new_user = h.user(cur, email="after-restore@example.com")
        assert new_user > highest_user


def test_constraints_are_still_enforced(backup_and_restore):
    _, restored = backup_and_restore
    with restored.cursor() as cur:
        with pytest.raises(errors.CheckViolation):
            h.insert(cur, "view_timeseries", video_id="v0", view_count=-1)
    restored.rollback()
    with restored.cursor() as cur:
        with pytest.raises(errors.ForeignKeyViolation):
            h.insert(cur, "videos", video_id="x", channel_id="nope", published_at=h.NOW)
    restored.rollback()
    with restored.cursor() as cur:
        with pytest.raises(errors.CheckViolation):
            h.notification(cur, user_id=1, type="not-a-real-type")


def test_cascades_still_work(backup_and_restore):
    _, restored = backup_and_restore
    with restored.cursor() as cur:
        cur.execute("DELETE FROM channel_stats WHERE channel_id = 'UC1'")
        assert (h.count(cur, "videos"), h.count(cur, "view_timeseries"), h.count(cur, "video_features")) == (0, 0, 0)


def test_view_and_security_settings_survive(backup_and_restore):
    source_cur, restored = backup_and_restore
    with restored.cursor() as cur:
        cur.execute("SELECT size_tier, tier_category FROM channel_stats_enriched WHERE channel_id = 'UC1'")
        assert cur.fetchone() == ("Micro (<1K)", "10")
        cur.execute("SELECT relname, relrowsecurity FROM pg_class WHERE relnamespace = 'public'::regnamespace "
                    "AND relkind = 'r' ORDER BY 1")
        restored_rls = cur.fetchall()
        cur.execute("SELECT obj_description('users'::regclass), obj_description('channel_stats_enriched'::regclass)")
        restored_comments = cur.fetchone()
    source_cur.execute("SELECT relname, relrowsecurity FROM pg_class WHERE relnamespace = 'public'::regnamespace "
                       "AND relkind = 'r' ORDER BY 1")
    assert restored_rls == source_cur.fetchall()
    source_cur.execute("SELECT obj_description('users'::regclass), obj_description('channel_stats_enriched'::regclass)")
    assert restored_comments == source_cur.fetchone() and all(restored_comments)
