"""Layer A6 / F: detecting drift between a database and the schema scripts.

The diff logic is tested against throwaway databases with known damage. The
last test applies the same diff to a real database (for example the live
Supabase one) when LIVE_DB_CHECK_URL is set; it only ever reads.

    LIVE_DB_CHECK_URL="$SUPABASE_DB_URL" python -m pytest tests/test_schema_diff.py -k live -s
"""

import os

import psycopg2
import pytest

import db_helpers as h


@pytest.fixture
def expected(cur):
    return h.schema_fingerprint(cur)


@pytest.fixture
def damaged(create_database):
    """Returns (connection, cursor) for a second copy of the schema to damage."""
    connection = create_database()
    return connection, connection.cursor()


def diff_against(expected, damaged):
    connection, cursor = damaged
    connection.commit()
    return h.diff_schema(expected, h.schema_fingerprint(cursor))


def test_identical_schemas_have_no_differences(expected, damaged):
    assert diff_against(expected, damaged) == []


def test_missing_constraint_is_reported(expected, damaged):
    # what a database that never received migration 003 looks like
    damaged[1].execute("ALTER TABLE notifications DROP CONSTRAINT chk_notifications_type")
    assert diff_against(expected, damaged) == ["missing constraint: notifications.chk_notifications_type"]


def test_unexpected_column_is_reported(expected, damaged):
    damaged[1].execute("ALTER TABLE users ADD COLUMN nickname TEXT")
    assert diff_against(expected, damaged) == ["unexpected column: users.nickname"]


def test_missing_column_is_reported(expected, damaged):
    damaged[1].execute("DROP VIEW channel_stats_enriched")
    damaged[1].execute("ALTER TABLE videos DROP COLUMN duration")
    problems = diff_against(expected, damaged)
    assert "missing column: videos.duration" in problems
    assert "missing view: channel_stats_enriched" in problems


def test_changed_column_type_is_reported(expected, damaged):
    damaged[1].execute("ALTER TABLE users ALTER COLUMN subscribers TYPE INTEGER")
    (problem,) = diff_against(expected, damaged)
    assert problem.startswith("changed column: users.subscribers")
    assert "bigint" in problem and "integer" in problem


def test_missing_index_is_reported(expected, damaged):
    damaged[1].execute("DROP INDEX idx_users_email")
    assert diff_against(expected, damaged) == ["missing index: users.idx_users_email"]


def test_changed_view_definition_is_reported(expected, damaged):
    damaged[1].execute("DROP VIEW channel_stats_enriched")
    damaged[1].execute("CREATE VIEW channel_stats_enriched AS SELECT channel_id FROM channel_stats")
    problems = diff_against(expected, damaged)
    assert problems and problems[0].startswith("changed view: channel_stats_enriched")


def test_changed_default_is_reported(expected, damaged):
    damaged[1].execute("ALTER TABLE videos ALTER COLUMN current_interval_hours SET DEFAULT 12")
    (problem,) = diff_against(expected, damaged)
    assert problem.startswith("changed column: videos.current_interval_hours")


def test_disabled_row_level_security_is_reported(expected, damaged):
    damaged[1].execute("ALTER TABLE users DISABLE ROW LEVEL SECURITY")
    (problem,) = diff_against(expected, damaged)
    assert problem.startswith("changed row-level security setting: users")


# The same constraint as Postgres writes it in a live database and in a restored copy of it.
IN_LIST_BUILT = ("CHECK (((status)::text = ANY ((ARRAY['draft'::character varying, "
                 "'complete'::character varying])::text[])))")
IN_LIST_RESTORED = ("CHECK (((status)::text = ANY (ARRAY[('draft'::character varying)::text, "
                    "('complete'::character varying)::text])))")


def test_restore_rewording_of_in_list_constraints_is_not_a_difference():
    assert h.normalize_constraint(IN_LIST_BUILT) == h.normalize_constraint(IN_LIST_RESTORED)


@pytest.mark.parametrize("changed", [
    IN_LIST_RESTORED.replace("'complete'", "'finished'"),
    IN_LIST_RESTORED.replace("('draft'::character varying)::text, ", ""),
    IN_LIST_RESTORED.replace("= ANY", "<> ALL"),
], ids=["different-value", "value-removed", "different-operator"])
def test_real_constraint_changes_survive_normalisation(changed):
    assert h.normalize_constraint(changed) != h.normalize_constraint(IN_LIST_BUILT)


def test_restored_constraint_wording_produces_no_diff():
    fp = {"columns": [], "indexes": [], "views": [], "rls": [],
          "constraints": [("predictions", "chk_predictions_status", IN_LIST_BUILT)]}
    restored = {**fp, "constraints": [("predictions", "chk_predictions_status", IN_LIST_RESTORED)]}
    assert h.diff_schema(fp, restored) == []


def test_several_problems_are_all_listed(expected, damaged):
    damaged[1].execute("ALTER TABLE notifications DROP CONSTRAINT chk_notifications_type")
    damaged[1].execute("DROP INDEX idx_users_email")
    damaged[1].execute("ALTER TABLE users ADD COLUMN nickname TEXT")
    assert len(diff_against(expected, damaged)) == 3


@pytest.mark.skipif(not os.environ.get("LIVE_DB_CHECK_URL"), reason="set LIVE_DB_CHECK_URL to check a real database")
def test_live_database_matches_the_schema_scripts(expected):
    """Read-only comparison of a real database against a fresh build of 01..04."""
    live = psycopg2.connect(os.environ["LIVE_DB_CHECK_URL"], options="-c default_transaction_read_only=on")
    live.set_session(readonly=True, autocommit=True)
    try:
        with live.cursor() as live_cur:
            problems = h.diff_schema(expected, h.schema_fingerprint(live_cur))
    finally:
        live.close()
    assert not problems, "live schema differs from the scripts:\n  " + "\n  ".join(problems)
