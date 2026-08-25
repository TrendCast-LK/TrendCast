"""Inference pipeline for POST /forecast.

Loads the trained ml/ pipeline's artifacts once (see load_artifacts(), called
from main.py's startup hook) and reproduces its feature-construction path,
column for column, for a single (title, thumbnail, upload time, optional
channel) request, by calling the same shared functions the training
pipeline uses (ml/services/) - see:

  ml/features_simple.py   - simple numeric features, upload-time conversion
  ml/embed_titles.py      - title embedding model (LaBSE)
  ml/embed_thumbnails.py  - thumbnail embedding model (CLIP)
  ml/build_features.py    - PCA reduction, one-hot encoding, target scaling
  ml/train_model.py       - feature-name sanitization, target reconstruction

Model *loading* and request-scoped state (InferenceState, the DB-backed
channel-stats lookup) stay here; the pure feature-transformation logic lives
in ml/services/ so training and serving can never drift apart.
"""

from __future__ import annotations

import io
import json
import logging
import math
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
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

# ml/ is a sibling of backend/, not installed as a package - add the repo
# root to sys.path so `from ml.services import ...` below resolves regardless
# of how uvicorn was launched (this is the only file in backend/ that needs
# ml/, so the path bootstrap lives here rather than in every entry point).
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ml.services import feature_row as feature_row_service  # noqa: E402
from ml.services import pca as pca_service  # noqa: E402
from ml.services import tabular_features  # noqa: E402
from ml.services import thumbnail_embedding  # noqa: E402
from ml.services import title_embedding  # noqa: E402

MODELS_DIR = Path(__file__).resolve().parent / "models"
VINF_MODEL_PATH = MODELS_DIR / "vinf_model.joblib"
TAU_MODEL_PATH = MODELS_DIR / "tau_model.joblib"
TITLE_PCA_PATH = MODELS_DIR / "title_pca.joblib"
THUMBNAIL_PCA_PATH = MODELS_DIR / "thumbnail_pca.joblib"
ENCODER_PATH = MODELS_DIR / "categorical_encoder.joblib"
CHANNEL_MEDIANS_PATH = MODELS_DIR / "channel_medians.json"

THUMBNAIL_DOWNLOAD_TIMEOUT_SECONDS = 10.0

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

        print(f"[inference] loading {title_embedding.MODEL_NAME} (~1.8GB, cached after first download)...")
        state.title_model = title_embedding.load_model(state.device)

        print(f"[inference] loading {thumbnail_embedding.MODEL_NAME} (downloads on first run, cached after)...")
        state.thumbnail_model, state.thumbnail_processor = thumbnail_embedding.load_model(state.device)

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
    raw = title_embedding.embed_titles(state.title_model, [title])
    return pca_service.apply_pca(state.title_pca, raw)[0]


def embed_thumbnail(state: InferenceState, image: Image.Image) -> np.ndarray:
    raw = thumbnail_embedding.embed_images(state.thumbnail_model, state.thumbnail_processor, [image], state.device)
    return pca_service.apply_pca(state.thumbnail_pca, raw)[0]


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


def run_forecast(
    state: InferenceState,
    title: str,
    thumbnail_url: str,
    scheduled_upload_time: datetime,
    channel_id: str | None,
) -> dict:
    image = download_thumbnail(thumbnail_url)
    return run_forecast_on_image(state, title, image, scheduled_upload_time, channel_id)


def run_forecast_on_image(
    state: InferenceState,
    title: str,
    image: Image.Image,
    scheduled_upload_time: datetime,
    channel_id: str | None,
) -> dict:
    """Same pipeline as run_forecast, for a thumbnail already loaded in memory
    (e.g. an uploaded file) - avoids an HTTP round-trip through
    download_thumbnail for callers that already have the image bytes."""
    title_pcs = embed_title(state, title)
    thumb_pcs = embed_thumbnail(state, image)
    upload_hour, upload_dayofweek, is_weekend = tabular_features.compute_upload_timing(scheduled_upload_time)
    channel_stats, used_channel_context = get_channel_stats(channel_id)

    # duration_seconds and tag_count are unknowable before upload. Reuse the
    # exact fallback ml/features_simple.py already applies to missing values
    # for these fields - NaN for duration (parse_iso8601_duration's fallback
    # for missing/malformed durations), 0 for tag count (count_tags' fallback
    # for null/empty tags) - so the model sees the same "unknown" encoding it
    # saw at training time rather than a fabricated value.
    simple_values = {
        "title_char_length": tabular_features.title_char_length(title),
        "title_word_count": tabular_features.title_word_count(title),
        "duration_seconds": float("nan"),
        "tag_count": 0,
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
    X = feature_row_service.assemble_feature_row(
        simple_values,
        encoder=state.encoder,
        category_id=UNKNOWN_CATEGORY_ID,
        size_tier=channel_stats["size_tier"],
        title_pcs=title_pcs,
        thumb_pcs=thumb_pcs,
        feature_names=state.feature_names,
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
        "avg_views_per_video": float(channel_stats["avg_views_per_video"]),
    }
