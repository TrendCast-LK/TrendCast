"""Shared read-only DB access and audit computation for the ml/ scripts.

Connects with the same SUPABASE_DB_URL env var used by backend/ and the ETL
jobs, loaded from backend/.env the same way
youtube-etl-pipeline/youtube_extractor/backfill_video_metadata.py does it.
All connections opened here are forced read-only at the session level.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv

BACKEND_ENV_PATH = Path(__file__).resolve().parents[1] / "backend" / ".env"
load_dotenv(BACKEND_ENV_PATH)

SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")

if not SUPABASE_DB_URL:
    raise RuntimeError(
        f"SUPABASE_DB_URL is not set (looked for it in the environment and in {BACKEND_ENV_PATH})"
    )

SPAN_THRESHOLDS_DAYS = (3, 5, 7)
USABLE_SPAN_DAYS = 7

# YouTube legitimately revises view counts downward when it filters spam/bot
# views, so a small dip is expected and shouldn't disqualify a video. Only
# drop videos whose worst single dip is large enough to look like a data
# problem rather than routine spam filtering; repair the rest with a running
# maximum so the series is monotonic by construction.
MAIN_DROP_PCT_THRESHOLD = 5.0
DROP_PCT_SENSITIVITY_THRESHOLDS = (1.0, 2.0, 5.0, 10.0)

# Videos whose first-7-day coverage has a hole bigger than this are too
# sparse in the window we care most about, even if their overall span is fine.
GAP_THRESHOLD_HOURS = 24.0

# "span >= 7 days" (latest_scraped - published_at) does NOT guarantee coverage
# starts near publish - a video can be polled for a week straight starting
# months after it was actually published (e.g. a pre-existing video picked up
# only once its channel was added to tracking), and still pass that filter.
# The median fitted tau (see ml/fit_curves.py diagnostics) is ~7 hours, so a
# video whose first real observation lands well past that - e.g. hour 20 - has
# already missed its entire rising phase. curve_fit will still converge
# against the flat tail and report a respectable R^2, but the recovered tau
# carries no real information about the video's actual growth rate. Since tau
# is one of our two prediction targets, including these videos would train
# the model on noise.
#
# A naive default would be ~6h (comfortably inside one tau). But the sweep in
# ml/sweep_first_obs.py shows 6h is unnecessarily costly: median R^2 and
# median tau are nearly flat across the whole 2-24h range (R^2 0.963 -> 0.936,
# tau 5.5h -> 7.3h as the threshold loosens from 2h to 24h), while going from
# 6h to 12h grows the usable set ~68% (1970 -> 3317 videos) and channel count
# from 45 to 53, for essentially no fit-quality cost. 12h -> 24h then adds
# almost nothing (+7% videos, same channel count, flat R^2) - 12h is the
# point past which loosening further stops paying for itself. Rerun the
# sweep if the underlying data changes enough to revisit this.
FIRST_OBS_MAX_HOURS = 12.0
FIRST_OBS_SWEEP_HOURS = (2.0, 4.0, 6.0, 12.0, 24.0)


def get_connection():
    """Open a psycopg2 connection pinned to read-only for the session."""
    conn = psycopg2.connect(SUPABASE_DB_URL)
    conn.set_session(readonly=True, autocommit=True)
    return conn


def fetch_videos_df(conn) -> pd.DataFrame:
    query = """
        SELECT video_id, channel_id, published_at, status, title,
               description, thumbnail_url, tags, category_id, duration
        FROM videos
    """
    return pd.read_sql(query, conn)


def fetch_timeseries_df(conn) -> pd.DataFrame:
    query = """
        SELECT video_id, scraped_at, view_count, like_count, comment_count
        FROM view_timeseries
    """
    return pd.read_sql(query, conn)


def fetch_channel_features_df(conn) -> pd.DataFrame:
    query = "SELECT * FROM channel_stats_enriched"
    return pd.read_sql(query, conn)


def compute_drop_stats(ts_df: pd.DataFrame) -> pd.Series:
    """Per-video largest single downward drop in view_count, as a percentage
    of the (higher) pre-drop value. 0.0 for videos with no decrease."""
    sorted_ts = ts_df.sort_values(["video_id", "scraped_at"])
    prev = sorted_ts.groupby("video_id", sort=False)["view_count"].shift(1)
    drop = prev - sorted_ts["view_count"]
    with np.errstate(divide="ignore", invalid="ignore"):
        drop_pct = np.where(prev > 0, drop / prev * 100.0, 0.0)
    drop_pct = pd.Series(drop_pct, index=sorted_ts.index).where(drop > 0, 0.0)
    return sorted_ts.assign(_drop_pct=drop_pct).groupby("video_id")["_drop_pct"].max()


def apply_monotonic_repair(ts_df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of ts_df with view_count replaced by its running maximum
    (np.maximum.accumulate) per video, ordered by scraped_at."""
    out = ts_df.sort_values(["video_id", "scraped_at"]).copy()
    out["view_count"] = out.groupby("video_id", sort=False)["view_count"].transform(
        lambda s: np.maximum.accumulate(s.to_numpy())
    )
    return out


