"""Row builders and schema introspection helpers for the database tests."""

from datetime import datetime, timezone

from psycopg2 import sql

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def insert(cur, table: str, ignore_conflict: bool = False, **values) -> None:
    cols = list(values)
    stmt = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, cols)),
        sql.SQL(", ").join(sql.Placeholder(c) for c in cols),
    )
    if ignore_conflict:
        stmt += sql.SQL(" ON CONFLICT DO NOTHING")
    cur.execute(stmt, values)


def channel(cur, channel_id="UC001", **over):
    values = {"channel_id": channel_id, "channel_title": "Test Channel", **over}
    insert(cur, "channel_stats", **values)
    return values["channel_id"]


def video(cur, video_id="vid001", channel_id="UC001", parent=True, **over):
    """Insert a video. parent=False skips creating the channel (for FK tests)."""
    if parent:
        insert(cur, "channel_stats", ignore_conflict=True,
               channel_id=channel_id, channel_title="Test Channel")
    values = {"video_id": video_id, "channel_id": channel_id, "published_at": NOW, **over}
    insert(cur, "videos", **values)
    return values["video_id"]


def timeseries(cur, video_id="vid001", parent=True, **over):
    if parent:
        cur.execute("SELECT 1 FROM videos WHERE video_id = %s", (video_id,))
        if cur.fetchone() is None:
            video(cur, video_id=video_id)
    insert(cur, "view_timeseries", video_id=video_id, **over)


def user(cur, email="user@example.com", **over):
    values = {"full_name": "Test User", "email": email, "password_hash": "x", **over}
    insert(cur, "users", **values)
    cur.execute("SELECT id FROM users WHERE email = %s", (values["email"],))
    return cur.fetchone()[0]


def prediction(cur, user_id=None, **over):
    if user_id is None:
        user_id = user(cur)
    insert(cur, "predictions", user_id=user_id, **{"title": "A video", **over})


def notification(cur, user_id=None, **over):
    if user_id is None:
        user_id = user(cur)
    values = {"type": "welcome", "title": "Hi", "message": "Welcome", **over}
    insert(cur, "notifications", user_id=user_id, **values)


def vector(dim: int, fill: float = 0.1) -> str:
    return "[" + ",".join([str(fill)] * dim) + "]"


def count(cur, table: str, where: str = "TRUE", params=()) -> int:
    cur.execute(sql.SQL("SELECT COUNT(*) FROM {} WHERE ").format(sql.Identifier(table)) + sql.SQL(where), params)
    return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# Schema introspection
# ---------------------------------------------------------------------------

def columns(cur, table: str) -> dict:
    """{column: (formatted type, not null)} for a table in the public schema."""
    cur.execute(
        """
        SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = %s AND c.relkind = 'r'
          AND a.attnum > 0 AND NOT a.attisdropped
        """,
        (table,),
    )
    return {name: (typ, notnull) for name, typ, notnull in cur.fetchall()}


def schema_fingerprint(cur) -> dict:
    """Everything structural about the public schema, for before/after diffs."""
    fp = {}
    cur.execute(
        """
        SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod),
               a.attnotnull, pg_get_expr(d.adbin, d.adrelid)
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE n.nspname = 'public' AND c.relkind = 'r' AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY 1, 2
        """
    )
    fp["columns"] = cur.fetchall()
    cur.execute("SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' ORDER BY 1, 2")
    fp["indexes"] = cur.fetchall()
    cur.execute(
        """
        SELECT c.relname, con.conname, pg_get_constraintdef(con.oid)
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' ORDER BY 1, 2
        """
    )
    fp["constraints"] = cur.fetchall()
    cur.execute("SELECT viewname, definition FROM pg_views WHERE schemaname = 'public' ORDER BY 1")
    fp["views"] = cur.fetchall()
    cur.execute(
        "SELECT relname, relrowsecurity FROM pg_class "
        "WHERE relnamespace = 'public'::regnamespace AND relkind = 'r' ORDER BY 1"
    )
    fp["rls"] = cur.fetchall()
    return fp


def snapshot_rows(cur, tables) -> dict:
    out = {}
    for table in tables:
        cur.execute(sql.SQL("SELECT * FROM {} ORDER BY 1").format(sql.Identifier(table)))
        out[table] = cur.fetchall()
    return out


def diff_schema(expected: dict, actual: dict) -> list:
    """Human-readable differences between two schema_fingerprint() results."""
    problems = []
    for label, key_len, section in (
        ("column", 2, "columns"), ("index", 2, "indexes"),
        ("constraint", 2, "constraints"), ("view", 1, "views"), ("row-level security setting", 1, "rls"),
    ):
        exp = {tuple(r[:key_len]): tuple(r[key_len:]) for r in expected[section]}
        act = {tuple(r[:key_len]): tuple(r[key_len:]) for r in actual[section]}
        for key in sorted(exp.keys() - act.keys()):
            problems.append(f"missing {label}: {'.'.join(key)}")
        for key in sorted(act.keys() - exp.keys()):
            problems.append(f"unexpected {label}: {'.'.join(key)}")
        for key in sorted(exp.keys() & act.keys()):
            if exp[key] != act[key]:
                problems.append(f"changed {label}: {'.'.join(key)}\n    expected {exp[key]}\n    actual   {act[key]}")
    return problems
