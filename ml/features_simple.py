"""Stage 1 feature engineering: simple numeric features only.

Reads ml/data/videos.csv and ml/data/curve_params.csv (both produced by
extract_dataset.py / fit_curves.py), joins them on video_id, and writes one
row per video to ml/data/features_simple.csv. No embeddings, no downloads,
no model loading, no database access - those are later stages.

Usage:
    python ml/features_simple.py
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent / "data"
VIDEOS_IN = DATA_DIR / "videos.csv"
CURVE_PARAMS_IN = DATA_DIR / "curve_params.csv"
FEATURES_OUT = DATA_DIR / "features_simple.csv"

SRI_LANKA_OFFSET = pd.Timedelta(hours=5, minutes=30)

CHANNEL_COLUMNS = [
    "subscriber_count",
    "total_views",
    "video_count",
    "avg_views_per_video",
    "views_per_subscriber",
    "engagement_ratio",
    "size_tier",
    "tier_category",
]

DURATION_RE = re.compile(
    r"^PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?$"
)


def parse_iso8601_duration(value) -> float:
    """Parse an ISO 8601 duration (e.g. 'PT4M13S') into total seconds.

    Returns NaN for missing/malformed values, including a bare 'PT' with no
    components, which carries no actual duration information.
    """
    if pd.isna(value):
        return np.nan
    match = DURATION_RE.match(str(value).strip())
    if not match or not any(match.groups()):
        return np.nan
    hours, minutes, seconds = match.groups()
    total = (
        int(hours or 0) * 3600
        + int(minutes or 0) * 60
        + float(seconds or 0)
    )
    return total


def count_tags(value) -> int:
    """Number of tags. 0 for null/empty; tags are stored as a stringified
    Python list (e.g. "['a', 'b']") in the CSV."""
    if pd.isna(value):
        return 0
    try:
        tags = ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return 0
    if not tags:
        return 0
    return len(tags)


def main() -> None:
    videos_df = pd.read_csv(VIDEOS_IN)
    curve_params_df = pd.read_csv(CURVE_PARAMS_IN)

    df = videos_df.merge(
        curve_params_df[["video_id", "v_inf", "tau"]],
        on="video_id",
        how="inner",
    )

    # --- Title -----------------------------------------------------------
    title = df["title"].fillna("")
    df["title_char_length"] = title.str.len()
    df["title_word_count"] = title.str.split().str.len()

    # --- Video metadata ----------------------------------------------------
    df["duration_seconds"] = df["duration"].apply(parse_iso8601_duration)
    n_malformed_duration = df["duration_seconds"].isna().sum()
    if n_malformed_duration:
        print(f"[features_simple] {n_malformed_duration} rows had missing/malformed `duration`; set to NaN")

    df["tag_count"] = df["tags"].apply(count_tags)
    # category_id kept as-is, to be encoded in a later stage

    # --- Upload timing (Sri Lanka time, UTC+5:30) ---------------------------
    published_at_utc = pd.to_datetime(df["published_at"], utc=True)
    published_at_slt = published_at_utc + SRI_LANKA_OFFSET
    df["upload_hour"] = published_at_slt.dt.hour
    df["upload_dayofweek"] = published_at_slt.dt.dayofweek
    df["is_weekend"] = df["upload_dayofweek"].isin([5, 6])

    # --- Assemble output -----------------------------------------------------
    output_columns = (
        ["video_id", "channel_id"]
        + ["title_char_length", "title_word_count"]
        + ["duration_seconds", "tag_count", "category_id"]
        + ["upload_hour", "upload_dayofweek", "is_weekend"]
        + CHANNEL_COLUMNS
        + ["v_inf", "tau", "thumbnail_url"]
    )
    features_df = df[output_columns]

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    features_df.to_csv(FEATURES_OUT, index=False)

    print_summary(features_df)
    print(f"\nWrote {len(features_df)} rows to {FEATURES_OUT}")


def print_summary(features_df: pd.DataFrame) -> None:
    line = "=" * 78
    print(line)
    print("FEATURES_SIMPLE SUMMARY")
    print(line)
    print(f"Rows: {len(features_df)}")
    print(f"Columns: {len(features_df.columns)}")

    print("\n--- Null counts per column ---------------------------------------------")
    null_counts = features_df.isna().sum()
    for col, n in null_counts.items():
        print(f"  {col:<28} {n}")

    print("\n--- Value ranges ---------------------------------------------------------")
    for col in ["duration_seconds", "tag_count", "upload_hour"]:
        series = features_df[col].dropna()
        if series.empty:
            print(f"  {col:<20} n/a (all null)")
        else:
            print(f"  {col:<20} min={series.min()}  max={series.max()}")
    print(line)


if __name__ == "__main__":
    main()
