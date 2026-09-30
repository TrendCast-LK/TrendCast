"""Channel history cache for the HistAttnV2 half of the forecast ensemble.

HistAttnV2 attends over a channel's recent uploads, each as raw CLIP-512 text
and image embeddings. Encoding them means downloading up to max_hist
thumbnails, which is too slow for a request, so warm_channel_history() does it
in the background at the points where the app already touches the user's
channel (signup and /channel/refresh, both through
routers.channel.refresh_user_channel). Predictions read it with load_history()
and fall back to CatBoost-only on a miss (inference._history_context).

A warm is incremental: videos already cached are not re-encoded, only their
view counts are refreshed. Tables: schema/init/05_channel_history_cache.sql.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import BackgroundTasks
from psycopg2.extras import execute_batch

import inference
from db import get_cursor

logger = logging.getLogger(__name__)

CACHE_TTL = timedelta(hours=24)

SELECT_HEADER_SQL = """
    SELECT encoder, warmed_at FROM channel_history_cache WHERE channel_id = %(channel_id)s
"""

SELECT_VIDEOS_SQL = """
    SELECT video_id, published_at, view_count, duration_s, text_embedding, image_embedding
    FROM channel_history_videos
    WHERE channel_id = %(channel_id)s
    ORDER BY published_at
"""

UPSERT_HEADER_SQL = """
    INSERT INTO channel_history_cache (channel_id, encoder, warmed_at, last_error, updated_at)
    VALUES (%(channel_id)s, %(encoder)s, %(warmed_at)s, NULL, NOW())
    ON CONFLICT (channel_id) DO UPDATE
    SET encoder = EXCLUDED.encoder, warmed_at = EXCLUDED.warmed_at,
        last_error = NULL, updated_at = NOW()
"""

RECORD_ERROR_SQL = """
    INSERT INTO channel_history_cache (channel_id, last_error, updated_at)
    VALUES (%(channel_id)s, %(error)s, NOW())
    ON CONFLICT (channel_id) DO UPDATE
    SET last_error = EXCLUDED.last_error, updated_at = NOW()
"""

# Drops uploads that fell out of the newest max_hist, and everything when the
# encoder changed (keep_ids is then only the freshly encoded set).
DELETE_OTHER_VIDEOS_SQL = """
    DELETE FROM channel_history_videos
    WHERE channel_id = %(channel_id)s AND NOT (video_id = ANY(%(keep_ids)s))
"""

UPSERT_VIDEO_SQL = """
    INSERT INTO channel_history_videos (
        channel_id, video_id, published_at, view_count, duration_s,
        text_embedding, image_embedding, encoded_at
    ) VALUES (
        %(channel_id)s, %(video_id)s, %(published_at)s, %(view_count)s, %(duration_s)s,
        %(text_embedding)s, %(image_embedding)s, NOW()
    )
    ON CONFLICT (channel_id, video_id) DO UPDATE
    SET published_at = EXCLUDED.published_at,
        view_count = EXCLUDED.view_count,
        duration_s = EXCLUDED.duration_s,
        text_embedding = EXCLUDED.text_embedding,
        image_embedding = COALESCE(EXCLUDED.image_embedding, channel_history_videos.image_embedding),
        encoded_at = NOW()
"""

# Already-encoded videos: only the metadata moves.
UPDATE_METADATA_SQL = """
    UPDATE channel_history_videos
    SET published_at = %(published_at)s, view_count = %(view_count)s, duration_s = %(duration_s)s
    WHERE channel_id = %(channel_id)s AND video_id = %(video_id)s
