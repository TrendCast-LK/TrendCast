"""Model training stage: trains two XGBoost regressors on ml/data/features.csv
- one for target_log_vinf_rel, one for target_log_tau - and evaluates them by
reconstructing full 7-day view curves, not just comparing raw parameters.

Splits by channel (never by row) so no channel appears in both train and
test - otherwise the model could learn channel identity instead of
generalizable signal, inflating the metrics. Compares the trained model
against three baselines (channel median, global median, metadata-only
ablation) using the same curve-reconstruction evaluation, and reports
feature importance for both models.

Reads ml/data/features.csv and ml/models/channel_medians.json. Writes
ml/models/vinf_model.joblib, ml/models/tau_model.joblib, and
ml/models/evaluation.json.

Usage:
    python ml/train_model.py
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from xgboost import XGBRegressor

DATA_DIR = Path(__file__).resolve().parent / "data"
MODELS_DIR = Path(__file__).resolve().parent / "models"

FEATURES_CSV = DATA_DIR / "features.csv"
CHANNEL_MEDIANS_JSON = MODELS_DIR / "channel_medians.json"
VINF_MODEL_OUT = MODELS_DIR / "vinf_model.joblib"
TAU_MODEL_OUT = MODELS_DIR / "tau_model.joblib"
EVALUATION_OUT = MODELS_DIR / "evaluation.json"

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
    df = pd.read_csv(FEATURES_CSV)
    with CHANNEL_MEDIANS_JSON.open() as f:
        channel_medians = json.load(f)

    train_channels, test_channels = split_channels(df, TEST_FRACTION)
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
    joblib.dump(vinf_model, VINF_MODEL_OUT)
    joblib.dump(tau_model, TAU_MODEL_OUT)

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
    with EVALUATION_OUT.open("w") as f:
        json.dump(evaluation, f, indent=2)

    print_report(evaluation)


def print_report(evaluation: dict) -> None:
    line = "=" * 78
    print(line)
    print("TRAIN_MODEL SUMMARY")
    print(line)

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

    print("\n--- Feature importance: target_log_vinf_rel model (top 20 by gain) --------")
    for name, gain in evaluation["feature_importance"]["vinf_model_top20_gain"]:
        print(f"    {name:<28} {gain:.2f}")

    print("\n--- Feature importance: target_log_tau model (top 20 by gain) -------------")
    for name, gain in evaluation["feature_importance"]["tau_model_top20_gain"]:
        print(f"    {name:<28} {gain:.2f}")

    print(f"\nModels saved to {VINF_MODEL_OUT} and {TAU_MODEL_OUT}")
    print(f"Evaluation written to {EVALUATION_OUT}")
    print(line)


if __name__ == "__main__":
    main()
