"""Layer A: schema and migration tests.

Checks the SQL scripts build the documented schema, can be re-applied, and
that each migration brings an older database to the same schema as a fresh build.
"""

import re

import pytest

import db_helpers as h
from conftest import (
    INIT_SCRIPTS, MIGRATION_003, MIGRATION_004, MIGRATION_005, MIGRATION_006, apply_sql,
)

BIG, TXT, TS = "bigint", "text", "timestamp with time zone"

EXPECTED_COLUMNS = {
    "users": {
        "id": BIG,
        "full_name": "character varying(255)",
        "email": "character varying(255)",
        "password_hash": "character varying(255)",
        "subscribers": BIG,
        "monthly_views": BIG,
        "channel_url": TXT,
        "channel_data": "jsonb",
        "channel_fetch_error": TXT,
        "created_at": TS,
        "is_active": "boolean",
    },
    "predictions": {
        "id": BIG,
        "user_id": BIG,
        "title": "character varying(255)",
        "category": "character varying(128)",
        "tags": "text[]",
        "target_date": "date",
        "target_time": "character varying(8)",
        "thumbnail_path": TXT,
        "dataset_path": TXT,
        "status": "character varying(16)",
        "predicted_views": BIG,
        "confidence": "double precision",
        "change_vs_avg": "double precision",
        "trajectory": "jsonb",
        "v_inf": "double precision",
        "tau": "double precision",
        "used_channel_context": "boolean",
        "created_at": TS,
    },
    "notifications": {
        "id": BIG,
        "user_id": BIG,
        "type": "character varying(32)",
        "title": "character varying(255)",
        "message": TXT,
        "read": "boolean",
        "created_at": TS,
    },
    "channel_history_cache": {
        "channel_id": "character varying(64)",
        "encoder": TXT,
        "warmed_at": TS,
        "last_error": TXT,
        "updated_at": TS,
    },
    "channel_history_videos": {
        "channel_id": "character varying(64)",
        "video_id": "character varying(64)",
        "published_at": TS,
        "view_count": BIG,
        "duration_s": "double precision",
        "text_embedding": "real[]",
        "image_embedding": "real[]",
        "encoded_at": TS,
    },
    "admins": {
        "id": BIG,
        "full_name": "character varying(255)",
        "email": "character varying(255)",
        "password_hash": "character varying(255)",
        "is_active": "boolean",
        "last_login_at": TS,
        "created_at": TS,
    },
    "admin_audit_log": {
        "id": BIG,
        "admin_id": BIG,
        "action": "character varying(64)",
        "target_type": "character varying(32)",
        "target_id": BIG,
        "details": "jsonb",
        "created_at": TS,
    },
}

EXPECTED_INDEXES = {
    "idx_users_email", "idx_predictions_user_created",
    "idx_notifications_user_created", "idx_notifications_user_unread",
    "idx_admin_audit_log_created", "idx_admin_audit_log_target",
}

EXPECTED_CONSTRAINTS = {
    "chk_users_subscribers_positive", "chk_users_monthly_views_positive",
    "fk_predictions_user", "chk_predictions_status", "fk_notifications_user",
    "chk_notifications_type",
    "pk_channel_history_videos", "fk_channel_history_videos_channel",
    "chk_channel_history_videos_view_count", "chk_channel_history_videos_text_dim",
    "chk_channel_history_videos_image_dim",
    "chk_admins_email_lowercase", "fk_admin_audit_log_admin",
}

APP_TABLES = list(EXPECTED_COLUMNS)


# ---------------------------------------------------------------------------
# A1. Fresh build
# ---------------------------------------------------------------------------

def test_init_scripts_are_discovered():
    assert [p.name[:2] for p in INIT_SCRIPTS] == ["01", "02", "03"]


def test_fresh_build_applies_scripts_in_order(create_database):
    conn = create_database(from_template=False)
    for script in INIT_SCRIPTS:
        apply_sql(conn, script)
    with conn.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        tables = {r[0] for r in cur.fetchall()}
    assert set(APP_TABLES) == tables


# ---------------------------------------------------------------------------
# A3. Schema matches the documented tables
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("table", APP_TABLES)
def test_columns_and_types_match_docs(cur, table):
    actual = {name: typ for name, (typ, _) in h.columns(cur, table).items()}
    assert actual == EXPECTED_COLUMNS[table]


def test_expected_indexes_exist(cur):
    cur.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
    actual = {r[0] for r in cur.fetchall()}
    assert EXPECTED_INDEXES - actual == set()