@dataclass
class AuditResult:
    total_videos: int
    total_ts_rows: int

    span_hours: pd.Series  # video_id -> observation span in hours (NaN if no ts rows)
    span_ge_3d_ids: set
    span_ge_5d_ids: set
    span_ge_7d_ids: set

    obs_count_ge_7d: pd.Series  # video_id -> row count, restricted to span_ge_7d_ids
    channels_of_ge_7d: pd.Series  # channel_id -> video count, restricted to span_ge_7d_ids

    negative_elapsed_ids: set  # videos with any scraped_at < published_at
    missing_meta_ids: set  # videos with null title or thumbnail_url
    non_monotonic_ids: set  # informational: videos with ANY view_count decrease (no threshold)

    first_7d_max_gap_hours: pd.Series  # video_id -> largest gap in hours, within first 7 days, for span_ge_7d_ids

    drop_pct: pd.Series  # video_id -> largest single downward drop, as % of pre-drop value
    drop_exceed_ids: set  # videos excluded at MAIN_DROP_PCT_THRESHOLD
    drop_threshold_sensitivity: dict  # {threshold_pct: usable_count}

    gap_exceed_ids: set  # span>=7d videos whose first-7-day max gap exceeds GAP_THRESHOLD_HOURS

    first_obs_elapsed_hours: pd.Series  # video_id -> hours from published_at to earliest scraped_at
    first_obs_exceed_ids: set  # videos excluded at FIRST_OBS_MAX_HOURS
    first_obs_threshold_sensitivity: dict  # {threshold_hours: usable_count}

    pipeline_removed: dict  # stepwise removal counts, in filter-application order

    usable_ids: set
    usable_channel_counts: pd.Series  # channel_id -> usable video count, for the final usable set


def _usable_ids_for_threshold(
    span_ge_7d_ids: set,
    negative_elapsed_ids: set,
    missing_meta_ids: set,
    drop_pct: pd.Series,
    gap_exceed_ids: set,
    first_obs_elapsed_hours: pd.Series,
    drop_threshold_pct: float = MAIN_DROP_PCT_THRESHOLD,
    first_obs_threshold_hours: float = FIRST_OBS_MAX_HOURS,
) -> set:
    """Recompute the usable set with one or both thresholds swapped out,
    holding every other filter fixed - used for sensitivity sweeps."""
    drop_exceed = set(drop_pct[drop_pct > drop_threshold_pct].index)
    first_obs_exceed = set(
        first_obs_elapsed_hours[first_obs_elapsed_hours > first_obs_threshold_hours].index
    )
    return (
        span_ge_7d_ids
        - negative_elapsed_ids
        - missing_meta_ids
        - drop_exceed
        - gap_exceed_ids
        - first_obs_exceed
    )


