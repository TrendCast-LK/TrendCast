"""Final feature assembly: joins simple features with the title/thumbnail
embeddings, reduces each embedding set with PCA, one-hot encodes categorical
columns, computes the two training targets, and writes the single training
table. No downloads, no model loading beyond what PCA/one-hot encoding need.

Before targets are computed, drops videos whose curve fit is unreliable:
tau pinned at (or near) the optimizer's upper bound, or R^2 below a minimum
threshold. See OUTLIER FILTER constants below. Genuine viral outliers (large
target_log_vinf_rel with a good R^2) are deliberately kept - predicting
outperformance is the point of the model, so those are exactly the videos
it most needs to learn from.

Reads:
  ml/data/features_simple.csv
  ml/data/curve_params.csv           (for r2, to screen out bad fits)
  ml/data/title_embeddings.npy       + ml/data/title_embedding_ids.csv
  ml/data/thumbnail_embeddings.npy   + ml/data/thumbnail_embedding_ids.csv

Writes:
  ml/data/features.csv               final training table
  ml/models/title_pca.joblib         fitted PCA (768 -> 40), for the backend
  ml/models/thumbnail_pca.joblib     fitted PCA (512 -> 40), for the backend
  ml/models/categorical_encoder.joblib   fitted OneHotEncoder (category_id, size_tier)
  ml/models/channel_medians.json     channel_id -> median fitted V_inf in this dataset

Usage:
    python ml/build_features.py
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.preprocessing import OneHotEncoder

DATA_DIR = Path(__file__).resolve().parent / "data"
MODELS_DIR = Path(__file__).resolve().parent / "models"

FEATURES_SIMPLE_CSV = DATA_DIR / "features_simple.csv"
CURVE_PARAMS_CSV = DATA_DIR / "curve_params.csv"
TITLE_EMBEDDINGS_NPY = DATA_DIR / "title_embeddings.npy"
TITLE_EMBEDDING_IDS_CSV = DATA_DIR / "title_embedding_ids.csv"
THUMBNAIL_EMBEDDINGS_NPY = DATA_DIR / "thumbnail_embeddings.npy"
THUMBNAIL_EMBEDDING_IDS_CSV = DATA_DIR / "thumbnail_embedding_ids.csv"
FEATURES_OUT = DATA_DIR / "features.csv"

TITLE_PCA_OUT = MODELS_DIR / "title_pca.joblib"
THUMBNAIL_PCA_OUT = MODELS_DIR / "thumbnail_pca.joblib"
ENCODER_OUT = MODELS_DIR / "categorical_encoder.joblib"
CHANNEL_MEDIANS_OUT = MODELS_DIR / "channel_medians.json"

N_PCA_COMPONENTS = 40
CATEGORICAL_COLUMNS = ["category_id", "size_tier"]

# --- Outlier filter -----------------------------------------------------------
# tau near/at the curve_fit optimizer's upper bound (see ml/fit_curves.py)
# means the video never visibly saturated within the 7-day window, so the
# fitted decay constant carries no real information.
TAU_MAX_HOURS = 10_000.0
# Below this R^2, the fitted curve doesn't actually describe the video's
# observed growth, so V_inf/tau from it aren't trustworthy either.
R2_MIN = 0.5

SIMPLE_FEATURE_COLUMNS = [
    "title_char_length",
    "title_word_count",
    "duration_seconds",
    "tag_count",
    "upload_hour",
    "upload_dayofweek",
    "is_weekend",
    "subscriber_count",
    "total_views",
    "video_count",
    "avg_views_per_video",
    "views_per_subscriber",
    "engagement_ratio",
    "tier_category",
]


def load_embedding_table(embeddings_path: Path, ids_path: Path, prefix: str) -> pd.DataFrame:
    """Load an (N, D) embedding array plus its (N,) video_id list into a
    DataFrame indexed by video_id - the two files store IDs separately from
    vectors, so this is what lets us align by ID rather than row position."""
    embeddings = np.load(embeddings_path)
    ids = pd.read_csv(ids_path)["video_id"]
    columns = [f"{prefix}_{i}" for i in range(embeddings.shape[1])]
    return pd.DataFrame(embeddings, columns=columns, index=ids).rename_axis("video_id")


def fit_pca(raw_embeddings: np.ndarray, prefix: str) -> tuple[PCA, pd.DataFrame]:
    pca = PCA(n_components=N_PCA_COMPONENTS, random_state=42)
    reduced = pca.fit_transform(raw_embeddings)
    columns = [f"{prefix}_pc_{i}" for i in range(N_PCA_COMPONENTS)]
    return pca, pd.DataFrame(reduced, columns=columns)


def main() -> None:
    features_df = pd.read_csv(FEATURES_SIMPLE_CSV)
    n_before_join = len(features_df)

    curve_params_df = pd.read_csv(CURVE_PARAMS_CSV, usecols=["video_id", "r2"])
    title_df = load_embedding_table(TITLE_EMBEDDINGS_NPY, TITLE_EMBEDDING_IDS_CSV, "title_raw")
    thumb_df = load_embedding_table(THUMBNAIL_EMBEDDINGS_NPY, THUMBNAIL_EMBEDDING_IDS_CSV, "thumb_raw")

    joined = (
        features_df.set_index("video_id")
        .join(curve_params_df.set_index("video_id"), how="inner")
        .join(title_df, how="inner")
        .join(thumb_df, how="inner")
        .reset_index()
    )
    n_dropped_join = n_before_join - len(joined)

    # --- Outlier filter, stepwise ---------------------------------------------
    removal_counts: dict[str, int] = {}

    stage_tau = joined[joined["tau"] <= TAU_MAX_HOURS]
    removal_counts[f"tau > {TAU_MAX_HOURS:.0f}h (unconverged)"] = len(joined) - len(stage_tau)

    stage_r2 = stage_tau[stage_tau["r2"].notna() & (stage_tau["r2"] >= R2_MIN)]
    removal_counts[f"r2 < {R2_MIN} or missing (poor fit)"] = len(stage_tau) - len(stage_r2)

    # Channel medians computed on the bad-fit-filtered set, not the raw join,
    # so a channel's median V_inf isn't dragged around by videos we already
    # know have unreliable fits. Genuine viral outliers stay in - the model
    # needs exactly those to learn to predict outperformance.
    channel_medians = stage_r2.groupby("channel_id")["v_inf"].median()
    channel_median_vinf = stage_r2["channel_id"].map(channel_medians)
    filtered = stage_r2.assign(
        target_log_vinf_rel=np.log(stage_r2["v_inf"] / channel_median_vinf),
        target_log_tau=np.log(stage_r2["tau"]),
    )

    vinf_rel_dist = filtered["target_log_vinf_rel"].describe()

    joined = filtered.reset_index(drop=True)

    # --- PCA, fit on the final filtered set's embeddings -----------------------
    title_raw_cols = list(title_df.columns)
    thumb_raw_cols = list(thumb_df.columns)

    title_pca, title_pcs_df = fit_pca(joined[title_raw_cols].to_numpy(), "title")
    thumb_pca, thumb_pcs_df = fit_pca(joined[thumb_raw_cols].to_numpy(), "thumb")

    title_pcs_df.index = joined.index
    thumb_pcs_df.index = joined.index

    # --- Categorical one-hot encoding ---------------------------------------
    encoder = OneHotEncoder(sparse_output=False, handle_unknown="ignore")
    encoded = encoder.fit_transform(joined[CATEGORICAL_COLUMNS])
    encoded_df = pd.DataFrame(
        encoded, columns=encoder.get_feature_names_out(CATEGORICAL_COLUMNS), index=joined.index
    )

    # --- Assemble final table -----------------------------------------------
    output_df = pd.concat(
        [
            joined[["video_id", "channel_id"] + SIMPLE_FEATURE_COLUMNS],
            encoded_df,
            title_pcs_df,
            thumb_pcs_df,
            joined[["v_inf", "tau", "target_log_vinf_rel", "target_log_tau"]],
        ],
        axis=1,
    )

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    output_df.to_csv(FEATURES_OUT, index=False)
    joblib.dump(title_pca, TITLE_PCA_OUT)
    joblib.dump(thumb_pca, THUMBNAIL_PCA_OUT)
    joblib.dump(encoder, ENCODER_OUT)
    with CHANNEL_MEDIANS_OUT.open("w") as f:
        json.dump(channel_medians.to_dict(), f, indent=2)

    print_summary(
        output_df,
        n_before_join,
        n_dropped_join,
        removal_counts,
        vinf_rel_dist,
        title_pca,
        thumb_pca,
    )


def print_summary(
    output_df: pd.DataFrame,
    n_before_join: int,
    n_dropped_join: int,
    removal_counts: dict[str, int],
    vinf_rel_dist: pd.Series,
    title_pca: PCA,
    thumb_pca: PCA,
) -> None:
    line = "=" * 78
    print(line)
    print("BUILD_FEATURES SUMMARY")
    print(line)
    print(f"Rows before join:          {n_before_join}")
    print(f"Rows dropped by the join:  {n_dropped_join}")

    print("\n--- Outlier filter (stepwise removal) ------------------------------------")
    remaining = n_before_join - n_dropped_join
    print(f"  after join:                                                 {remaining}")
    for label, removed in removal_counts.items():
        remaining -= removed
        print(f"  - {label:<58} -{removed:<5} -> {remaining}")

    print("\n--- target_log_vinf_rel distribution (no bound filter - outliers kept) ---")
    print(
        f"  count={vinf_rel_dist['count']:.0f}  mean={vinf_rel_dist['mean']:.4f}  "
        f"std={vinf_rel_dist['std']:.4f}  min={vinf_rel_dist['min']:.4f}  "
        f"25%={vinf_rel_dist['25%']:.4f}  50%={vinf_rel_dist['50%']:.4f}  "
        f"75%={vinf_rel_dist['75%']:.4f}  max={vinf_rel_dist['max']:.4f}"
    )

    print(f"\nFinal rows:                {len(output_df)}")
    print(f"Final column count:        {len(output_df.columns)}")

    print("\n--- PCA explained variance retained -----------------------------------")
    print(f"  title embeddings   (768 -> {N_PCA_COMPONENTS}): {title_pca.explained_variance_ratio_.sum() * 100:.1f}%")
    print(f"  thumbnail embeddings (512 -> {N_PCA_COMPONENTS}): {thumb_pca.explained_variance_ratio_.sum() * 100:.1f}%")

    print("\n--- Target column statistics -------------------------------------------")
    for col in ["target_log_vinf_rel", "target_log_tau"]:
        series = output_df[col]
        n_non_finite = (~np.isfinite(series)).sum()
        print(
            f"  {col:<22} min={series.min():.4f}  max={series.max():.4f}  "
            f"mean={series.mean():.4f}  median={series.median():.4f}  std={series.std():.4f}  "
            f"non-finite={n_non_finite}"
        )
    print(line)


if __name__ == "__main__":
    main()
