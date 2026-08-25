"""Sensitivity sweep for the first-observation-lag filter (common.FIRST_OBS_MAX_HOURS).

Answers: at each candidate threshold, how many videos would be usable, how
good are their curve fits (median R^2, median tau of converged fits), and
how concentrated is the surviving set across channels (train/test is split
by channel, so a threshold that collapses channel diversity is a real cost)?

This is a standalone diagnostic, not part of the regular extract -> fit ->
plot chain: it needs the database (via common.py) to see the broader
candidate population that thresholds looser than the FIRST_OBS_MAX_HOURS
default would draw from - population that never makes it into
ml/data/view_timeseries.csv once extract_dataset.py applies the single
default threshold. Read-only; writes only its own diagnostic CSV.

Usage:
    python ml/sweep_first_obs.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from common import (
    FIRST_OBS_MAX_HOURS,
    FIRST_OBS_SWEEP_HOURS,
    compute_audit,
    fetch_timeseries_df,
    fetch_videos_df,
    get_connection,
)
from fit_curves import GRID_HOURS, compute_elapsed_hours, fit_one_video, resample_to_grid

DATA_DIR = Path(__file__).resolve().parent / "data"
SWEEP_CSV = DATA_DIR / "first_obs_sweep_candidates.csv"


def fit_candidate_population(
    ts_df: pd.DataFrame, videos_df: pd.DataFrame, candidate_ids: set
) -> pd.DataFrame:
    """Fit every sweep-candidate video once. Per-threshold stats are then
    just a filter on first_obs_elapsed_hours over this single fitted table -
    thresholds are nested (a 2h-eligible video is also 4h/6h/12h/24h-eligible),
    so one fitting pass at the loosest threshold covers all of them."""
    ts = compute_elapsed_hours(
        ts_df[ts_df["video_id"].isin(candidate_ids)], videos_df
    ).sort_values(["video_id", "elapsed_hours"])

    records = []
    for video_id, group in ts.groupby("video_id", sort=False):
        elapsed = group["elapsed_hours"].to_numpy()
        values = group["view_count"].to_numpy(dtype=float)
        grid_values, _ = resample_to_grid(elapsed, values)
        valid_mask = ~np.isnan(grid_values)
        fit = fit_one_video(GRID_HOURS[valid_mask], grid_values[valid_mask])
        records.append(
            {"video_id": video_id, "first_obs_elapsed_hours": float(elapsed.min()), **fit}
        )

    return pd.DataFrame.from_records(records)


def print_sweep(fitted: pd.DataFrame, videos_df: pd.DataFrame) -> None:
    line = "=" * 96
    print(line)
    print("FIRST-OBSERVATION-LAG THRESHOLD SWEEP")
    print(line)
    print(
        f"{'threshold':>10} {'videos':>8} {'converged':>10} {'median R2':>10} "
        f"{'median tau(h)':>14} {'channels':>9} {'top ch %':>9}"
    )

    channel_of = videos_df.set_index("video_id")["channel_id"]

    for t in sorted(FIRST_OBS_SWEEP_HOURS):
        subset = fitted[fitted["first_obs_elapsed_hours"] <= t]
        conv = subset[subset["converged"]]
        n_videos = len(subset)
        n_converged = len(conv)
        median_r2 = conv["r2"].median() if n_converged else float("nan")
        median_tau = conv["tau"].median() if n_converged else float("nan")

        channel_counts = subset["video_id"].map(channel_of).value_counts()
        n_channels = len(channel_counts)
        top_share = (channel_counts.iloc[0] / n_videos * 100.0) if n_videos and n_channels else float("nan")

        marker = "  <- default" if t == FIRST_OBS_MAX_HOURS else ""
        print(
            f"{t:>9.0f}h {n_videos:>8} {n_converged:>10} {median_r2:>10.3f} "
            f"{median_tau:>14.1f} {n_channels:>9} {top_share:>8.1f}%{marker}"
        )

    print(line)


def main() -> None:
    conn = get_connection()
    try:
        videos_df = fetch_videos_df(conn)
        ts_df = fetch_timeseries_df(conn)
    finally:
        conn.close()

    audit = compute_audit(videos_df, ts_df)

    # Everything the usable-set pipeline requires EXCEPT the first-obs filter -
    # the broadest population any threshold in the sweep could draw candidates from.
    pre_first_obs_candidates = (
        audit.span_ge_7d_ids
        - audit.negative_elapsed_ids
        - audit.missing_meta_ids
        - audit.drop_exceed_ids
        - audit.gap_exceed_ids
    )

    max_sweep_hours = max(FIRST_OBS_SWEEP_HOURS)
    first_obs = audit.first_obs_elapsed_hours
    within_sweep_range = set(first_obs[first_obs <= max_sweep_hours].index)
    candidate_ids = pre_first_obs_candidates & within_sweep_range

    print(
        f"Fitting {len(candidate_ids)} candidate videos (first observation <= "
        f"{max_sweep_hours:.0f}h after publish, every other usable-set filter already applied)..."
    )

    fitted = fit_candidate_population(ts_df, videos_df, candidate_ids)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fitted.to_csv(SWEEP_CSV, index=False)
    print(f"Wrote {len(fitted)} rows to {SWEEP_CSV}\n")

    print_sweep(fitted, videos_df)


if __name__ == "__main__":
    main()
