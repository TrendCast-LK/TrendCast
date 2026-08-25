"""Inference pipeline for POST /forecast.

Loads the trained ml/ pipeline's artifacts once (see load_artifacts(), called
from main.py's startup hook) and reproduces its feature-construction path,
column for column, for a single (title, thumbnail, upload time, optional
channel) request. Mirrors the transformations in:

  ml/features_simple.py   - simple numeric features, upload-time conversion
  ml/embed_titles.py      - title embedding model (LaBSE)
  ml/embed_thumbnails.py  - thumbnail embedding model (CLIP)
  ml/build_features.py    - PCA reduction, one-hot encoding, target scaling
  ml/train_model.py       - feature-name sanitization, target reconstruction

Any drift between this file and those scripts silently produces wrong
predictions rather than an error, so keep them in sync by hand - ml/ is not
imported directly (it's a standalone script collection, not a package, and
isn't guaranteed to ship alongside the backend at deploy time).
"""

from __future__ import annotations

import io
import json
import logging
import math
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import requests
import torch
from PIL import Image
from sentence_transformers import SentenceTransformer
from transformers import CLIPModel, CLIPProcessor

from db import get_cursor

MODELS_DIR = Path(__file__).resolve().parent / "models"
VINF_MODEL_PATH = MODELS_DIR / "vinf_model.joblib"
TAU_MODEL_PATH = MODELS_DIR / "tau_model.joblib"
TITLE_PCA_PATH = MODELS_DIR / "title_pca.joblib"
THUMBNAIL_PCA_PATH = MODELS_DIR / "thumbnail_pca.joblib"
ENCODER_PATH = MODELS_DIR / "categorical_encoder.joblib"
CHANNEL_MEDIANS_PATH = MODELS_DIR / "channel_medians.json"

TITLE_MODEL_NAME = "sentence-transformers/LaBSE"  # ~1.8GB, downloads on first run
THUMBNAIL_MODEL_NAME = "openai/clip-vit-base-patch32"  # downloads on first run

THUMBNAIL_DOWNLOAD_TIMEOUT_SECONDS = 10.0

SRI_LANKA_OFFSET = timedelta(hours=5, minutes=30)

# Same column list/order as ml/features_simple.py's SIMPLE_FEATURE_COLUMNS
# plus the title/word-count columns it computes inline.
CATEGORICAL_COLUMNS = ["category_id", "size_tier"]

# category_id is never known at inference time (an unpublished video has no
# YouTube category yet, and ForecastRequest doesn't collect one) - -1 is
# guaranteed absent from the trained category set (1,2,10,17,19,20,22,24,25,
# 26,27,28,29), so the encoder's handle_unknown="ignore" zeroes out every
# category_id_* column. That's an honest "no signal" representation rather
# than guessing a category.
UNKNOWN_CATEGORY_ID = -1

CHANNEL_STATS_COLUMNS = [
    "subscriber_count",
    "total_views",
    "video_count",
    "avg_views_per_video",
    "views_per_subscriber",
    "engagement_ratio",
    "size_tier",
    "tier_category",
]

CHANNEL_STATS_BY_ID_SQL = """
    SELECT subscriber_count, total_views, video_count, avg_views_per_video,
           views_per_subscriber, engagement_ratio, size_tier, tier_category
    FROM channel_stats_enriched
    WHERE channel_id = %(channel_id)s
"""

ALL_CHANNEL_STATS_SQL = """
    SELECT subscriber_count, total_views, video_count, avg_views_per_video,
           views_per_subscriber, engagement_ratio, tier_category
    FROM channel_stats_enriched
"""


class ThumbnailDownloadError(Exception):
    """Raised for any thumbnail URL that can't be fetched or isn't a valid
    image - the /forecast endpoint turns this into an HTTP 400."""