def test_expected_constraints_exist(cur):
    cur.execute(
        """
        SELECT con.conname FROM pg_constraint con
        JOIN pg_namespace n ON n.oid = con.connamespace WHERE n.nspname = 'public'
        """
    )
    actual = {r[0] for r in cur.fetchall()}
    assert EXPECTED_CONSTRAINTS - actual == set()


def test_users_email_is_unique(cur):
    cur.execute(
        """
        SELECT pg_get_constraintdef(oid) FROM pg_constraint
        WHERE conrelid = 'users'::regclass AND contype = 'u'
        """
    )
    assert "UNIQUE (email)" in [r[0] for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# A2. Idempotency
# ---------------------------------------------------------------------------

def _seed_all_tables(cur):
    uid = h.user(cur)
    h.prediction(cur, user_id=uid)
    h.notification(cur, user_id=uid)


DATA_TABLES = ["users", "predictions", "notifications"]

RERUN_SCRIPTS = [pytest.param(p, id=p.name) for p in [*INIT_SCRIPTS, MIGRATION_003, MIGRATION_004, MIGRATION_005, MIGRATION_006]]


@pytest.mark.parametrize("script", RERUN_SCRIPTS)
def test_script_is_idempotent(conn, cur, script):
    _seed_all_tables(cur)
    conn.commit()
    before_schema = h.schema_fingerprint(cur)
    before_rows = h.snapshot_rows(cur, DATA_TABLES)

    apply_sql(conn, script)

    assert h.schema_fingerprint(cur) == before_schema
    assert h.snapshot_rows(cur, DATA_TABLES) == before_rows


# ---------------------------------------------------------------------------
# A4. Init scripts vs migrations
# ---------------------------------------------------------------------------

def test_migration_003_adds_notifications_type_check_to_legacy_db(cur, create_database):
    """A DB without the constraint, plus 003, equals a fresh build."""
    legacy = create_database()
    with legacy.cursor() as lcur:
        lcur.execute("ALTER TABLE notifications DROP CONSTRAINT chk_notifications_type")
    legacy.commit()
    apply_sql(legacy, MIGRATION_003)

    with legacy.cursor() as lcur:
        legacy_fp = h.schema_fingerprint(lcur)
    assert legacy_fp == h.schema_fingerprint(cur)


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

def test_every_public_table_has_row_level_security(cur):
    cur.execute(
        "SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace "
        "AND relkind = 'r' AND NOT relrowsecurity ORDER BY 1"
    )
    assert [r[0] for r in cur.fetchall()] == []


def test_migration_004_enables_row_level_security_on_a_legacy_db(cur, create_database):
    """A database created before RLS was enabled, plus 004, equals a fresh build.

    Only the tables 004 covers are damaged: tables added later (005, 006) turn
    RLS on in their own migration, so a pre-004 database never had them."""
    covered = re.findall(r"ALTER TABLE\s+(\w+)\s+ENABLE ROW LEVEL SECURITY", MIGRATION_004.read_text(encoding="utf-8"))
    assert "users" in covered
    legacy = create_database()
    with legacy.cursor() as lcur:
        for table in covered:
            lcur.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
    legacy.commit()
    with legacy.cursor() as lcur:
        assert h.schema_fingerprint(lcur) != h.schema_fingerprint(cur)  # the damage is visible
    apply_sql(legacy, MIGRATION_004)
    with legacy.cursor() as lcur:
        assert h.schema_fingerprint(lcur) == h.schema_fingerprint(cur)


def test_migration_005_adds_the_channel_history_cache_to_a_legacy_db(cur, create_database):
    """A database created before the forecast ensemble, plus 005, equals a fresh build."""
    legacy = create_database()
    with legacy.cursor() as lcur:
        lcur.execute("DROP TABLE channel_history_videos, channel_history_cache")
    legacy.commit()
    apply_sql(legacy, MIGRATION_005)
    with legacy.cursor() as lcur:
        assert h.schema_fingerprint(lcur) == h.schema_fingerprint(cur)


def test_migration_006_adds_the_admin_dashboard_to_a_legacy_db(cur, create_database):
    """A database created before the admin dashboard, plus 006, equals a fresh build."""
    legacy = create_database()
    with legacy.cursor() as lcur:
        lcur.execute("DROP TABLE admin_audit_log, admins")
        lcur.execute("ALTER TABLE users DROP COLUMN is_active")
    legacy.commit()
    apply_sql(legacy, MIGRATION_006)
    with legacy.cursor() as lcur:
        assert h.schema_fingerprint(lcur) == h.schema_fingerprint(cur)


def test_row_level_security_leaves_the_owner_unaffected(cur):
    """The backend connects as the owner; RLS with no policies must not hide rows from it."""
    h.user(cur)
    cur.execute("SELECT COUNT(*) FROM users")
    assert cur.fetchone() == (1,)