"""


@dataclass
class CachedHistory:
    videos: list[dict[str, Any]]
    warmed_at: datetime
    fresh: bool


_in_flight: set[str] = set()
_in_flight_lock = threading.Lock()


def load_history(channel_id: str) -> CachedHistory | None:
    """The cached history for a channel, or None when it has never been
    warmed successfully or was encoded with different encoders."""
    params = {"channel_id": channel_id}
    with get_cursor() as cur:
        cur.execute(SELECT_HEADER_SQL, params)
        header = cur.fetchone()
        if header is None:
            return None
        encoder, warmed_at = header
        if warmed_at is None or encoder != inference.get_state().encoder_signature:
            return None
        cur.execute(SELECT_VIDEOS_SQL, params)
        columns = [col.name for col in cur.description]
        videos = [dict(zip(columns, row)) for row in cur.fetchall()]

    fresh = datetime.now(timezone.utc) - warmed_at < CACHE_TTL
    return CachedHistory(videos=videos, warmed_at=warmed_at, fresh=fresh)


def schedule_warm(background_tasks: BackgroundTasks, channel_id: str) -> None:
    """Queues warm_channel_history to run after the response is sent."""
    background_tasks.add_task(warm_channel_history, channel_id)


def warm_channel_history(channel_id: str) -> None:
    """Fetches the channel's newest uploads, encodes the ones not cached yet
    and replaces the cached set. Runs as a background task, so it never
    raises: failures are logged and stored as last_error, and the cache keeps
    whatever it had."""
    with _in_flight_lock:
        if channel_id in _in_flight:
            logger.info("[channel_cache] warm already running for %s", channel_id)
            return
        _in_flight.add(channel_id)
    try:
        _warm(channel_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[channel_cache] warm failed for %s", channel_id)
        try:
            with get_cursor(commit=True) as cur:
                cur.execute(RECORD_ERROR_SQL, {"channel_id": channel_id, "error": f"{type(exc).__name__}: {exc}"})
        except Exception:  # noqa: BLE001
            logger.exception("[channel_cache] could not record the warm error for %s", channel_id)
    finally:
        with _in_flight_lock:
            _in_flight.discard(channel_id)


def _warm(channel_id: str) -> None:
    state = inference.get_state()
    if not getattr(state, "ready", False):
        logger.warning("[channel_cache] models not loaded; skipping warm for %s", channel_id)
        return

    start = time.perf_counter()
    uploads = inference._fetch_channel_history(channel_id, use_cache=False)[:state.max_hist]
    fetched = time.perf_counter()

    with get_cursor() as cur:
        cur.execute(SELECT_HEADER_SQL, {"channel_id": channel_id})
        header = cur.fetchone()
        encoder_matches = header is not None and header[0] == state.encoder_signature
        cur.execute(
            "SELECT video_id, image_embedding IS NULL FROM channel_history_videos "
            "WHERE channel_id = %(channel_id)s",
            {"channel_id": channel_id},
        )
        cached = dict(cur.fetchall()) if encoder_matches else {}

    # New uploads, plus cached ones whose thumbnail failed last time.
    to_encode = [v for v in uploads if cached.get(v["video_id"], True)]
    encoded = inference.encode_history_videos(state, to_encode)
    encoded_at = time.perf_counter()

    # Deterministic primary-key order, so concurrent warms of one channel
    # (from two app instances) lock rows in the same order.
    encoded_rows = sorted((_video_params(channel_id, v) for v in encoded), key=lambda r: r["video_id"])
    kept_rows = sorted(
        (_video_params(channel_id, v) for v in uploads if cached.get(v["video_id"]) is False),
        key=lambda r: r["video_id"],
    )

    with get_cursor(commit=True) as cur:
        cur.execute(UPSERT_HEADER_SQL, {
            "channel_id": channel_id,
            "encoder": state.encoder_signature,
            "warmed_at": datetime.now(timezone.utc),
        })
        cur.execute(DELETE_OTHER_VIDEOS_SQL, {
            "channel_id": channel_id, "keep_ids": [v["video_id"] for v in uploads],
        })
        execute_batch(cur, UPSERT_VIDEO_SQL, encoded_rows)
        execute_batch(cur, UPDATE_METADATA_SQL, kept_rows)

    logger.info(
        "channel_warm channel=%s videos=%d encoded=%d fetch_ms=%.1f encode_ms=%.1f write_ms=%.1f",
        channel_id, len(uploads), len(encoded),
        (fetched - start) * 1000, (encoded_at - fetched) * 1000, (time.perf_counter() - encoded_at) * 1000,
    )


def _video_params(channel_id: str, video: dict[str, Any]) -> dict[str, Any]:
    image = video.get("image_embedding")
    text = video.get("text_embedding")
    return {
        "channel_id": channel_id,
        "video_id": video["video_id"],
        "published_at": video["published_at"],
        "view_count": int(video["view_count"]),
        "duration_s": video.get("duration_s"),
        "text_embedding": [float(x) for x in text] if text is not None else None,
        "image_embedding": [float(x) for x in image] if image is not None else None,
    }
