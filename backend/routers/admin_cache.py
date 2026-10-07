"""Admin view of the forecast's channel history cache (channel_cache.py):
which channels are warm, stale or never warmed, and controls to re-warm or
purge them. Mounted under /admin by routers/admin.py.

The listed set is every channel with a cache row plus every channel a user
links to, so a channel whose warm never produced a row still shows up.
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

import channel_cache
from db import get_cursor
from inference import get_state
from models import AdminCacheList, AdminWarmResult
from routers.admin_common import like_pattern, record_action
from security import get_current_admin

router = APIRouter(prefix="/cache")

# Warming downloads and encodes up to max_hist thumbnails per channel, so a
# bulk re-warm is capped per call.
MAX_BULK_WARM = 25

BASE_CTE = """
    linked AS (
        SELECT channel_data->>'channel_id' AS channel_id,
               COUNT(*) AS linked_users,
               MAX(channel_data->>'title') AS channel_title
        FROM users
        WHERE channel_data->>'channel_id' IS NOT NULL
        GROUP BY 1
    ),
    vids AS (
        SELECT channel_id, COUNT(*) AS video_count, MAX(published_at) AS newest_video_at
        FROM channel_history_videos
        GROUP BY channel_id
    ),
    base AS (
        SELECT
            COALESCE(c.channel_id, l.channel_id) AS channel_id,
            l.channel_title,
            COALESCE(l.linked_users, 0) AS linked_users,
            c.channel_id IS NOT NULL AS has_entry,
            c.encoder,
            CASE WHEN %(encoder)s::text IS NULL OR c.channel_id IS NULL THEN NULL
                 ELSE c.encoder IS NOT DISTINCT FROM %(encoder)s::text END AS encoder_matches,
            c.warmed_at,
            c.last_error,
            c.updated_at,
            COALESCE(v.video_count, 0) AS video_count,
            v.newest_video_at,
            CASE
                WHEN c.warmed_at IS NULL THEN 'never'
                WHEN c.warmed_at < NOW() - make_interval(secs => %(ttl_seconds)s)
                     OR (%(encoder)s::text IS NOT NULL AND c.encoder IS DISTINCT FROM %(encoder)s::text) THEN 'stale'
                ELSE 'fresh'
            END AS status
        FROM channel_history_cache c
        FULL OUTER JOIN linked l ON l.channel_id = c.channel_id
        LEFT JOIN vids v ON v.channel_id = COALESCE(c.channel_id, l.channel_id)
    )
"""

LIST_SQL = "WITH " + BASE_CTE + """,
    filtered AS (
        SELECT * FROM base
        WHERE (%(pattern)s::text IS NULL OR channel_id ILIKE %(pattern)s OR channel_title ILIKE %(pattern)s)
          AND (%(status)s::text IS NULL
               OR (%(status)s::text = 'error' AND last_error IS NOT NULL)
               OR status = %(status)s::text)
    )
    SELECT
        (SELECT COUNT(*) FROM filtered),
        COALESCE((SELECT json_agg(page) FROM (
            SELECT * FROM filtered
            ORDER BY (last_error IS NOT NULL) DESC,
                     CASE status WHEN 'never' THEN 0 WHEN 'stale' THEN 1 ELSE 2 END,
                     linked_users DESC, channel_id
            LIMIT %(limit)s OFFSET %(offset)s
        ) page), '[]'::json),
        (SELECT json_build_object(
            'total', COUNT(*),
            'fresh', COUNT(*) FILTER (WHERE status = 'fresh'),
            'stale', COUNT(*) FILTER (WHERE status = 'stale'),
            'never', COUNT(*) FILTER (WHERE status = 'never'),
            'with_error', COUNT(*) FILTER (WHERE last_error IS NOT NULL),
            'hits_7d', (SELECT COUNT(*) FROM predictions
                        WHERE status = 'complete' AND used_channel_context IS TRUE
                          AND created_at >= GREATEST(NOW() - INTERVAL '7 days',
                                                     COALESCE(%(since)s::timestamptz, '-infinity'))),
            'forecasts_7d', (SELECT COUNT(*) FROM predictions
                             WHERE status = 'complete' AND used_channel_context IS NOT NULL
                               AND created_at >= GREATEST(NOW() - INTERVAL '7 days',
                                                          COALESCE(%(since)s::timestamptz, '-infinity')))
        ) FROM base)
