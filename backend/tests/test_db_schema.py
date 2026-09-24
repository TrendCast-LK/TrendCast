"""Layer A: schema and migration tests.

Checks the SQL scripts build the schema documented in CLAUDE.md, can be
re-applied, and that the archive tables and migration 002 stay in step with
the core tables.
"""

import pytest

import db_helpers as h
from conftest import INIT_SCRIPTS, MIGRATION_002, MIGRATION_003, MIGRATION_004, apply_sql

BIG, TXT, TS = "bigint", "text", "timestamp with time zone"

EXPECTED_COLUMNS = {
    "channel_stats": {
        "channel_id": "character varying(64)",
        "channel_title": "character varying(255)",
        "channel_description": TXT,
        "published_at": TS,
        "country": "character varying(10)",
        "total_views": BIG,
        "subscriber_count": BIG,
        "video_count": "integer",
        "processed_at": TS,
        "created_at": TS,
        "title": "character varying(255)",
        "tier_category": "character varying(64)",
        "uploads_playlist_id": "character varying(64)",
        "last_checked_at": TS,
    },
    "videos": {
        "video_id": "character varying(64)",
        "channel_id": "character varying(64)",
        "published_at": TS,
        "status": "character varying(16)",
        "last_polled_at": TS,
        "next_poll_at": TS,
        "current_interval_hours": "numeric(5,2)",
        "created_at": TS,
        "title": "character varying(255)",
        "description": TXT,
        "thumbnail_url": TXT,
        "tags": "text[]",
        "category_id": "character varying(16)",
        "duration": "character varying(32)",
    },
    "view_timeseries": {
        "id": BIG,
        "video_id": "character varying(64)",
        "scraped_at": TS,
        "view_count": BIG,
        "like_count": BIG,
        "comment_count": BIG,
    },
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
    "video_features": {
        "video_id": "character varying(64)",
        "title_embedding": "vector(768)",
        "thumbnail_embedding": "vector(512)",
        "computed_at": TS,
    },
}

EXPECTED_INDEXES = {
    "idx_channel_stats_subscribers", "idx_channel_stats_processed",
    "idx_channel_stats_views", "idx_channel_stats_country",
    "idx_channel_stats_last_checked",
    "idx_videos_next_poll_at", "idx_videos_status_next_poll", "idx_videos_channel_id",
    "idx_view_timeseries_video_scraped", "idx_view_timeseries_scraped_at",
    "idx_users_email", "idx_predictions_user_created",
    "idx_notifications_user_created", "idx_notifications_user_unread",
}

EXPECTED_CONSTRAINTS = {
    "chk_total_views_positive", "chk_subscriber_count_positive", "chk_video_count_positive",
    "fk_videos_channel", "chk_videos_status", "chk_videos_current_interval_hours",
    "fk_view_timeseries_video", "chk_view_timeseries_view_count",
    "chk_view_timeseries_like_count", "chk_view_timeseries_comment_count",
    "chk_users_subscribers_positive", "chk_users_monthly_views_positive",
    "fk_predictions_user", "chk_predictions_status", "fk_notifications_user",
    "fk_video_features_video", "chk_notifications_type",
}

VIEW_COLUMNS = {
    "channel_id", "channel_title", "channel_description", "published_at", "country",
    "total_views", "subscriber_count", "video_count", "processed_at", "created_at",
    "avg_views_per_video", "views_per_subscriber", "engagement_ratio",
    "size_tier", "channel_age_days", "tier_category",
}

APP_TABLES = list(EXPECTED_COLUMNS)
ARCHIVE_TABLES = ["channel_stats_archive", "videos_archive", "view_timeseries_archive"]


# ---------------------------------------------------------------------------
# A1. Fresh build
# ---------------------------------------------------------------------------

def test_init_scripts_are_discovered():
    assert [p.name[:2] for p in INIT_SCRIPTS] == ["01", "02", "03", "04"]


def test_fresh_build_applies_scripts_in_order(create_database):
    conn = create_database(from_template=False)
    for script in INIT_SCRIPTS:
        apply_sql(conn, script)
    with conn.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        tables = {r[0] for r in cur.fetchall()}
    assert set(APP_TABLES) | set(ARCHIVE_TABLES) <= tables


