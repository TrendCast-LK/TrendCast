"""
================================================================================
embed_new_videos.py — Standalone Script (GitHub Actions)
================================================================================
Schedule  : Every 12 hours (17 */12 * * *), after Job 1 — Channel Ingestion
Purpose   : Cache title/thumbnail embeddings for videos that don't have a
            video_features row yet, so weekly model retraining doesn't have
            to re-embed every video from scratch each run.

            Reuses the exact embedding functions the training/inference
            pipeline uses (ml/services/title_embedding.py,
            ml/services/thumbnail_embedding.py) rather than reimplementing
            them, so cached vectors are identical to what training would
            compute.

            Only ever looks at videos with no video_features row — it does
            not backfill existing videos any differently, so it naturally
            picks up new videos as Job 1 discovers them.

Environment Variables:
    SUPABASE_DB_URL    — PostgreSQL connection string

Usage:
    python youtube_extractor/embed_new_videos.py
    python youtube_extractor/embed_new_videos.py --limit 50   # small test run
================================================================================
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import psycopg2
import requests
import torch
from PIL import Image
from psycopg2.extras import execute_batch

# Ensure sibling modules are importable when run as a standalone script
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Make the repo-root `ml` package importable so we can reuse its embedding
# services (ml/services/title_embedding.py, ml/services/thumbnail_embedding.py)
# rather than duplicating them here.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from ml.services.title_embedding import embed_titles, load_model as load_title_model
from ml.services.thumbnail_embedding import embed_images, load_model as load_thumbnail_model

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)
log = logging.getLogger(__name__)

THUMBNAIL_DOWNLOAD_TIMEOUT_SECONDS = 10.0
THUMBNAIL_EMBED_BATCH_SIZE = 32  # images per CLIP forward pass


class ThumbnailDownloadError(Exception):
    """Raised when a video's thumbnail can't be downloaded or decoded."""


# ---------------------------------------------------------------------------
# SQL Templates
# ---------------------------------------------------------------------------
SELECT_UNEMBEDDED_VIDEOS_SQL = """
    SELECT v.video_id, v.title, v.thumbnail_url
    FROM videos v
    LEFT JOIN video_features vf ON vf.video_id = v.video_id
    WHERE vf.video_id IS NULL
    ORDER BY v.video_id
