"""Plots a sample of fitted saturating-exponential curves against their
observed points, spanning the R^2 range (worst / median / best fits) so you
can see visually where the model form holds and where it breaks.

Reads ml/data/curve_params.csv, ml/data/view_timeseries.csv and
ml/data/videos.csv (all produced earlier in the ml/ pipeline). Writes
ml/data/fit_examples.png. Purely local file I/O - does not touch the database.

Usage:
    python ml/plot_fits.py
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from fit_curves import (
    CURVE_PARAMS_CSV,
    DATA_DIR,
    HORIZON_HOURS,
    TIMESERIES_CSV,
    VIDEOS_CSV,
    compute_elapsed_hours,
    saturating_exponential,
)

OUTPUT_PNG = DATA_DIR / "fit_examples.png"

N_WORST = 4
N_MEDIAN = 4
N_BEST = 4


def pick_examples(params: pd.DataFrame) -> pd.DataFrame:
    fitted = (
        params[params["converged"]]
        .dropna(subset=["r2"])
        .sort_values("r2")
        .reset_index(drop=True)
    )
    n = len(fitted)
    if n == 0:
        raise RuntimeError("No converged fits available to plot - run fit_curves.py first.")

    worst = fitted.iloc[: min(N_WORST, n)]
    best = fitted.iloc[max(0, n - N_BEST):]
    mid_start = max(0, n // 2 - N_MEDIAN // 2)
    median = fitted.iloc[mid_start: mid_start + N_MEDIAN]

    picked = pd.concat([worst, median, best]).drop_duplicates(subset="video_id")
    return picked.head(N_WORST + N_MEDIAN + N_BEST)


def main() -> None:
    params = pd.read_csv(CURVE_PARAMS_CSV)
    videos_df = pd.read_csv(VIDEOS_CSV)
    ts_df = pd.read_csv(TIMESERIES_CSV)

    ts = compute_elapsed_hours(ts_df, videos_df)
    examples = pick_examples(params)

    fig, axes = plt.subplots(3, 4, figsize=(22, 14))
    axes = axes.flatten()

    for ax, (_, row) in zip(axes, examples.iterrows()):
        video_id = row["video_id"]
        obs = ts[
            (ts["video_id"] == video_id)
            & (ts["elapsed_hours"] >= 0)
            & (ts["elapsed_hours"] <= HORIZON_HOURS)
        ].sort_values("elapsed_hours")

        ax.scatter(obs["elapsed_hours"], obs["view_count"], s=6, alpha=0.5, label="observed")

        t_fine = np.linspace(0, HORIZON_HOURS, 300)
        v_fit = saturating_exponential(t_fine, row["v_inf"], row["tau"])
        ax.plot(t_fine, v_fit, color="red", linewidth=1.5, label="fit")

        ax.set_title(
            f"{video_id[:14]}\n"
            f"R2={row['r2']:.3f}  tau={row['tau']:.1f}h  V_inf/obs168={row['vinf_ratio']:.2f}",
            fontsize=9,
        )
        ax.set_xlabel("hours since publish")
        ax.set_ylabel("view count")
        ax.legend(fontsize=7)

    for ax in axes[len(examples):]:
        ax.axis("off")

    fig.suptitle("Saturating-exponential fits: worst / median / best by R^2", fontsize=14)
    fig.tight_layout(rect=[0, 0, 1, 0.96])

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT_PNG, dpi=130)
    plt.close(fig)
    print(f"Wrote {OUTPUT_PNG}")


if __name__ == "__main__":
    main()
