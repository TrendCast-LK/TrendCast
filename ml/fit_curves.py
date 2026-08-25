"""Curve-fitting stage: fits a saturating exponential
V(t) = V_inf * (1 - exp(-t/tau)) to each usable video's monotonicity-repaired
view-count trajectory.

Reads ml/data/view_timeseries.csv and ml/data/videos.csv (produced by
extract_dataset.py). Writes ml/data/curve_params.csv. Purely local file I/O
- does not touch the database.

Usage:
    python ml/fit_curves.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit

DATA_DIR = Path(__file__).resolve().parent / "data"
VIDEOS_CSV = DATA_DIR / "videos.csv"
TIMESERIES_CSV = DATA_DIR / "view_timeseries.csv"
CURVE_PARAMS_CSV = DATA_DIR / "curve_params.csv"

# Regular grid: hourly for the first 48h, then every 6h out to 168h (7 days).
GRID_HOURS = np.concatenate([np.arange(0, 49, 1), np.arange(54, 169, 6)]).astype(float)
HORIZON_HOURS = 168.0
LARGE_GAP_HOURS = 6.0
MIN_POINTS_TO_FIT = 5
TAU_INIT_HOURS = 24.0
V168_GRID_MASK = GRID_HOURS == HORIZON_HOURS


def saturating_exponential(t, v_inf, tau):
    return v_inf * (1.0 - np.exp(-t / tau))


def compute_elapsed_hours(ts_df: pd.DataFrame, videos_df: pd.DataFrame) -> pd.DataFrame:
    """Attach published_at and elapsed_hours (scraped_at - published_at) to
    each view_timeseries row."""
    published = videos_df.set_index("video_id")["published_at"]
    out = ts_df.copy()
    out["scraped_at"] = pd.to_datetime(out["scraped_at"], utc=True)
    out["published_at"] = pd.to_datetime(out["video_id"].map(published), utc=True)
    out["elapsed_hours"] = (out["scraped_at"] - out["published_at"]).dt.total_seconds() / 3600.0
    return out


def resample_to_grid(elapsed: np.ndarray, values: np.ndarray):
    """Linearly interpolate (elapsed, values) onto GRID_HOURS.

    Grid points outside [elapsed.min(), elapsed.max()] are NaN - we never
    extrapolate, only interpolate between real observations. Also returns,
    for each grid point, the time gap between the two real observations it
    was interpolated between (0 at an exact match, NaN where out of range).
    """
    n = len(elapsed)
    if n == 0:
        nan_arr = np.full(GRID_HOURS.shape, np.nan)
        return nan_arr, nan_arr.copy()

    idx = np.searchsorted(elapsed, GRID_HOURS, side="left")
    left = np.clip(idx - 1, 0, n - 1)
    right = np.clip(idx, 0, n - 1)

    t0, t1 = elapsed[left], elapsed[right]
    v0, v1 = values[left], values[right]

    same = t1 == t0
    frac = np.where(same, 0.0, (GRID_HOURS - t0) / np.where(same, 1.0, t1 - t0))
    grid_values = v0 + frac * (v1 - v0)
    bracket_gap = t1 - t0

    out_of_range = (GRID_HOURS < elapsed[0]) | (GRID_HOURS > elapsed[-1])
    grid_values = np.where(out_of_range, np.nan, grid_values)
    bracket_gap = np.where(out_of_range, np.nan, bracket_gap)

    return grid_values, bracket_gap


def fit_one_video(t: np.ndarray, v: np.ndarray) -> dict:
    result = {
        "v_inf": np.nan,
        "tau": np.nan,
        "r2": np.nan,
        "rmse": np.nan,
        "converged": False,
        "n_points_fit": len(t),
        "fail_reason": None,
    }
    if len(t) < MIN_POINTS_TO_FIT:
        # Distinguish "genuinely sparse" from "no data at all fell inside the
        # 0-168h window" - the latter means the video's first real observation
        # came well after published_at (e.g. a pre-existing video only picked
        # up by polling long after it was actually published), not that the
        # data is merely thin.
        result["fail_reason"] = "no_coverage_in_window" if len(t) == 0 else "insufficient_points"
        return result

    v_inf0 = max(float(v[-1]), 1.0)
    p0 = [v_inf0, TAU_INIT_HOURS]
    bounds = ([1e-6, 1e-3], [np.inf, 1e5])

    try:
        popt, _ = curve_fit(
            saturating_exponential, t, v, p0=p0, bounds=bounds, max_nfev=10000
        )
    except (RuntimeError, ValueError) as exc:
        result["fail_reason"] = type(exc).__name__
        return result

    v_inf, tau = popt
    pred = saturating_exponential(t, v_inf, tau)
    residuals = v - pred
    ss_res = float(np.sum(residuals ** 2))
    ss_tot = float(np.sum((v - v.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan
    rmse = float(np.sqrt(np.mean(residuals ** 2)))

    result.update(
        {"v_inf": float(v_inf), "tau": float(tau), "r2": r2, "rmse": rmse, "converged": True}
    )
    return result


def process_all_videos(ts_df: pd.DataFrame, videos_df: pd.DataFrame) -> pd.DataFrame:
    """Steps 1+2 combined: resample each video onto the grid, fit the curve,
    and compute the data-quality / identifiability diagnostics for it."""
    ts = compute_elapsed_hours(ts_df, videos_df).sort_values(["video_id", "elapsed_hours"])

    records = []
    for video_id, group in ts.groupby("video_id", sort=False):
        elapsed = group["elapsed_hours"].to_numpy()
        values = group["view_count"].to_numpy(dtype=float)

        grid_values, bracket_gap = resample_to_grid(elapsed, values)
        valid_mask = ~np.isnan(grid_values)

        frac_interp_gap_gt6h = (
            float(np.mean(bracket_gap[valid_mask] > LARGE_GAP_HOURS)) if valid_mask.any() else np.nan
        )

        t = GRID_HOURS[valid_mask]
        v = grid_values[valid_mask]
        fit = fit_one_video(t, v)

        v168_valid = bool(valid_mask[V168_GRID_MASK][0])
        observed_v168 = float(grid_values[V168_GRID_MASK][0]) if v168_valid else np.nan

        vinf_ratio = (
            fit["v_inf"] / observed_v168
            if fit["converged"] and observed_v168 and observed_v168 > 0
            else np.nan
        )

        records.append(
            {
                "video_id": video_id,
                **fit,
                "observed_v168": observed_v168,
                "vinf_ratio": vinf_ratio,
                "frac_interp_gap_gt6h": frac_interp_gap_gt6h,
                "first_obs_elapsed_hours": float(elapsed.min()) if len(elapsed) else np.nan,
            }
        )

    return pd.DataFrame.from_records(records)


def _pct_dist(series: pd.Series) -> str:
    s = series.dropna()
    if s.empty:
        return "n/a"
    return (
        f"min={s.min():.3g}  p10={s.quantile(0.10):.3g}  median={s.median():.3g}  "
        f"p90={s.quantile(0.90):.3g}  max={s.max():.3g}"
    )


def print_report(params: pd.DataFrame) -> None:
    line = "=" * 78
    print(line)
    print("TRENDCAST CURVE FIT DIAGNOSTICS")
    print(line)

    n_total = len(params)
    n_converged = int(params["converged"].sum())
    n_failed = n_total - n_converged
    print(f"\nVideos fit: {n_total}  |  converged: {n_converged}  |  failed: {n_failed}")
    if n_failed:
        reasons = params.loc[~params["converged"], "fail_reason"].value_counts()
        for reason, count in reasons.items():
            print(f"  failed ({reason}): {count}")
        n_no_coverage = int(reasons.get("no_coverage_in_window", 0))
        if n_no_coverage:
            print(
                f"\n  NOTE: {n_no_coverage} videos have ZERO observations inside the 0-168h "
                "window - their first real poll came after hour 168, not merely sparsely\n"
                "  within it. This means the 'span >= 7 days' filter upstream (published_at "
                "to latest scraped_at) is not sufficient on its own: it does not\n"
                "  guarantee coverage starts near publish. See first_obs_elapsed_hours in "
                "curve_params.csv to identify/filter these."
            )

    fitted = params[params["converged"]]

    print("\n--- R^2 distribution -------------------------------------------------------")
    print(f"  {_pct_dist(fitted['r2'])}")
    for thresh in (0.90, 0.95, 0.99):
        n_above = int((fitted["r2"] > thresh).sum())
        print(f"  R^2 > {thresh:.2f}: {n_above} videos")

    print("\n--- Fitted V_inf distribution -----------------------------------------------")
    print(f"  {_pct_dist(fitted['v_inf'])}")

    print("\n--- Fitted tau distribution (hours) ------------------------------------------")
    print(f"  {_pct_dist(fitted['tau'])}")

    print("\n--- Identifiability check: V_inf / observed view count at 168h ---------------")
    print(
        "If a video hasn't visibly saturated within 7 days, curve_fit can trade V_inf\n"
        "against tau and produce an enormous, meaningless ceiling."
    )
    print(f"  {_pct_dist(fitted['vinf_ratio'])}")
    for thresh in (2, 5, 10):
        n_above = int((fitted["vinf_ratio"] > thresh).sum())
        print(f"  ratio > {thresh}: {n_above} videos")

    valid_corr = fitted[(fitted["v_inf"] > 0) & (fitted["tau"] > 0)]
    if len(valid_corr) >= 2:
        corr = float(np.corrcoef(np.log(valid_corr["v_inf"]), np.log(valid_corr["tau"]))[0, 1])
    else:
        corr = float("nan")
    print(f"\n  Pearson correlation between log(V_inf) and log(tau): {corr:.4f}")
    print(
        "  (strong positive correlation is the signature of the V_inf/tau trade-off above)"
    )

    print("\n--- Interpolation data quality -------------------------------------------------")
    print(
        f"Fraction of each video's valid grid points interpolated across a gap "
        f"> {LARGE_GAP_HOURS:.0f}h:"
    )
    print(f"  {_pct_dist(params['frac_interp_gap_gt6h'])}")

    print(line)


def main() -> None:
    videos_df = pd.read_csv(VIDEOS_CSV)
    ts_df = pd.read_csv(TIMESERIES_CSV)

    params = process_all_videos(ts_df, videos_df)

    params.to_csv(CURVE_PARAMS_CSV, index=False)
    print(f"Wrote {len(params)} rows to {CURVE_PARAMS_CSV}")

    print_report(params)


if __name__ == "__main__":
    main()
