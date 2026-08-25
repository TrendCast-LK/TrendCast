"""Thumbnail download stage: fetches each video's thumbnail image to disk.

Reads ml/data/features_simple.csv for the (video_id, thumbnail_url) list.
Downloads to ml/data/thumbnails/{video_id}.jpg. Purely local file I/O plus
HTTP GETs - no embeddings, no model loading, no feature computation.

Resumable: a video whose thumbnail file already exists on disk is skipped,
so a crashed or interrupted run can just be re-run. Failures (dead URLs,
non-image responses, etc.) are logged to ml/data/thumbnail_failures.csv
rather than aborting the run, so the next stage knows which videos to
exclude.

Usage:
    python ml/download_thumbnails.py
    python ml/download_thumbnails.py --limit 50
"""

from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

import pandas as pd
import requests
from PIL import Image

DATA_DIR = Path(__file__).resolve().parent / "data"
FEATURES_CSV = DATA_DIR / "features_simple.csv"
THUMBNAILS_DIR = DATA_DIR / "thumbnails"
FAILURES_CSV = DATA_DIR / "thumbnail_failures.csv"

REQUEST_TIMEOUT_SECONDS = 10
REQUEST_DELAY_SECONDS = 0.2
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2.0  # attempt N waits RETRY_BACKOFF_SECONDS * N before retrying
PROGRESS_EVERY = 100


def download_one(video_id: str, url: str, dest: Path) -> tuple[bool, str]:
    """Attempt to download and validate a single thumbnail, retrying on
    failure. Returns (success, reason) - reason is empty on success, else
    the last failure's description."""
    last_reason = "unknown error"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
            if response.status_code != 200:
                last_reason = f"HTTP {response.status_code}"
            elif not response.content:
                last_reason = "empty response body"
            else:
                dest.write_bytes(response.content)
                valid, reason = validate_image(dest)
                if valid:
                    return True, ""
                dest.unlink(missing_ok=True)
                last_reason = reason
        except requests.RequestException as exc:
            last_reason = f"request failed: {exc}"

        if attempt < MAX_ATTEMPTS:
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    return False, last_reason


def validate_image(path: Path) -> tuple[bool, str]:
    """Verify the saved file is a real, openable image and not empty or an
    error page saved with a .jpg extension."""
    if path.stat().st_size == 0:
        return False, "zero-byte file"
    try:
        with Image.open(path) as img:
            img.verify()
    except Exception as exc:
        return False, f"invalid image: {exc}"
    return True, ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=None, help="Only process the first N videos (for testing)"
    )
    args = parser.parse_args()

    features_df = pd.read_csv(FEATURES_CSV)
    videos = features_df[["video_id", "thumbnail_url"]].dropna(subset=["thumbnail_url"])
    if args.limit is not None:
        videos = videos.head(args.limit)

    THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)

    total_attempted = len(videos)
    downloaded = 0
    skipped = 0
    failures: list[tuple[str, str]] = []

    for i, row in enumerate(videos.itertuples(index=False), start=1):
        video_id, url = row.video_id, row.thumbnail_url
        dest = THUMBNAILS_DIR / f"{video_id}.jpg"

        if dest.exists():
            skipped += 1
        else:
            success, reason = download_one(video_id, url, dest)
            if success:
                downloaded += 1
            else:
                print(f"[download_thumbnails] FAILED {video_id}: {reason}")
                failures.append((video_id, reason))
            time.sleep(REQUEST_DELAY_SECONDS)

        if i % PROGRESS_EVERY == 0:
            print(f"[download_thumbnails] progress: {i}/{total_attempted}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with FAILURES_CSV.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["video_id", "reason"])
        writer.writerows(failures)

    print("=" * 78)
    print(f"Total attempted:          {total_attempted}")
    print(f"Downloaded this run:      {downloaded}")
    print(f"Skipped (already present): {skipped}")
    print(f"Failed:                   {len(failures)}")
    print(f"Failure record written to {FAILURES_CSV}")
    print("=" * 78)


if __name__ == "__main__":
    main()
