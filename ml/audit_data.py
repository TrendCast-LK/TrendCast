"""Read-only data audit for the 7-day view-trajectory model.

Connects to the same Supabase/Postgres database as backend/ (SUPABASE_DB_URL),
opens the session read-only, and prints a coverage / data-quality / usable-set
summary to stdout. Makes no writes.

Usage:
    python ml/audit_data.py
"""

from __future__ import annotations

import pandas as pd

from common import (
    FIRST_OBS_MAX_HOURS,
    GAP_THRESHOLD_HOURS,
    MAIN_DROP_PCT_THRESHOLD,
    USABLE_SPAN_DAYS,
    AuditResult,
    compute_audit,
    fetch_timeseries_df,
    fetch_videos_df,
    get_connection,
)


def _fmt_dist(series: pd.Series, unit: str = "") -> str:
    series = series.dropna()
    if series.empty:
        return "n/a (no qualifying videos)"
    suffix = f" {unit}" if unit else ""
    return (
        f"min={series.min():.1f}{suffix}  "
        f"median={series.median():.1f}{suffix}  "
        f"max={series.max():.1f}{suffix}"
    )


def print_report(audit: AuditResult) -> None:
    line = "=" * 78

    print(line)
    print("TRENDCAST ML DATA AUDIT")
    print(line)

    print("\n--- Coverage analysis ---------------------------------------------------")
    print(f"Total videos in `videos`:            {audit.total_videos}")
    print(f"Total rows in `view_timeseries`:      {audit.total_ts_rows}")
    print()
    print(f"Videos with span >= 3 days:           {len(audit.span_ge_3d_ids)}")
    print(f"Videos with span >= 5 days:           {len(audit.span_ge_5d_ids)}")
    print(f"Videos with span >= 7 days:           {len(audit.span_ge_7d_ids)}")
    print()
    print("Observation-count distribution for span >= 7 day videos "
          "(rows in view_timeseries per video):")
    print(f"  {_fmt_dist(audit.obs_count_ge_7d, 'rows')}")
    print()
    n_channels = len(audit.channels_of_ge_7d)
    print(f"Distinct channels among span >= 7 day videos: {n_channels}")
    if n_channels:
        print(f"  videos-per-channel distribution: {_fmt_dist(audit.channels_of_ge_7d, 'videos')}")

    print("\n--- Data quality checks ---------------------------------------------------")
    print(
        "Videos with a view_timeseries row where scraped_at < published_at "
        f"(broken published_at, must exclude): {len(audit.negative_elapsed_ids)}"
    )
    print(
        f"Videos with title IS NULL or thumbnail_url IS NULL: {len(audit.missing_meta_ids)}"
    )
    print(
        "Videos with ANY view_count decrease (informational only - no longer "
        f"used for exclusion, see repair-and-threshold below): {len(audit.non_monotonic_ids)}"
    )
    print()
    print(
        "Largest observation gap within the first 7 days, for span >= 7 day videos "
        "(hours; includes the gap from publish time to the first observation):"
    )
    print(f"  {_fmt_dist(audit.first_7d_max_gap_hours, 'h')}")

    print("\n--- Monotonicity: repair-and-threshold ------------------------------------")
    print(
        "View counts are repaired with a running maximum rather than dropped whenever "
        "they dip (YouTube legitimately revises counts down when filtering spam)."
    )
    print(
        f"Videos excluded at the {MAIN_DROP_PCT_THRESHOLD:.0f}% max-single-drop threshold: "
        f"{len(audit.drop_exceed_ids)}"
    )
    print("Usable-count sensitivity to the drop threshold (all other filters held fixed):")
    for t in sorted(audit.drop_threshold_sensitivity):
        marker = "  <- used" if t == MAIN_DROP_PCT_THRESHOLD else ""
        print(f"  threshold={t:>4.0f}%  usable={audit.drop_threshold_sensitivity[t]}{marker}")

    print("\n--- Density filter (first-7-day gap) ---------------------------------------")
    print(
        f"Videos excluded for a first-7-day gap > {GAP_THRESHOLD_HOURS:.0f}h "
        f"(after span/negative-elapsed/missing-meta/monotonic filters): {len(audit.gap_exceed_ids)}"
    )

    print("\n--- First-observation lag filter --------------------------------------------")
    print(
        "A video can pass the span >= 7d filter while having zero coverage near publish "
        "(e.g. a pre-existing video only picked up once its channel was added to tracking).\n"
        f"Videos excluded for a first observation > {FIRST_OBS_MAX_HOURS:.0f}h after publish "
        f"(after span/negative-elapsed/missing-meta/monotonic/density filters): "
        f"{len(audit.first_obs_exceed_ids)}"
    )
    print("Usable-count sensitivity to the first-observation threshold (all other filters held fixed):")
    for t in sorted(audit.first_obs_threshold_sensitivity):
        marker = "  <- used" if t == FIRST_OBS_MAX_HOURS else ""
        print(f"  threshold={t:>5.0f}h  usable={audit.first_obs_threshold_sensitivity[t]}{marker}")
    print(
        "(see ml/sweep_first_obs.py for the fuller sweep: fit quality and channel "
        "diversity at each threshold, not just the count)"
    )

    print("\n--- Filter pipeline (stepwise removal, in applied order) -------------------")
    remaining = len(audit.span_ge_7d_ids)
    print(f"  start (span >= {USABLE_SPAN_DAYS}d):                 {remaining}")
    for label, removed in audit.pipeline_removed.items():
        remaining -= removed
        print(f"  - {label:<32} -{removed:<6} -> {remaining}")

    print("\n--- Final usable training set ----------------------------------------------")
    print(
        f"Videos meeting ALL of: span >= {USABLE_SPAN_DAYS}d, no negative-elapsed rows, "
        f"non-null title & thumbnail_url, max single view-count drop <= {MAIN_DROP_PCT_THRESHOLD:.0f}%, "
        f"first-7-day gap <= {GAP_THRESHOLD_HOURS:.0f}h, first observation <= {FIRST_OBS_MAX_HOURS:.0f}h "
        "after publish:"
    )
    print(f"\n  >>> USABLE TRAINING SET SIZE: {len(audit.usable_ids)} videos <<<\n")

    print("--- Channel distribution of the usable set (for channel-based train/test split) ---")
    n_usable_channels = len(audit.usable_channel_counts)
    print(f"Distinct channels: {n_usable_channels}")
    if n_usable_channels:
        print(f"  videos-per-channel distribution: {_fmt_dist(audit.usable_channel_counts, 'videos')}")
        top_count = audit.usable_channel_counts.max()
        top_share = top_count / len(audit.usable_ids) * 100.0
        print(
            f"  largest single channel: {top_count} videos "
            f"({top_share:.1f}% of the usable set)"
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
    print_report(audit)


if __name__ == "__main__":
    main()