# ---------------------------------------------------------------------------
# A3. Schema matches CLAUDE.md
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


def test_enriched_view_exposes_documented_columns(cur):
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'channel_stats_enriched'
        """
    )
    assert {r[0] for r in cur.fetchall()} == VIEW_COLUMNS


def test_video_features_has_row_level_security(cur):
    cur.execute("SELECT relrowsecurity FROM pg_class WHERE relname = 'video_features'")
    assert cur.fetchone()[0] is True


# ---------------------------------------------------------------------------
# A2. Idempotency
# ---------------------------------------------------------------------------

def _seed_all_tables(cur):
    h.channel(cur, title="Test Channel", total_views=10, subscriber_count=5, video_count=1)
    h.video(cur, category_id="10", title="A")
    h.timeseries(cur, view_count=100)
    uid = h.user(cur)
    h.prediction(cur, user_id=uid)
    h.notification(cur, user_id=uid)


DATA_TABLES = ["channel_stats", "videos", "view_timeseries", "users", "predictions", "notifications"]

RERUN_SCRIPTS = [pytest.param(p, id=p.name) for p in [*INIT_SCRIPTS, MIGRATION_002, MIGRATION_003, MIGRATION_004]]


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
# A4. Init scripts vs migration 002
# ---------------------------------------------------------------------------

def test_migration_002_brings_legacy_db_to_current_schema(conn, cur, create_database):
    """A DB created before video metadata existed, plus 002, equals a fresh build."""
    legacy = create_database()
    with legacy.cursor() as lcur:
        lcur.execute("DROP VIEW channel_stats_enriched")
        lcur.execute(
            "ALTER TABLE videos DROP COLUMN title, DROP COLUMN description, "
            "DROP COLUMN thumbnail_url, DROP COLUMN tags, DROP COLUMN category_id, "
            "DROP COLUMN duration"
        )
    legacy.commit()
    apply_sql(legacy, MIGRATION_002)

    with legacy.cursor() as lcur:
        legacy_fp = h.schema_fingerprint(lcur)
    fresh_fp = h.schema_fingerprint(cur)
    assert legacy_fp == fresh_fp


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
# A5. Archive tables mirror the core tables
# ---------------------------------------------------------------------------

ARCHIVE_ONLY = {"archive_id", "archived_at"}

ARCHIVE_PARITY = [
    pytest.param("channel_stats", "channel_stats_archive", {}, id="channel_stats"),
    pytest.param(
        "videos", "videos_archive", {},
        id="videos",
        marks=pytest.mark.xfail(
            strict=True,
            reason="videos_archive lags videos: current_interval_hours is INTEGER (not NUMERIC(5,2)) "
                   "and title/description/thumbnail_url/tags/category_id/duration are missing",
        ),
    ),
    pytest.param("view_timeseries", "view_timeseries_archive", {"id": "original_id"}, id="view_timeseries"),
]


@pytest.mark.parametrize("base, archive, renames", ARCHIVE_PARITY)
def test_archive_mirrors_core_table(cur, base, archive, renames):
    base_cols = {renames.get(c, c): t for c, (t, _) in h.columns(cur, base).items()}
    archive_cols = {c: t for c, (t, _) in h.columns(cur, archive).items() if c not in ARCHIVE_ONLY}
    assert archive_cols == base_cols


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
    """A database created before RLS was enabled, plus 004, equals a fresh build."""
    legacy = create_database()
    with legacy.cursor() as lcur:
        lcur.execute("SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'")
        for (table,) in lcur.fetchall():
            lcur.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
    legacy.commit()
    with legacy.cursor() as lcur:
        assert h.schema_fingerprint(lcur) != h.schema_fingerprint(cur)  # the damage is visible
    apply_sql(legacy, MIGRATION_004)
    with legacy.cursor() as lcur:
        assert h.schema_fingerprint(lcur) == h.schema_fingerprint(cur)


def test_row_level_security_leaves_the_owner_unaffected(cur):
    """The backend connects as the owner; RLS with no policies must not hide rows from it."""
    h.channel(cur, "UC1")
    cur.execute("SELECT COUNT(*) FROM channel_stats")
    assert cur.fetchone() == (1,)
