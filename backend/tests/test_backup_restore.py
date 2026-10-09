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
    uid = h.user(cur, full_name="Ünïcödé — name", channel_data=Json({"channel_id": "UC1", "title": "Chan", "nested": {"k": [1, 2]}}), subscribers=2**31 + 1)
    h.prediction(cur, user_id=uid, status="complete", predicted_views=1234, trajectory=Json([{"day": 1, "views": 5}]),
                 v_inf=1234.0, tau=2.5, used_channel_context=True, tags=["a", "b c"])
    h.notification(cur, user_id=uid, type="prediction_complete")
    h.insert(cur, "channel_history_cache", channel_id="UC1", encoder="clip|clip|{title}", warmed_at=h.NOW)
    h.insert(cur, "channel_history_videos", channel_id="UC1", video_id="v0", published_at=h.NOW, view_count=42,
             duration_s=61.5, text_embedding=[0.25] * 512, image_embedding=None)
    cur.execute("INSERT INTO admins (full_name, email, password_hash) VALUES ('Root', 'root@x.com', '$2b$12$h') RETURNING id")
    admin_id = cur.fetchone()[0]
    h.insert(cur, "admin_audit_log", admin_id=admin_id, action="user.disable", target_type="user", target_id=uid,
             details=Json({"email": "user@example.com"}))


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
        cur.execute("SELECT full_name, subscribers, channel_data FROM users")
        name, subscribers, data = cur.fetchone()
        assert (name, subscribers) == ("Ünïcödé — name", 2**31 + 1)
        assert data["nested"] == {"k": [1, 2]}
        cur.execute("SELECT tags FROM predictions")
        assert cur.fetchone()[0] == ["a", "b c"]
        cur.execute("SELECT text_embedding FROM channel_history_videos")
        assert cur.fetchone()[0] == [0.25] * 512


def test_id_sequences_continue_after_the_restored_maximum(backup_and_restore):
    _, restored = backup_and_restore
    with restored.cursor() as cur:
        cur.execute("SELECT MAX(id) FROM users")
        (highest_user,) = cur.fetchone()
        new_user = h.user(cur, email="after-restore@example.com")
        assert new_user > highest_user


def test_constraints_are_still_enforced(backup_and_restore):
    _, restored = backup_and_restore
    with restored.cursor() as cur:
        with pytest.raises(errors.CheckViolation):
            h.insert(cur, "channel_history_videos", channel_id="UC1", video_id="neg", published_at=h.NOW,
                     view_count=-1, text_embedding=[0.1] * 512)
    restored.rollback()
    with restored.cursor() as cur:
        with pytest.raises(errors.ForeignKeyViolation):
            h.prediction(cur, user_id=999)
    restored.rollback()
    with restored.cursor() as cur:
        with pytest.raises(errors.CheckViolation):
            h.notification(cur, user_id=1, type="not-a-real-type")


def test_cascades_still_work(backup_and_restore):
    _, restored = backup_and_restore
    with restored.cursor() as cur:
        cur.execute("DELETE FROM channel_history_cache WHERE channel_id = 'UC1'")
        assert h.count(cur, "channel_history_videos") == 0
        cur.execute("DELETE FROM users")
        assert (h.count(cur, "predictions"), h.count(cur, "notifications")) == (0, 0)


def test_comments_and_security_settings_survive(backup_and_restore):
    source_cur, restored = backup_and_restore
    with restored.cursor() as cur:
        cur.execute("SELECT relname, relrowsecurity FROM pg_class WHERE relnamespace = 'public'::regnamespace "
                    "AND relkind = 'r' ORDER BY 1")
        restored_rls = cur.fetchall()
        cur.execute("SELECT obj_description('users'::regclass), obj_description('predictions'::regclass)")
        restored_comments = cur.fetchone()
    source_cur.execute("SELECT relname, relrowsecurity FROM pg_class WHERE relnamespace = 'public'::regnamespace "
                       "AND relkind = 'r' ORDER BY 1")
    assert restored_rls == source_cur.fetchall()
    source_cur.execute("SELECT obj_description('users'::regclass), obj_description('predictions'::regclass)")
    assert restored_comments == source_cur.fetchone() and all(restored_comments)