@dataclass
class InferenceState:
    ready: bool = False
    error: str | None = None
    device: str | None = None
    load_time_seconds: float | None = None

    vinf_model: object = None
    tau_model: object = None
    title_pca: object = None
    thumbnail_pca: object = None
    encoder: object = None
    channel_medians: dict[str, float] = field(default_factory=dict)
    global_median_vinf: float | None = None
    feature_names: list[str] = field(default_factory=list)

    title_model: SentenceTransformer | None = None
    thumbnail_model: CLIPModel | None = None
    thumbnail_processor: CLIPProcessor | None = None


_state = InferenceState()


def get_state() -> InferenceState:
    return _state


def load_artifacts() -> None:
    """Load every model artifact once. Never raises - on failure, state.ready
    stays False and state.error carries the reason, so /forecast can return a
    503 instead of the process crashing at startup."""
    global _state
    start = time.time()
    state = InferenceState()

    try:
        state.device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"[inference] using device: {state.device}")

        for path in (VINF_MODEL_PATH, TAU_MODEL_PATH, TITLE_PCA_PATH, THUMBNAIL_PCA_PATH, ENCODER_PATH, CHANNEL_MEDIANS_PATH):
            if not path.exists():
                raise FileNotFoundError(f"missing artifact: {path}")

        state.vinf_model = joblib.load(VINF_MODEL_PATH)
        state.tau_model = joblib.load(TAU_MODEL_PATH)
        state.title_pca = joblib.load(TITLE_PCA_PATH)
        state.thumbnail_pca = joblib.load(THUMBNAIL_PCA_PATH)
        state.encoder = joblib.load(ENCODER_PATH)

        with CHANNEL_MEDIANS_PATH.open(encoding="utf-8") as f:
            state.channel_medians = json.load(f)
        state.global_median_vinf = statistics.median(state.channel_medians.values())

        vinf_feature_names = state.vinf_model.get_booster().feature_names
        tau_feature_names = state.tau_model.get_booster().feature_names
        if vinf_feature_names != tau_feature_names:
            raise ValueError("vinf_model and tau_model were trained on different feature sets")
        state.feature_names = vinf_feature_names

        print(f"[inference] loading {TITLE_MODEL_NAME} (~1.8GB, cached after first download)...")
        state.title_model = SentenceTransformer(TITLE_MODEL_NAME, device=state.device)

        print(f"[inference] loading {THUMBNAIL_MODEL_NAME} (downloads on first run, cached after)...")
        state.thumbnail_model = CLIPModel.from_pretrained(THUMBNAIL_MODEL_NAME).to(state.device).eval()
        state.thumbnail_processor = CLIPProcessor.from_pretrained(THUMBNAIL_MODEL_NAME)

        state.ready = True
    except Exception as exc:  # noqa: BLE001 - any failure here must become a 503, not a crash
        state.ready = False
        state.error = f"{type(exc).__name__}: {exc}"
        logging.error("[inference] failed to load artifacts: %s", state.error)

    state.load_time_seconds = time.time() - start
    print(
        f"[inference] artifact load {'succeeded' if state.ready else 'FAILED'} "
        f"in {state.load_time_seconds:.1f}s"
    )
    _state = state


def sanitize_feature_name(name: str) -> str:
    """Must match ml/train_model.py's sanitize_feature_name exactly - the
    saved models' booster feature names were sanitized this way at training
    time (XGBoost rejects '<', '[', ']', which the 'Micro (<1K)' size_tier
    category hits)."""
    return name.replace("<", "lt").replace(">", "gt").replace("[", "(").replace("]", ")")


