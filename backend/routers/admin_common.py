"""Helpers shared by the admin routers (admin.py, admin_predictions.py, admin_cache.py).

Every admin endpoint is written to need as few database round trips as
possible (one statement for a read, usually two for a write): against a
remote Supabase pooler each round trip can cost close to a second.
"""

from psycopg2.extras import Json

INSERT_AUDIT_SQL = """
    INSERT INTO admin_audit_log (admin_id, action, target_type, target_id, details)
    VALUES (%(admin_id)s, %(action)s, %(target_type)s, %(target_id)s, %(details)s)
"""

# Audit rows with the acting admin's email, for embedding in other queries.
AUDIT_ROWS_SQL = """
    SELECT l.id, l.admin_id, a.email AS admin_email, l.action, l.target_type, l.target_id,
           l.details, l.created_at
    FROM admin_audit_log l
    LEFT JOIN admins a ON a.id = l.admin_id
"""


def record_action(cur, admin: dict, action: str, *, target_type: str | None = None,
                  target_id: int | None = None, details: dict | None = None) -> None:
    """Writes an audit row on `cur`, so it commits (or rolls back) with the change.

    target_id is a BIGINT; for targets keyed by text (a YouTube channel id)
    pass target_id=None and put the key in details."""
    cur.execute(
        INSERT_AUDIT_SQL,
        {
            "admin_id": admin["id"],
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "details": Json(details or {}),
        },
    )


def like_pattern(q: str | None) -> str | None:
    """An ILIKE pattern matching q anywhere, with LIKE wildcards in q taken literally."""
    q = (q or "").strip()
    if not q:
        return None
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