"""


def _state_params() -> tuple[bool, str | None]:
    state = get_state()
    ready = bool(getattr(state, "ready", False))
    return ready, getattr(state, "encoder_signature", None) if ready else None


@router.get("", response_model=AdminCacheList)
def list_cache(
    q: str | None = None,
    status: Literal["all", "fresh", "stale", "never", "error"] = "all",
    ensemble_since: datetime | None = None,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    admin: dict = Depends(get_current_admin),
):
    ready, encoder = _state_params()
    params = {
        "pattern": like_pattern(q),
        "status": None if status == "all" else status,
        "encoder": encoder,
        "ttl_seconds": channel_cache.CACHE_TTL.total_seconds(),
        "since": ensemble_since,
        "limit": limit,
        "offset": offset,
    }
    with get_cursor() as cur:
        cur.execute(LIST_SQL, params)
        total, items, summary = cur.fetchone()
    return AdminCacheList(
        total=total, items=items, summary=summary, model_ready=ready,
        ttl_hours=channel_cache.CACHE_TTL.total_seconds() / 3600,
    )


def _require_model() -> None:
    if not _state_params()[0]:
        raise HTTPException(status_code=503, detail="The forecast model isn't loaded yet, so channels can't be encoded")


@router.post("/warm-stale", response_model=AdminWarmResult, status_code=202)
def warm_stale(background_tasks: BackgroundTasks, admin: dict = Depends(get_current_admin)):
    """Queues a warm for up to MAX_BULK_WARM channels that are never-warmed,
    stale or failing, most-linked first."""
    _require_model()
    _, encoder = _state_params()
    sql = "WITH " + BASE_CTE + """
        SELECT channel_id FROM base
        WHERE status <> 'fresh' OR last_error IS NOT NULL
        ORDER BY linked_users DESC, channel_id
        LIMIT %(limit)s
    """
    with get_cursor(commit=True) as cur:
        cur.execute(sql, {"encoder": encoder, "ttl_seconds": channel_cache.CACHE_TTL.total_seconds(),
                          "limit": MAX_BULK_WARM})
        channel_ids = [r[0] for r in cur.fetchall()]
        if channel_ids:
            record_action(cur, admin, "cache.warm_stale", target_type="channel",
                          details={"count": len(channel_ids), "channel_ids": channel_ids})

    for channel_id in channel_ids:
        channel_cache.schedule_warm(background_tasks, channel_id)
    return AdminWarmResult(queued=len(channel_ids), channel_ids=channel_ids)


KNOWN_CHANNEL_SQL = """
    SELECT 1 FROM channel_history_cache WHERE channel_id = %(channel_id)s
    UNION ALL
    SELECT 1 FROM users WHERE channel_data->>'channel_id' = %(channel_id)s
    LIMIT 1
"""


@router.post("/{channel_id}/warm",response_model=AdminWarmResult, status_code=202)
def warm_channel(channel_id: str, background_tasks: BackgroundTasks, admin: dict = Depends(get_current_admin)):
    _require_model()
    with get_cursor(commit=True) as cur:
        # Only channels the app knows about: each warm spends YouTube API quota.
        cur.execute(KNOWN_CHANNEL_SQL, {"channel_id": channel_id})
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="No user links that channel and it has no cache entry")
        record_action(cur, admin, "cache.warm", target_type="channel", details={"channel_id": channel_id})
    channel_cache.schedule_warm(background_tasks, channel_id)
    return AdminWarmResult(queued=1, channel_ids=[channel_id])


PURGE_SQL = """
    WITH purged AS (
        DELETE FROM channel_history_cache WHERE channel_id = %(channel_id)s RETURNING channel_id
    )
    SELECT COUNT(*) FROM purged
"""


@router.delete("/{channel_id}", status_code=204)
def purge_channel(channel_id: str, admin: dict = Depends(get_current_admin)):
    """Drops the cached history (videos cascade). Forecasts for the channel
    are CatBoost-only until it is warmed again."""
    with get_cursor(commit=True) as cur:
        cur.execute(PURGE_SQL, {"channel_id": channel_id})
        if cur.fetchone()[0] == 0:
            raise HTTPException(status_code=404, detail="No cache entry for that channel")
        record_action(cur, admin, "cache.purge", target_type="channel", details={"channel_id": channel_id})