def download_thumbnail(url: str) -> Image.Image:
    try:
        response = requests.get(url, timeout=THUMBNAIL_DOWNLOAD_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise ThumbnailDownloadError(f"could not reach thumbnail URL: {exc}") from exc

    if response.status_code != 200:
        raise ThumbnailDownloadError(f"thumbnail URL returned HTTP {response.status_code}")
    if not response.content:
        raise ThumbnailDownloadError("thumbnail URL returned an empty response")

    try:
        return Image.open(io.BytesIO(response.content)).convert("RGB")
    except Exception as exc:
        raise ThumbnailDownloadError(f"response was not a valid image: {exc}") from exc


def embed_title(state: InferenceState, title: str) -> np.ndarray:
    raw = state.title_model.encode([title], convert_to_numpy=True)
    return state.title_pca.transform(raw)[0]


def embed_thumbnail(state: InferenceState, image: Image.Image) -> np.ndarray:
    inputs = state.thumbnail_processor(images=[image], return_tensors="pt").to(state.device)
    with torch.no_grad():
        features = state.thumbnail_model.get_image_features(**inputs)
    # Same defensive unwrap as ml/embed_thumbnails.py: newer transformers
    # versions can return a BaseModelOutputWithPooling instead of a bare tensor.
    image_embeds = getattr(features, "pooler_output", features)
    raw = image_embeds.cpu().numpy().astype(np.float32)
    return state.thumbnail_pca.transform(raw)[0]


def compute_upload_timing(scheduled_upload_time: datetime) -> tuple[int, int, bool]:
    """Same Sri Lanka time (UTC+5:30) conversion as ml/features_simple.py.
    Naive datetimes are assumed UTC, matching how published_at is stored."""
    if scheduled_upload_time.tzinfo is None:
        scheduled_utc = scheduled_upload_time.replace(tzinfo=timezone.utc)
    else:
        scheduled_utc = scheduled_upload_time.astimezone(timezone.utc)
    scheduled_slt = scheduled_utc + SRI_LANKA_OFFSET
    upload_hour = scheduled_slt.hour
    upload_dayofweek = scheduled_slt.weekday()  # Monday=0..Sunday=6, matches pandas .dt.dayofweek
    is_weekend = upload_dayofweek in (5, 6)
    return upload_hour, upload_dayofweek, is_weekend


def _size_tier_bucket(subscriber_count: float) -> str:
    """Same bucket boundaries as the channel_stats_enriched view (see
    CLAUDE.md): Micro/Small/Mid/Large/Mega. Used only to turn a fallback
    median subscriber_count into a matching categorical size_tier."""
    if subscriber_count < 1_000:
        return "Micro (<1K)"
    if subscriber_count < 10_000:
        return "Small (1K–10K)"
    if subscriber_count < 100_000:
        return "Mid (10K–100K)"
    if subscriber_count < 1_000_000:
        return "Large (100K–1M)"
    return "Mega (1M+)"


_median_channel_stats_cache: dict | None = None


def _get_median_channel_stats() -> dict:
    """Dataset-wide median channel stats, queried from channel_stats_enriched
    once and cached in memory for the life of the process."""
    global _median_channel_stats_cache
    if _median_channel_stats_cache is None:
        with get_cursor() as cur:
            cur.execute(ALL_CHANNEL_STATS_SQL)
            columns = [col.name for col in cur.description]
            rows = cur.fetchall()
        medians = pd.DataFrame(rows, columns=columns).astype(float).median()
        subscriber_count = medians["subscriber_count"]
        _median_channel_stats_cache = {
            "subscriber_count": subscriber_count,
            "total_views": medians["total_views"],
            "video_count": medians["video_count"],
            "avg_views_per_video": medians["avg_views_per_video"],
            "views_per_subscriber": medians["views_per_subscriber"],
            "engagement_ratio": medians["engagement_ratio"],
            "tier_category": medians["tier_category"],
            "size_tier": _size_tier_bucket(subscriber_count),
        }
    return _median_channel_stats_cache


def get_channel_stats(channel_id: str | None) -> tuple[dict, bool]:
    """Returns (channel_stats, used_real_channel_context). Falls back to
    dataset-wide medians if channel_id is missing or not found in
    channel_stats_enriched."""
    if channel_id:
        with get_cursor() as cur:
            cur.execute(CHANNEL_STATS_BY_ID_SQL, {"channel_id": channel_id})
            row = cur.fetchone()
        if row is not None:
            return dict(zip(CHANNEL_STATS_COLUMNS, row)), True

    return _get_median_channel_stats(), False


def get_channel_median_vinf(state: InferenceState, channel_id: str | None) -> float:
    """channel_medians.json only covers the ~52 channels in the filtered
    training set, which can differ from what's live in the DB (new channels
    added since extraction) - fall back to the global median V_inf whenever
    the channel isn't one of those."""
    if channel_id and channel_id in state.channel_medians:
        return state.channel_medians[channel_id]
    return state.global_median_vinf


def build_feature_row(
    state: InferenceState,
    title: str,
    duration_seconds: float,
    tag_count: int,
    upload_hour: int,
    upload_dayofweek: int,
    is_weekend: bool,
    channel_stats: dict,
    title_pcs: np.ndarray,
    thumb_pcs: np.ndarray,
) -> pd.DataFrame:
    """Assembles one feature row, named and ordered to exactly match the
    trained booster's feature_names (see load_artifacts)."""
    raw: dict[str, float | str | bool] = {
        "title_char_length": len(title),
        "title_word_count": len(title.split()),
        "duration_seconds": duration_seconds,
        "tag_count": tag_count,
        "upload_hour": upload_hour,
        "upload_dayofweek": upload_dayofweek,
        "is_weekend": is_weekend,
        "subscriber_count": channel_stats["subscriber_count"],
        "total_views": channel_stats["total_views"],
        "video_count": channel_stats["video_count"],
        "avg_views_per_video": channel_stats["avg_views_per_video"],
        "views_per_subscriber": channel_stats["views_per_subscriber"],
        "engagement_ratio": channel_stats["engagement_ratio"],
        "tier_category": channel_stats["tier_category"],
    }

    encoded = state.encoder.transform(
        pd.DataFrame({"category_id": [UNKNOWN_CATEGORY_ID], "size_tier": [channel_stats["size_tier"]]})
    )[0]
    for name, value in zip(state.encoder.get_feature_names_out(CATEGORICAL_COLUMNS), encoded):
        raw[name] = value

    for i, value in enumerate(title_pcs):
        raw[f"title_pc_{i}"] = float(value)
    for i, value in enumerate(thumb_pcs):
        raw[f"thumb_pc_{i}"] = float(value)

    sanitized = {sanitize_feature_name(k): v for k, v in raw.items()}
    row = [sanitized.get(name, 0.0) for name in state.feature_names]
    return pd.DataFrame([row], columns=state.feature_names).astype(float)


def run_forecast(
    state: InferenceState,
    title: str,
    thumbnail_url: str,
    scheduled_upload_time: datetime,
    channel_id: str | None,
) -> dict:
    image = download_thumbnail(thumbnail_url)

    title_pcs = embed_title(state, title)
    thumb_pcs = embed_thumbnail(state, image)
    upload_hour, upload_dayofweek, is_weekend = compute_upload_timing(scheduled_upload_time)
    channel_stats, used_channel_context = get_channel_stats(channel_id)

    # duration_seconds and tag_count are unknowable before upload. Reuse the
    # exact fallback ml/features_simple.py already applies to missing values
    # for these fields - NaN for duration (parse_iso8601_duration's fallback
    # for missing/malformed durations), 0 for tag count (count_tags' fallback
    # for null/empty tags) - so the model sees the same "unknown" encoding it
    # saw at training time rather than a fabricated value.
    X = build_feature_row(
        state,
        title=title,
        duration_seconds=float("nan"),
        tag_count=0,
        upload_hour=upload_hour,
        upload_dayofweek=upload_dayofweek,
        is_weekend=is_weekend,
        channel_stats=channel_stats,
        title_pcs=title_pcs,
        thumb_pcs=thumb_pcs,
    )

    pred_log_vinf_rel = float(state.vinf_model.predict(X)[0])
    pred_log_tau = float(state.tau_model.predict(X)[0])

    channel_median_vinf = get_channel_median_vinf(state, channel_id)
    v_inf = channel_median_vinf * math.exp(pred_log_vinf_rel)
    tau = math.exp(pred_log_tau)

    curve = [
        {"day": day, "views": int(round(v_inf * (1.0 - math.exp(-24.0 * day / tau))))}
        for day in range(1, 8)
    ]

    return {
        "curve": curve,
        "v_inf": v_inf,
        "tau": tau,
        "used_channel_context": used_channel_context,
    }
