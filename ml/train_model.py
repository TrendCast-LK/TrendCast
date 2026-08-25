"""Model training stage: trains two XGBoost regressors on the assembled
features table - one for target_log_vinf_rel, one for target_log_tau - and
evaluates them by reconstructing full 7-day view curves, not just comparing
raw parameters.

Splits by channel (never by row) so no channel appears in both train and
test - otherwise the model could learn channel identity instead of
generalizable signal, inflating the metrics. Compares the trained model
against three baselines (channel median, global median, metadata-only
ablation) using the same curve-reconstruction evaluation, and reports
feature importance for both models.

Reads ml/data/features_{clip,dinov3}.csv (selected via --thumbnail-encoder)
and the matching ml/models/channel_medians_{clip,dinov3}.json. Writes
ml/models/vinf_model_{clip,dinov3}.joblib, ml/models/tau_model_{clip,dinov3}.joblib,
and ml/models/evaluation_{clip,dinov3}.json.

The channel train/test split is pinned to the one recorded in the original
ml/models/evaluation.json (the pre-existing CLIP run) rather than recomputed
per encoder, so RMSLE numbers stay directly comparable across encoders - see
REFERENCE_EVALUATION_JSON below.

Usage:
    python ml/train_model.py
    python ml/train_model.py --thumbnail-encoder dinov3
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from xgboost import XGBRegressor

DATA_DIR = Path(__file__).resolve().parent / "data"
MODELS_DIR = Path(__file__).resolve().parent / "models"

# The original (pre-dual-encoder) CLIP run's evaluation.json. Its "split" is
# reused as-is for every encoder so the same four held-out channels are
# always the test set, keeping RMSLE comparable; its "full_model" RMSLE is
# also the "recorded CLIP results" a DINOv3 run compares itself against.
REFERENCE_EVALUATION_JSON = MODELS_DIR / "evaluation.json"
# Fallback if that file has never been produced - recorded CLIP results as of
# this comparison (see REFERENCE_EVALUATION_JSON docstring above).
FALLBACK_CLIP_RMSLE = {"overall": 1.42, "day1": 1.42, "day3": 1.39, "day7": 1.37}

THUMBNAIL_ENCODER_CONFIGS = {
    "clip": {
        "features_csv": DATA_DIR / "features_clip.csv",
        "channel_medians_json": MODELS_DIR / "channel_medians_clip.json",
        "vinf_model_out": MODELS_DIR / "vinf_model_clip.joblib",
        "tau_model_out": MODELS_DIR / "tau_model_clip.joblib",
        "evaluation_out": MODELS_DIR / "evaluation_clip.json",
    },
    "dinov3": {
        "features_csv": DATA_DIR / "features_dinov3.csv",
        "channel_medians_json": MODELS_DIR / "channel_medians_dinov3.json",
        "vinf_model_out": MODELS_DIR / "vinf_model_dinov3.joblib",
        "tau_model_out": MODELS_DIR / "tau_model_dinov3.joblib",
        "evaluation_out": MODELS_DIR / "evaluation_dinov3.json",
    },
}

TEST_FRACTION = 0.2
NON_FEATURE_COLUMNS = ["video_id", "channel_id", "v_inf", "tau", "target_log_vinf_rel", "target_log_tau"]
EMBEDDING_PREFIXES = ("title_pc_", "thumb_pc_")

# Curve-reconstruction evaluation grid. Excludes t=0, where V(t) is
# identically 0 for every video by construction (MAPE/RMSLE undefined there).
EVAL_HOURS = np.concatenate([np.arange(1, 49, 1), np.arange(54, 169, 6)]).astype(float)
DAY_CHECKPOINTS = {"day1": 24.0, "day3": 72.0, "day7": 168.0}

# Floor for the MAPE denominator (in views) - some videos have small enough
# V_inf/tau that early-curve values are under 1 view, which would otherwise
# blow up the percentage error on a near-zero actual.
MAPE_EPS = 1.0

TOP_N_IMPORTANCE = 20


def sanitize_feature_name(name: str) -> str:
    """XGBoost rejects feature names containing '[', ']', or '<' (used by its
    own split-condition serialization) - one-hot column names like
    'size_tier_Micro (<1K)' hit this, so swap the offending characters for
    plain text rather than touching features.csv itself."""
    return name.replace("<", "lt").replace(">", "gt").replace("[", "(").replace("]", ")")


def get_reference_split(df: pd.DataFrame) -> tuple[list[str], list[str]]:
    """Train/test channel split, pinned across encoder runs for comparable
    RMSLE. Reuses the split recorded in REFERENCE_EVALUATION_JSON (the
    original CLIP run) when it exists; otherwise falls back to a fresh
    split_channels() split, which then becomes the de facto reference for
    any later runs (since evaluation_clip.json no longer overwrites the
    legacy REFERENCE_EVALUATION_JSON path)."""
    if REFERENCE_EVALUATION_JSON.exists():
        with REFERENCE_EVALUATION_JSON.open() as f:
            split = json.load(f)["split"]
        print(
            f"[train_model] reusing channel split from {REFERENCE_EVALUATION_JSON} "
            f"({split['n_test_channels']} test channels) for cross-encoder comparability"
        )
        return split["train_channels"], split["test_channels"]
    print(
        f"[train_model] no reference split at {REFERENCE_EVALUATION_JSON} - "
        "computing a fresh split (this run's channels become the reference)"
    )
    return split_channels(df, TEST_FRACTION)


def split_channels(df: pd.DataFrame, test_fraction: float) -> tuple[list[str], list[str]]:
    """Assign whole channels to train/test, greedily packing the largest
    channels first into whichever split they fit into without overshooting
    the video-count target - gets close to an 80/20 split of *videos* even
    though channel sizes are wildly uneven (one channel alone is ~36% of the
    data, so a per-channel random split would badly miss the target)."""
    counts = df["channel_id"].value_counts()
    target_test = round(test_fraction * len(df))

    remaining = target_test
    test_channels: list[str] = []
    train_channels: list[str] = []
    for channel_id, count in counts.sort_values(ascending=False).items():
        if count <= remaining:
            test_channels.append(channel_id)
            remaining -= count
        else:
            train_channels.append(channel_id)
    return train_channels, test_channels


def train_xgb_model(X_train: pd.DataFrame, y_train: pd.Series, X_test: pd.DataFrame, y_test: pd.Series) -> XGBRegressor:
    model = XGBRegressor(
        n_estimators=2000,
        max_depth=6,
        learning_rate=0.03,
        subsample=0.8,
        colsample_bytree=0.8,
        objective="reg:squarederror",
        eval_metric="rmse",
        early_stopping_rounds=50,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
    return model


def top_feature_importance(model: XGBRegressor, n: int) -> list[tuple[str, float]]:
    gain_scores = model.get_booster().get_score(importance_type="gain")
    return sorted(gain_scores.items(), key=lambda kv: kv[1], reverse=True)[:n]


def reconstruct_curve(v_inf: np.ndarray, tau: np.ndarray, hours: np.ndarray) -> np.ndarray:
    """V(t) = V_inf * (1 - exp(-t/tau)), broadcast to (n_videos, n_hours)."""
    return v_inf[:, None] * (1.0 - np.exp(-hours[None, :] / tau[:, None]))


def mape(actual: np.ndarray, pred: np.ndarray) -> float:
    return float(np.mean(np.abs(actual - pred) / np.maximum(np.abs(actual), MAPE_EPS)))


def rmsle(actual: np.ndarray, pred: np.ndarray) -> float:
    return float(np.sqrt(np.mean((np.log1p(pred) - np.log1p(actual)) ** 2)))


def evaluate_curves(actual_vinf: np.ndarray, actual_tau: np.ndarray, pred_vinf: np.ndarray, pred_tau: np.ndarray) -> dict:
    actual_curve = reconstruct_curve(actual_vinf, actual_tau, EVAL_HOURS)
    pred_curve = reconstruct_curve(pred_vinf, pred_tau, EVAL_HOURS)

    metrics = {"overall": {"mape": mape(actual_curve, pred_curve), "rmsle": rmsle(actual_curve, pred_curve)}}
    for label, t in DAY_CHECKPOINTS.items():
        actual_t = actual_vinf * (1.0 - np.exp(-t / actual_tau))
        pred_t = pred_vinf * (1.0 - np.exp(-t / pred_tau))
        metrics[label] = {"mape": mape(actual_t, pred_t), "rmsle": rmsle(actual_t, pred_t)}
    return metrics


def predict_curve_params(
    vinf_model: XGBRegressor, tau_model: XGBRegressor, X: pd.DataFrame, channel_median_vinf: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    pred_vinf = channel_median_vinf * np.exp(vinf_model.predict(X))
    pred_tau = np.exp(tau_model.predict(X))
    return pred_vinf, pred_tau


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--thumbnail-encoder",
        choices=sorted(THUMBNAIL_ENCODER_CONFIGS),
        default="clip",
        help="Which thumbnail-encoder feature set to train on (default: clip)",
    )
    args = parser.parse_args()
    encoder_config = THUMBNAIL_ENCODER_CONFIGS[args.thumbnail_encoder]
    print(f"[train_model] thumbnail encoder: {args.thumbnail_encoder}")

    df = pd.read_csv(encoder_config["features_csv"])
    with encoder_config["channel_medians_json"].open() as f:
        channel_medians = json.load(f)

    train_channels, test_channels = get_reference_split(df)
    train_df = df[df["channel_id"].isin(train_channels)].reset_index(drop=True)
    test_df = df[df["channel_id"].isin(test_channels)].reset_index(drop=True)

    feature_cols = [c for c in df.columns if c not in NON_FEATURE_COLUMNS]
    ablated_cols = [c for c in feature_cols if not c.startswith(EMBEDDING_PREFIXES)]

    rename_map = {c: sanitize_feature_name(c) for c in feature_cols}
    inverse_rename_map = {v: k for k, v in rename_map.items()}

    def to_xgb_input(source_df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
        return source_df[cols].rename(columns=rename_map)

    X_train, X_test = to_xgb_input(train_df, feature_cols), to_xgb_input(test_df, feature_cols)
    y_train_vinf, y_test_vinf = train_df["target_log_vinf_rel"], test_df["target_log_vinf_rel"]
    y_train_tau, y_test_tau = train_df["target_log_tau"], test_df["target_log_tau"]

    print("[train_model] training full models (with embeddings)...")
    vinf_model = train_xgb_model(X_train, y_train_vinf, X_test, y_test_vinf)
    tau_model = train_xgb_model(X_train, y_train_tau, X_test, y_test_tau)

    print("[train_model] training metadata-only ablation models (no embeddings)...")
    X_train_ablated = to_xgb_input(train_df, ablated_cols)
    X_test_ablated = to_xgb_input(test_df, ablated_cols)
    vinf_model_ablated = train_xgb_model(X_train_ablated, y_train_vinf, X_test_ablated, y_test_vinf)
    tau_model_ablated = train_xgb_model(X_train_ablated, y_train_tau, X_test_ablated, y_test_tau)

    test_channel_median_vinf = test_df["channel_id"].map(channel_medians).to_numpy(dtype=float)
    actual_vinf = test_df["v_inf"].to_numpy()
    actual_tau = test_df["tau"].to_numpy()

    # --- Full model -----------------------------------------------------------
    pred_vinf, pred_tau = predict_curve_params(vinf_model, tau_model, X_test, test_channel_median_vinf)
    full_model_metrics = evaluate_curves(actual_vinf, actual_tau, pred_vinf, pred_tau)

    # --- Baseline 1: channel median curve --------------------------------------
    # Every test channel's own median V_inf/tau (computed over the whole
    # filtered dataset, same as channel_medians.json) - measures how much
    # channel identity alone explains, with no per-video signal at all.
    channel_median_tau_map = df.groupby("channel_id")["tau"].median()
    baseline_channel_vinf = test_channel_median_vinf
    baseline_channel_tau = test_df["channel_id"].map(channel_median_tau_map).to_numpy(dtype=float)
    channel_baseline_metrics = evaluate_curves(actual_vinf, actual_tau, baseline_channel_vinf, baseline_channel_tau)

    # --- Baseline 2: global median curve ----------------------------------------
    # Train-set-wide median V_inf/tau applied to every test video - the
    # "no information at all" baseline.
    global_median_vinf = train_df["v_inf"].median()
    global_median_tau = train_df["tau"].median()
    baseline_global_vinf = np.full(len(test_df), global_median_vinf)
    baseline_global_tau = np.full(len(test_df), global_median_tau)
    global_baseline_metrics = evaluate_curves(actual_vinf, actual_tau, baseline_global_vinf, baseline_global_tau)

    # --- Baseline 3: metadata-only ablation -------------------------------------
    pred_vinf_ablated, pred_tau_ablated = predict_curve_params(
        vinf_model_ablated, tau_model_ablated, X_test_ablated, test_channel_median_vinf
    )
    ablation_metrics = evaluate_curves(actual_vinf, actual_tau, pred_vinf_ablated, pred_tau_ablated)

    def restore_names(importance: list[tuple[str, float]]) -> list[tuple[str, float]]:
        return [(inverse_rename_map.get(name, name), gain) for name, gain in importance]

    vinf_importance = restore_names(top_feature_importance(vinf_model, TOP_N_IMPORTANCE))
    tau_importance = restore_names(top_feature_importance(tau_model, TOP_N_IMPORTANCE))

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(vinf_model, encoder_config["vinf_model_out"])
    joblib.dump(tau_model, encoder_config["tau_model_out"])

    evaluation = {
        "split": {
            "test_fraction_target": TEST_FRACTION,
            "train_channels": train_channels,
            "test_channels": test_channels,
            "n_train_channels": len(train_channels),
            "n_test_channels": len(test_channels),
            "n_train_videos": len(train_df),
            "n_test_videos": len(test_df),
            "test_video_fraction": len(test_df) / len(df),
        },
        "full_model": full_model_metrics,
        "baselines": {
            "channel_median": channel_baseline_metrics,
            "global_median": global_baseline_metrics,
            "metadata_only_ablation": ablation_metrics,
        },
        "feature_importance": {
            "vinf_model_top20_gain": vinf_importance,
            "tau_model_top20_gain": tau_importance,
        },
    }
    evaluation_out = encoder_config["evaluation_out"]
    with evaluation_out.open("w") as f:
        json.dump(evaluation, f, indent=2)

    print_report(evaluation, args.thumbnail_encoder, encoder_config)


def print_report(evaluation: dict, thumbnail_encoder: str, encoder_config: dict) -> None:
    line = "=" * 78
    print(line)
    print("TRAIN_MODEL SUMMARY")
    print(line)
    print(f"Thumbnail encoder:         {thumbnail_encoder}")

    split = evaluation["split"]
    print("\n--- Channel split (by video count, not channel count) --------------------")
    print(f"  train: {split['n_train_channels']} channels, {split['n_train_videos']} videos")
    print(f"  test:  {split['n_test_channels']} channels, {split['n_test_videos']} videos "
          f"({split['test_video_fraction'] * 100:.1f}% of total)")
    print(f"  test channels:  {', '.join(split['test_channels'])}")
    print(f"  train channels: {', '.join(split['train_channels'])}")

    def _print_metrics(label: str, metrics: dict) -> None:
        print(f"\n  {label}")
        for period in ["overall", "day1", "day3", "day7"]:
            m = metrics[period]
            print(f"    {period:<8} MAPE={m['mape']:.4f}  RMSLE={m['rmsle']:.4f}")

    print("\n--- Curve-reconstruction evaluation (test set) ----------------------------")
    _print_metrics("Full model (title + thumbnail + metadata)", evaluation["full_model"])
    _print_metrics("Baseline: channel median curve", evaluation["baselines"]["channel_median"])
    _print_metrics("Baseline: global median curve", evaluation["baselines"]["global_median"])
    _print_metrics("Baseline: metadata-only (embeddings removed)", evaluation["baselines"]["metadata_only_ablation"])

    if thumbnail_encoder == "dinov3":
        print_clip_comparison(evaluation)

    print("\n--- Feature importance: target_log_vinf_rel model (top 20 by gain) --------")
    for name, gain in evaluation["feature_importance"]["vinf_model_top20_gain"]:
        print(f"    {name:<28} {gain:.2f}")

    print("\n--- Feature importance: target_log_tau model (top 20 by gain) -------------")
    for name, gain in evaluation["feature_importance"]["tau_model_top20_gain"]:
        print(f"    {name:<28} {gain:.2f}")

    print(f"\nModels saved to {encoder_config['vinf_model_out']} and {encoder_config['tau_model_out']}")
    print(f"Evaluation written to {encoder_config['evaluation_out']}")
    print(line)


def print_clip_comparison(evaluation: dict) -> None:
    """DINOv3 full-model RMSLE against the recorded CLIP results, plus the
    DINOv3 feature set's own metadata-only ablation, so the value of the
    embeddings themselves is visible for both encoders side by side."""
    clip_rmsle = dict(FALLBACK_CLIP_RMSLE)
    source = "hardcoded (recorded CLIP results)"
    if REFERENCE_EVALUATION_JSON.exists():
        with REFERENCE_EVALUATION_JSON.open() as f:
            clip_full_model = json.load(f)["full_model"]
        clip_rmsle = {period: clip_full_model[period]["rmsle"] for period in FALLBACK_CLIP_RMSLE}
        source = str(REFERENCE_EVALUATION_JSON)

    dinov3_rmsle = {period: evaluation["full_model"][period]["rmsle"] for period in FALLBACK_CLIP_RMSLE}
    ablation_rmsle = {period: evaluation["baselines"]["metadata_only_ablation"][period]["rmsle"] for period in FALLBACK_CLIP_RMSLE}

    print("\n--- DINOv3 vs CLIP: RMSLE comparison (test set) ---------------------------")
    print(f"  CLIP reference: {source}")
    header = f"  {'period':<8} {'CLIP':>10} {'DINOv3':>10} {'DINOv3 (metadata-only ablation)':>34}"
    print(header)
    for period in ["overall", "day1", "day3", "day7"]:
        print(
            f"  {period:<8} {clip_rmsle[period]:>10.4f} {dinov3_rmsle[period]:>10.4f} {ablation_rmsle[period]:>34.4f}"
        )


if __name__ == "__main__":
    main()