def compute_audit(videos_df: pd.DataFrame, ts_df: pd.DataFrame) -> AuditResult:
    total_videos = len(videos_df)
    total_ts_rows = len(ts_df)

    videos_pub = videos_df.set_index("video_id")["published_at"]

    ts = ts_df.merge(
        videos_df[["video_id", "published_at", "title", "thumbnail_url"]],
        on="video_id",
        how="inner",
        suffixes=("", "_video"),
    )
    ts["elapsed_hours"] = (
        ts["scraped_at"] - ts["published_at"]
    ).dt.total_seconds() / 3600.0

    # --- Coverage: observation span per video ---------------------------------
    latest_scraped = ts.groupby("video_id")["scraped_at"].max()
    obs_count_all = ts.groupby("video_id").size()

    span_hours = (latest_scraped - videos_pub.reindex(latest_scraped.index)).dt.total_seconds() / 3600.0

    span_ge_3d_ids = set(span_hours[span_hours >= 3 * 24].index)
    span_ge_5d_ids = set(span_hours[span_hours >= 5 * 24].index)
    span_ge_7d_ids = set(span_hours[span_hours >= 7 * 24].index)

    obs_count_ge_7d = obs_count_all.reindex(sorted(span_ge_7d_ids))

    channels_of_ge_7d = (
        videos_df[videos_df["video_id"].isin(span_ge_7d_ids)]
        .groupby("channel_id")
        .size()
    )

    # --- Data quality -----------------------------------------------------------
    negative_elapsed_ids = set(ts.loc[ts["elapsed_hours"] < 0, "video_id"].unique())

    missing_meta_ids = set(
        videos_df.loc[
            videos_df["title"].isna() | videos_df["thumbnail_url"].isna(), "video_id"
        ]
    )

    drop_pct = compute_drop_stats(ts)
    non_monotonic_ids = set(drop_pct[drop_pct > 0].index)  # informational only

    # --- Gap analysis within the first 7 days, for the >=7d-span population ----
    first_7d = ts[(ts["video_id"].isin(span_ge_7d_ids)) & (ts["elapsed_hours"] <= 7 * 24)]

    def _max_gap_hours(group: pd.DataFrame) -> float:
        elapsed = sorted(group["elapsed_hours"].tolist() + [0.0])  # 0.0 = publish time
        diffs = [b - a for a, b in zip(elapsed, elapsed[1:])]
        return max(diffs) if diffs else float("nan")

    first_7d_max_gap_hours = first_7d.groupby("video_id").apply(
        _max_gap_hours, include_groups=False
    )
    first_7d_max_gap_hours = first_7d_max_gap_hours.reindex(sorted(span_ge_7d_ids))

    gap_exceed_ids = set(
        first_7d_max_gap_hours[first_7d_max_gap_hours > GAP_THRESHOLD_HOURS].index
    )

    # --- First-observation lag: hours from published_at to the earliest ------
    # --- real observation, regardless of the video's overall span. -----------
    first_obs_elapsed_hours = ts.groupby("video_id")["elapsed_hours"].min()

    # --- Final usable set, built up stepwise so each filter's removal count --
    # --- is reported in the order it's actually applied. -----------------------
    stage0 = span_ge_7d_ids
    stage1 = stage0 - negative_elapsed_ids
    stage2 = stage1 - missing_meta_ids
    drop_exceed_ids = set(drop_pct[drop_pct > MAIN_DROP_PCT_THRESHOLD].index)
    stage3 = stage2 - drop_exceed_ids
    stage4 = stage3 - gap_exceed_ids
    first_obs_exceed_ids = set(
        first_obs_elapsed_hours[first_obs_elapsed_hours > FIRST_OBS_MAX_HOURS].index
    )
    stage5 = stage4 - first_obs_exceed_ids

    usable_ids = stage5

    pipeline_removed = {
        "negative_elapsed": len(stage0) - len(stage1),
        "missing_meta": len(stage1) - len(stage2),
        f"monotonic_repair_gt_{MAIN_DROP_PCT_THRESHOLD:.0f}pct": len(stage2) - len(stage3),
        f"density_gap_gt_{GAP_THRESHOLD_HOURS:.0f}h": len(stage3) - len(stage4),
        f"first_obs_gt_{FIRST_OBS_MAX_HOURS:.0f}h": len(stage4) - len(stage5),
    }

    drop_threshold_sensitivity = {
        t: len(
            _usable_ids_for_threshold(
                span_ge_7d_ids,
                negative_elapsed_ids,
                missing_meta_ids,
                drop_pct,
                gap_exceed_ids,
                first_obs_elapsed_hours,
                drop_threshold_pct=t,
            )
        )
        for t in DROP_PCT_SENSITIVITY_THRESHOLDS
    }

    first_obs_threshold_sensitivity = {
        t: len(
            _usable_ids_for_threshold(
                span_ge_7d_ids,
                negative_elapsed_ids,
                missing_meta_ids,
                drop_pct,
                gap_exceed_ids,
                first_obs_elapsed_hours,
                first_obs_threshold_hours=t,
            )
        )
        for t in FIRST_OBS_SWEEP_HOURS
    }

    usable_channel_counts = (
        videos_df[videos_df["video_id"].isin(usable_ids)].groupby("channel_id").size()
    )

    return AuditResult(
        total_videos=total_videos,
        total_ts_rows=total_ts_rows,
        span_hours=span_hours,
        span_ge_3d_ids=span_ge_3d_ids,
        span_ge_5d_ids=span_ge_5d_ids,
        span_ge_7d_ids=span_ge_7d_ids,
        obs_count_ge_7d=obs_count_ge_7d,
        channels_of_ge_7d=channels_of_ge_7d,
        negative_elapsed_ids=negative_elapsed_ids,
        missing_meta_ids=missing_meta_ids,
        non_monotonic_ids=non_monotonic_ids,
        first_7d_max_gap_hours=first_7d_max_gap_hours,
        drop_pct=drop_pct,
        drop_exceed_ids=drop_exceed_ids,
        drop_threshold_sensitivity=drop_threshold_sensitivity,
        gap_exceed_ids=gap_exceed_ids,
        first_obs_elapsed_hours=first_obs_elapsed_hours,
        first_obs_exceed_ids=first_obs_exceed_ids,
        first_obs_threshold_sensitivity=first_obs_threshold_sensitivity,
        pipeline_removed=pipeline_removed,
        usable_ids=usable_ids,
        usable_channel_counts=usable_channel_counts,
    )