"""

INSERT_VIDEO_FEATURES_SQL = """
INSERT INTO video_features (
    video_id,
    title_embedding,
    thumbnail_embedding,
    computed_at
) VALUES (
    %(video_id)s,
    %(title_embedding)s::vector,
    %(thumbnail_embedding)s::vector,
    CURRENT_TIMESTAMP
)
ON CONFLICT (video_id) DO NOTHING;
"""


# ---------------------------------------------------------------------------
# Step 1: Find videos with no cached features yet
# ---------------------------------------------------------------------------
def get_unembedded_videos(conn, limit: Optional[int]) -> List[Dict[str, Any]]:
    query = SELECT_UNEMBEDDED_VIDEOS_SQL
    if limit is not None:
        query += " LIMIT %(limit)s"

    with conn.cursor() as cur:
        cur.execute(query, {"limit": limit} if limit is not None else None)
        rows = cur.fetchall()

    videos = [{"video_id": r[0], "title": r[1], "thumbnail_url": r[2]} for r in rows]
    log.info("Found %d videos without cached features", len(videos))
    return videos


# ---------------------------------------------------------------------------
# Step 2: Download thumbnails into memory
# ---------------------------------------------------------------------------
def download_thumbnail(url: str) -> Image.Image:
    """Download a thumbnail into memory and decode it. Raises
    ThumbnailDownloadError on any failure — never returns a partial image."""
    try:
        response = requests.get(url, timeout=THUMBNAIL_DOWNLOAD_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise ThumbnailDownloadError(f"could not reach thumbnail URL: {exc}") from exc

    if response.status_code != 200:
        raise ThumbnailDownloadError(f"thumbnail URL returned HTTP {response.status_code}")
    if not response.content:
        raise ThumbnailDownloadError("thumbnail URL returned an empty response")

    try:
        return Image.open(io.BytesIO(response.content)).convert("RGB")
    except Exception as exc:
        raise ThumbnailDownloadError(f"response was not a valid image: {exc}") from exc


def download_thumbnails(videos: List[Dict[str, Any]]) -> tuple[Dict[str, Image.Image], Dict[str, str]]:
    """Download every video's thumbnail, collecting failures instead of
    raising — one bad video (dead URL, missing thumbnail_url, non-image
    response) must not abort the whole batch."""
    images: Dict[str, Image.Image] = {}
    failures: Dict[str, str] = {}

    for video in videos:
        video_id = video["video_id"]
        url = video["thumbnail_url"]
        if not url:
            failures[video_id] = "no thumbnail_url"
            continue

        try:
            images[video_id] = download_thumbnail(url)
        except ThumbnailDownloadError as exc:
            failures[video_id] = str(exc)

    return images, failures


# ---------------------------------------------------------------------------
# Step 3: Embed titles + thumbnails (batched, reusing ml/services/*)
# ---------------------------------------------------------------------------
def vector_literal(values: np.ndarray) -> str:
    """Format a numpy vector as a pgvector input literal, e.g. '[0.1,0.2]'."""
    return "[" + ",".join(repr(float(x)) for x in values) + "]"


def get_missing_titles(videos: List[Dict[str, Any]]) -> Dict[str, str]:
    """video_ids whose title is null/empty — treated as a failure, not a
    zero vector, so they get retried next run instead of being permanently
    marked "done" with a meaningless embedding."""
    return {
        v["video_id"]: "missing/empty title"
        for v in videos
        if not (v["title"] or "").strip()
    }


def embed_video_titles(model, videos: List[Dict[str, Any]]) -> Dict[str, np.ndarray]:
    """Batch-embed titles. Callers must pre-filter out videos with a
    missing/empty title (see get_missing_titles) — every title here is
    encoded as-is."""
    video_ids = [v["video_id"] for v in videos]
    titles = [v["title"].strip() for v in videos]
    encoded = embed_titles(model, titles)
    return dict(zip(video_ids, encoded))


def embed_video_thumbnails(
    model, processor, images_by_id: Dict[str, Image.Image], device: str
) -> Dict[str, np.ndarray]:
    """Batch-embed thumbnails in chunks of THUMBNAIL_EMBED_BATCH_SIZE so a
    large backlog of new videos doesn't load every image into GPU/CPU memory
    at once."""
    video_ids = list(images_by_id.keys())
    embeddings: Dict[str, np.ndarray] = {}

    for i in range(0, len(video_ids), THUMBNAIL_EMBED_BATCH_SIZE):
        chunk_ids = video_ids[i : i + THUMBNAIL_EMBED_BATCH_SIZE]
        chunk_images = [images_by_id[vid] for vid in chunk_ids]
        chunk_embeddings = embed_images(model, processor, chunk_images, device)
        embeddings.update(zip(chunk_ids, chunk_embeddings))

    return embeddings


# ---------------------------------------------------------------------------
# Step 4: Insert cached features
# ---------------------------------------------------------------------------
def insert_video_features(conn, rows: List[Dict[str, Any]]) -> int:
    if not rows:
        return 0

    rows = sorted(rows, key=lambda r: r["video_id"])
    with conn.cursor() as cur:
        execute_batch(cur, INSERT_VIDEO_FEATURES_SQL, rows, page_size=200)
    conn.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=None, help="Only process the first N unembedded videos (for testing)"
    )
    args = parser.parse_args()

    db_url = os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        log.error("SUPABASE_DB_URL environment variable is not set")
        sys.exit(1)

    log.info("Job 3 — Embed New Videos starting")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info("Using device: %s", device)

    conn = psycopg2.connect(db_url)
    try:
        videos = get_unembedded_videos(conn, args.limit)
        if not videos:
            log.info("No videos need embedding — nothing to do")
            return

        images_by_id, download_failures = download_thumbnails(videos)
        for video_id, reason in download_failures.items():
            log.warning("Skipping %s — thumbnail download failed: %s", video_id, reason)

        title_failures = get_missing_titles(videos)
        for video_id in title_failures:
            log.warning("Skipping %s — missing/empty title", video_id)

        embeddable_videos = [
            v for v in videos if v["video_id"] in images_by_id and v["video_id"] not in title_failures
        ]
        embeddable_ids = {v["video_id"] for v in embeddable_videos}
        images_by_id = {vid: img for vid, img in images_by_id.items() if vid in embeddable_ids}

        log.info("Loading embedding models (LaBSE + CLIP)...")
        title_model = load_title_model(device)
        thumbnail_model, thumbnail_processor = load_thumbnail_model(device)

        title_embeddings = embed_video_titles(title_model, embeddable_videos)
        thumbnail_embeddings = embed_video_thumbnails(thumbnail_model, thumbnail_processor, images_by_id, device)

        rows = [
            {
                "video_id": video_id,
                "title_embedding": vector_literal(title_embeddings[video_id]),
                "thumbnail_embedding": vector_literal(thumbnail_embeddings[video_id]),
            }
            for video_id in embeddable_ids
        ]

        inserted = insert_video_features(conn, rows)

        skipped = len(videos) - len(embeddable_videos)
        log.info(
            "Job 3 complete — processed %d, inserted %d, skipped/failed %d",
            len(videos),
            inserted,
            skipped,
        )

    finally:
        conn.close()


if __name__ == "__main__":
    main()
