"""Read-only extraction of the usable training set into ml/data/*.csv.

Uses the same "usable video" definition as audit_data.py - the single source
of truth is common.compute_audit() - span >= 7 days, no negative-elapsed
rows, non-null title & thumbnail_url, max single view-count drop <=
MAIN_DROP_PCT_THRESHOLD, first-7-day gap <= GAP_THRESHOLD_HOURS, and first
observation <= FIRST_OBS_MAX_HOURS after publish (excludes videos whose
polling only started long after their real published_at, which would give a
curve fit a flat tail to converge on instead of an actual rising phase). And
writes two files:

  ml/data/videos.csv           one row per usable video, joined with
                                channel_stats_enriched features
  ml/data/view_timeseries.csv  long-format observations for those videos,
                                with view_count repaired to a running maximum
                                (see common.apply_monotonic_repair) instead
                                of the raw, possibly-dipping values

Usage:
    python ml/extract_dataset.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from common import (
    apply_monotonic_repair,
    compute_audit,
    fetch_channel_features_df,
    fetch_timeseries_df,
    fetch_videos_df,
    get_connection,
)

DATA_DIR = Path(__file__).resolve().parent / "data"
VIDEOS_OUT = DATA_DIR / "videos.csv"
TIMESERIES_OUT = DATA_DIR / "view_timeseries.csv"


def main() -> None:
    conn = get_connection()
    try:
        videos_df = fetch_videos_df(conn)
        ts_df = fetch_timeseries_df(conn)
        channel_features_df = fetch_channel_features_df(conn)
    finally:
        conn.close()

    audit = compute_audit(videos_df, ts_df)
    usable_ids = audit.usable_ids
    print(f"Usable videos: {len(usable_ids)} (of {audit.total_videos} total)")

    usable_videos = videos_df[videos_df["video_id"].isin(usable_ids)].copy()
    usable_ts = ts_df[ts_df["video_id"].isin(usable_ids)].copy()
    usable_ts = apply_monotonic_repair(usable_ts)  # view_count -> running maximum

    videos_out = usable_videos.merge(
        channel_features_df,
        on="channel_id",
        how="left",
        suffixes=("", "_channel"),
    )

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    videos_out.to_csv(VIDEOS_OUT, index=False)
    usable_ts.sort_values(["video_id", "scraped_at"]).to_csv(TIMESERIES_OUT, index=False)

    print(f"Wrote {len(videos_out)} rows to {VIDEOS_OUT}")
    print(f"Wrote {len(usable_ts)} rows to {TIMESERIES_OUT}")


if __name__ == "__main__":
    main()
