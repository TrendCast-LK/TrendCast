"""Forecast inference pipeline for TrendCast.

Matches the artifact export in artifacts/ and MODEL_INTEGRATION.md.
Loads training artifacts once, never refits PCA, and assembles the feature
vector in the exact CatBoost column order from feature_columns.json.
"""

from __future__ import annotations

import io
import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import requests
from catboost import CatBoostClassifier, CatBoostRegressor
from PIL import Image
from sentence_transformers import SentenceTransformer

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = REPO_ROOT / "artifacts"

THUMBNAIL_DOWNLOAD_TIMEOUT_SECONDS = 10.0
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
CHANNEL_HISTORY_TTL_SECONDS = 6 * 60 * 60
MAX_HISTORY_VIDEOS = 30

# IMPORTANT: training used RAW (un-normalised) sentence-transformer output.
# extract_features.py called model.encode(...) with no normalize_embeddings,
# saved those to .npy, and export_artifacts.py fitted PCA on them directly.
# Serving must therefore NOT L2-normalise before PCA.
NORMALISE_EMBEDDINGS_BEFORE_PCA = False


class ThumbnailDownloadError(RuntimeError):
    """Raised when a thumbnail URL cannot be fetched or decoded."""


class InsufficientHistoryError(ValueError):
    """Channel lacks enough prior uploads for a reliable S estimate."""


class ChannelNotFoundError(ValueError):
    """Channel ID does not resolve to a public channel."""


class QuotaExceededError(RuntimeError):
    """YouTube Data API quota exhausted."""


@dataclass
class InferenceState:
    ready: bool = False
    error: str | None = None
    device: str | None = None
    load_time_seconds: float | None = None

    config: dict[str, Any] = field(default_factory=dict)
    feature_columns: list[str] = field(default_factory=list)
    maturation_curve: dict[int, float] = field(default_factory=dict)
    pca_text: Any = None
    pca_image: Any = None
    mean_image_embedding: np.ndarray = field(default_factory=lambda: np.zeros(512, dtype=float))
    mean_text_embedding: np.ndarray = field(default_factory=lambda: np.zeros(512, dtype=float))

    magnitude_model: Any = None
    shape_form_model: Any = None
    shape_c_model: Any = None
    shape_theta_model: Any = None
    shape_k_model: Any = None
    shape_t0_model: Any = None

    text_model: SentenceTransformer | None = None
    image_model: SentenceTransformer | None = None


_state = InferenceState()
_CHANNEL_HISTORY_CACHE: dict[str, dict[str, Any]] = {}


def get_state() -> InferenceState:
    return _state


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def _clip(value: float, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)


# ---------------------------------------------------------------------------
# Artifact loading
# ---------------------------------------------------------------------------

def load_artifacts() -> None:
    """Load every forecast artifact once at FastAPI startup."""
    global _state
    start = time.time()
    state = InferenceState()

    try:
        required = [
            "catboost_magnitude.cbm", "catboost_shape_form.cbm",
            "catboost_shape_c.cbm", "catboost_shape_theta.cbm",
            "catboost_shape_k.cbm", "catboost_shape_t0.cbm",
            "pca_text.pkl", "pca_image.pkl",
            "feature_columns.json", "maturation_curve.json", "config.json",
        ]
        for name in required:
            path = ARTIFACTS_DIR / name
            if not path.exists():
                raise FileNotFoundError(f"missing artifact: {path}")

        state.device = "cpu"
        state.config = _load_json(ARTIFACTS_DIR / "config.json")
        state.feature_columns = _load_json(ARTIFACTS_DIR / "feature_columns.json")
        state.maturation_curve = {
            int(k): float(v)
            for k, v in _load_json(ARTIFACTS_DIR / "maturation_curve.json").items()
        }
        state.pca_text = joblib.load(ARTIFACTS_DIR / "pca_text.pkl")
        state.pca_image = joblib.load(ARTIFACTS_DIR / "pca_image.pkl")
        state.mean_image_embedding = np.asarray(
            state.config.get("mean_image_embedding", [0.0] * 512), dtype=float
        )
        state.mean_text_embedding = np.asarray(
            state.config.get("mean_text_embedding", [0.0] * 512), dtype=float
        )

        def _reg(name: str) -> CatBoostRegressor:
            m = CatBoostRegressor()
            m.load_model(str(ARTIFACTS_DIR / name))
            return m

        state.magnitude_model = _reg("catboost_magnitude.cbm")
        state.shape_c_model = _reg("catboost_shape_c.cbm")
        state.shape_theta_model = _reg("catboost_shape_theta.cbm")
        state.shape_k_model = _reg("catboost_shape_k.cbm")
        state.shape_t0_model = _reg("catboost_shape_t0.cbm")

        state.shape_form_model = CatBoostClassifier()
        state.shape_form_model.load_model(str(ARTIFACTS_DIR / "catboost_shape_form.cbm"))

        state.text_model = SentenceTransformer("sentence-transformers/clip-ViT-B-32-multilingual-v1")
        state.image_model = SentenceTransformer("sentence-transformers/clip-ViT-B-32")

        state.ready = True
    except Exception as exc:  # noqa: BLE001
        state.ready = False
        state.error = f"{type(exc).__name__}: {exc}"
        logging.exception("[inference] failed to load artifacts")

    state.load_time_seconds = time.time() - start
    logging.info(
        "[inference] artifact load %s in %.1fs",
        "succeeded" if state.ready else "FAILED",
        state.load_time_seconds,
    )
    _state = state


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _youtube_api_key() -> str:
    key = os.getenv("YOUTUBE_API_KEY")
    if not key:
        raise RuntimeError("YOUTUBE_API_KEY is not set")
    return key


def _parse_duration_seconds(duration: str | None) -> float:
    """ISO 8601 duration -> seconds. 'PT8M32S' -> 512.0

    duration_s is the single most important feature in the model, so a parsing
    bug here matters more than anywhere else.
    """
    if not duration:
        return float("nan")
    text = str(duration).strip().upper()
    if not text.startswith("PT"):
        return float("nan")
    hours = minutes = seconds = 0
    pos, buf = 2, ""
    while pos < len(text):
        ch = text[pos]
        if ch.isdigit():
            buf += ch
        elif buf:
            n = int(buf)
            if ch == "H":
                hours = n
            elif ch == "M":
                minutes = n
            elif ch == "S":
                seconds = n
            buf = ""
        pos += 1
    return float(hours * 3600 + minutes * 60 + seconds)


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=float).reshape(-1)
    b = np.asarray(b, dtype=float).reshape(-1)
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-8 or nb < 1e-8:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _sin_cos(value: float, period: float) -> tuple[float, float]:
    return (
        math.sin(2.0 * math.pi * value / period),
        math.cos(2.0 * math.pi * value / period),
    )


def _prepare_for_pca(emb: np.ndarray) -> np.ndarray:
    """Match whatever preprocessing training applied before fitting PCA."""
    arr = np.asarray(emb, dtype=float).reshape(-1)
    if NORMALISE_EMBEDDINGS_BEFORE_PCA:
        n = np.linalg.norm(arr)
        if n > 1e-8:
            arr = arr / n
    return arr


# ---------------------------------------------------------------------------
# Channel history
# ---------------------------------------------------------------------------

def _fetch_channel_history(channel_id: str) -> list[dict[str, Any]]:
    """Last <=30 uploads with view counts and category IDs.

    Fetches ONE page of 50 playlist items, not the full history. A large
    channel can have tens of thousands of uploads; paginating through all of
    them would cost hundreds of API calls per request and exhaust the daily
    quota, only to discard all but 30 rows.
    """
    cache_key = channel_id.strip()
    now = time.time()
    cached = _CHANNEL_HISTORY_CACHE.get(cache_key)
    if cached and (now - cached["fetched_at"]) < CHANNEL_HISTORY_TTL_SECONDS:
        return cached["videos"]

    api_key = _youtube_api_key()

    resp = requests.get(
        f"{YOUTUBE_API_BASE}/channels",
        params={"part": "contentDetails", "id": channel_id, "key": api_key},
        timeout=15,
    )
    if resp.status_code in (400, 404):
        raise ChannelNotFoundError(f"channel not found: {channel_id}")
    if resp.status_code in (403, 429):
        raise QuotaExceededError("YouTube API quota exceeded")
    resp.raise_for_status()

    items = resp.json().get("items") or []
    if not items:
        raise ChannelNotFoundError(f"channel not found: {channel_id}")
    playlist_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    # single page; playlistItems returns newest first
    resp = requests.get(
        f"{YOUTUBE_API_BASE}/playlistItems",
        params={"part": "snippet", "playlistId": playlist_id,
                "maxResults": 50, "key": api_key},
        timeout=15,
    )
    if resp.status_code in (403, 429):
        raise QuotaExceededError("YouTube API quota exceeded")
    resp.raise_for_status()

    video_ids = [
        it["snippet"]["resourceId"]["videoId"]
        for it in (resp.json().get("items") or [])
        if it.get("snippet", {}).get("resourceId", {}).get("videoId")
    ][:50]
    if not video_ids:
        return []

    resp = requests.get(
        f"{YOUTUBE_API_BASE}/videos",
        params={"part": "snippet,statistics", "id": ",".join(video_ids), "key": api_key},
        timeout=15,
    )
    if resp.status_code in (403, 429):
        raise QuotaExceededError("YouTube API quota exceeded")
    resp.raise_for_status()

    history: list[dict[str, Any]] = []
    for item in (resp.json().get("items") or []):
        snippet = item.get("snippet") or {}
        stats = item.get("statistics") or {}
        published_at = snippet.get("publishedAt")
        if not published_at:
            continue
        category_id = snippet.get("categoryId")
        history.append({
            "video_id": item.get("id"),
            "published_at": datetime.fromisoformat(
                published_at.replace("Z", "+00:00")
            ).astimezone(timezone.utc),
            "view_count": int(stats.get("viewCount", 0) or 0),
            "category_id": int(category_id) if category_id else None,
        })

    history.sort(key=lambda r: r["published_at"], reverse=True)
    history = history[:MAX_HISTORY_VIDEOS]
    _CHANNEL_HISTORY_CACHE[cache_key] = {"fetched_at": now, "videos": history}
    return history


def _channel_features(
    history: list[dict[str, Any]], maturation_curve: dict[int, float], min_prior: int
) -> dict[str, float]:
    """S and the four channel statistics, computed exactly as in training."""
    now = datetime.now(timezone.utc)
    equivalents: list[float] = []
    for row in history:
        age_days = (now - row["published_at"]).days
        if age_days < 1:
            continue  # too immature to rescale
        key = min(max(age_days, 1), 7)
        frac = float(maturation_curve.get(key, maturation_curve.get(7, 1.0)))
        equivalents.append(float(row["view_count"]) / max(frac, 1e-9))

    if len(equivalents) < min_prior:
        raise InsufficientHistoryError(
            f"This channel has {len(equivalents)} usable prior uploads; "
            f"at least {min_prior} are needed for a reliable baseline."
        )

    arr = np.asarray(equivalents, dtype=float)

    # Training: np.std(np.log(views.clip(lower=1))) -- a SPREAD, not a magnitude.
    logs = np.log(np.maximum(arr, 1.0))

    categories = {r["category_id"] for r in history if r.get("category_id") is not None}

    return {
        "channel_baseline": float(np.median(arr)),          # S -- median, not mean
        "channel_view_std": float(np.std(arr)),
        "channel_log_volatility": float(np.std(logs)),
        "channel_category_diversity": float(len(categories) or 1),
        "channel_video_count": float(len(history)),
        "n_equivalents": len(equivalents),
    }


# ---------------------------------------------------------------------------
# Feature vector
# ---------------------------------------------------------------------------

def _build_feature_vector(
    state: InferenceState,
    *,
    duration_s: float,
    title_length: int,
    description_length: int,
    tag_count: int,
    publish_time: datetime,
    thumbnail_alignment: float,
    has_thumbnail: int,
    text_embedding: np.ndarray,
    image_embedding: np.ndarray,
    category_id: int | None,
    channel_video_count: float,
    channel_median_views: float,
    channel_view_std: float,
    channel_log_volatility: float,
    channel_category_diversity: float,
) -> np.ndarray:
    """Assemble the row in exactly feature_columns.json order.

    CatBoost matches features positionally: a reordered vector produces
    confident nonsense with no error raised.
    """
    values: list[float] = [
        float(duration_s),
        float(title_length),
        float(description_length),
        float(tag_count),
    ]

    hour_sin, hour_cos = _sin_cos(float(publish_time.hour), 24.0)
    dow_sin, dow_cos = _sin_cos(float(publish_time.weekday()), 7.0)
    values.extend([hour_sin, hour_cos, dow_sin, dow_cos])
    values.extend([float(thumbnail_alignment), float(has_thumbnail)])

    text_pca = state.pca_text.transform(_prepare_for_pca(text_embedding).reshape(1, -1))[0]
    img_pca = state.pca_image.transform(_prepare_for_pca(image_embedding).reshape(1, -1))[0]
    values.extend(float(v) for v in text_pca)
    values.extend(float(v) for v in img_pca)

    for category in state.config.get("categories", []):
        values.append(1.0 if category_id is not None and int(category_id) == int(category) else 0.0)

    values.extend([
        float(channel_video_count),
        float(channel_median_views),
        float(channel_view_std),
        float(channel_log_volatility),
        float(channel_category_diversity),
    ])

    if len(values) != len(state.feature_columns):
        raise ValueError(
            f"feature length mismatch: expected {len(state.feature_columns)}, got {len(values)}"
        )
    return np.asarray(values, dtype=float)


# ---------------------------------------------------------------------------
# Curve reconstruction
# ---------------------------------------------------------------------------

def _forecast_curve(
    *, channel_baseline: float, log_m: float, shape_form: int,
    params: dict[str, float], horizon: int = 7,
) -> tuple[list[dict[str, Any]], str, float, dict[str, float]]:
    m = math.exp(log_m)

    if shape_form == 1:
        k = _clip(params["k"], 0.05, 10.0)
        t0 = _clip(params["t0"], -5.0, 7.0)
        family = "logistic"
        used = {"k": k, "t0": t0}
        denom = _sigmoid(k * (horizon - t0))

        def f(day: int) -> float:
            return _sigmoid(k * (day - t0)) / denom if denom > 1e-9 else day / horizon
    else:
        c = _clip(params["c"], 0.05, 100.0)
        theta = _clip(params["theta"], 0.05, 20.0)
        family = "power"
        used = {"c": c, "theta": theta}
        denom = 1.0 - (1.0 + horizon / c) ** (-theta)

        def f(day: int) -> float:
            raw = 1.0 - (1.0 + day / c) ** (-theta)
            return raw / denom if denom > 1e-9 else day / horizon

    vals = np.asarray([channel_baseline * m * f(d) for d in range(1, horizon + 1)], dtype=float)
    vals = np.maximum.accumulate(vals)          # cumulative views cannot decrease
    vals[-1] = channel_baseline * m             # F(horizon) = 1 exactly

    curve = [{"day": d, "views": int(round(float(v)))} for d, v in enumerate(vals, start=1)]
    day1_fraction = float(vals[0] / vals[-1]) if vals[-1] > 0 else 0.0
    return curve, family, day1_fraction, used


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def _download_thumbnail(url: str) -> tuple[Image.Image | None, str | None]:
    try:
        resp = requests.get(url, timeout=THUMBNAIL_DOWNLOAD_TIMEOUT_SECONDS)
        if resp.status_code != 200 or not resp.content:
            raise ThumbnailDownloadError("thumbnail URL could not be fetched")
        return Image.open(io.BytesIO(resp.content)).convert("RGB"), None
    except Exception:  # noqa: BLE001
        return None, "thumbnail_unavailable"


def run_forecast(
    state: InferenceState,
    title: str,
    thumbnail_url: str | None,
    scheduled_upload_time: datetime,
    channel_id: str | None,
    *,
    tags: list[str] | None = None,
    duration: str | None = None,
    description: str | None = None,
    category_id: int | None = None,
) -> dict:
    image, warning = (_download_thumbnail(thumbnail_url) if thumbnail_url else (None, "thumbnail_unavailable"))
    return run_forecast_on_image(
        state,
        title=title,
        image=image,                       # may be None -> mean-embedding fallback
        scheduled_upload_time=scheduled_upload_time,
        channel_id=channel_id,
        tags=tags or [],
        duration=duration,
        description=description,
        category_id=category_id,
        warnings=[warning] if warning else [],
    )


def run_forecast_on_image(
    state: InferenceState,
    title: str,
    image: Image.Image | None,
    scheduled_upload_time: datetime,
    channel_id: str | None,
    *,
    tags: list[str] | None = None,
    duration: str | None = None,
    description: str | None = None,
    category_id: int | None = None,
    warnings: list[str] | None = None,
) -> dict:
    if not state.ready:
        load_artifacts()
        state = get_state()
    if not state.ready:
        raise RuntimeError(state.error or "forecast models are not loaded")
    if not channel_id:
        raise ValueError("channel_id is required for a forecast")

    tags = list(tags or [])
    warnings = list(warnings or [])
    config = state.config
    min_prior = int(config.get("min_prior_videos", 5))

    # --- text embedding -----------------------------------------------------
    text_input = f"{title}. {title}. {' '.join(tags[:10])}"
    text_emb = state.text_model.encode(
        [text_input], convert_to_numpy=True, show_progress_bar=False
    )[0]

    # --- image embedding ----------------------------------------------------
    # A missing thumbnail uses the stored MEAN embedding, not a zero vector and
    # not a black image: both are specific, unusual points in embedding space
    # that the model would read as a real (weird) thumbnail.
    if image is not None:
        image_emb = state.image_model.encode(
            [image], convert_to_numpy=True, show_progress_bar=False
        )[0]
        has_thumbnail = 1
        thumbnail_alignment = _cosine_similarity(image_emb, text_emb)
    else:
        image_emb = state.mean_image_embedding.copy()
        has_thumbnail = 0
        thumbnail_alignment = 0.0
        if "thumbnail_unavailable" not in warnings:
            warnings.append("thumbnail_unavailable")

    # --- channel features ---------------------------------------------------
    history = _fetch_channel_history(channel_id)
    if len(history) < min_prior:
        raise InsufficientHistoryError(
            f"This channel has {len(history)} prior uploads; "
            f"at least {min_prior} are needed for a reliable baseline."
        )
    ch = _channel_features(history, state.maturation_curve, min_prior)
    channel_baseline = ch["channel_baseline"]

    # --- remaining scalars --------------------------------------------------
    publish_time = (
        scheduled_upload_time.astimezone(timezone.utc)
        if scheduled_upload_time.tzinfo
        else scheduled_upload_time.replace(tzinfo=timezone.utc)
    )

    features = _build_feature_vector(
        state,
        duration_s=_parse_duration_seconds(duration),
        title_length=len(title or ""),
        description_length=len(description or ""),
        tag_count=len(tags),
        publish_time=publish_time,
        thumbnail_alignment=thumbnail_alignment,
        has_thumbnail=has_thumbnail,
        text_embedding=text_emb,
        image_embedding=image_emb,
        category_id=category_id,
        channel_video_count=ch["channel_video_count"],
        channel_median_views=channel_baseline,
        channel_view_std=ch["channel_view_std"],
        channel_log_volatility=ch["channel_log_volatility"],
        channel_category_diversity=ch["channel_category_diversity"],
    )
    row = features.reshape(1, -1)

    # --- predict ------------------------------------------------------------
    log_m = float(_clip(
        float(state.magnitude_model.predict(row)[0]),
        float(config["log_m_min"]), float(config["log_m_max"]),
    ))
    shape_form = int(state.shape_form_model.predict(row)[0])

    if shape_form == 1:
        params = {
            "k": float(state.shape_k_model.predict(row)[0]),
            "t0": float(state.shape_t0_model.predict(row)[0]),
        }
    else:
        params = {
            "c": float(state.shape_c_model.predict(row)[0]),
            "theta": float(state.shape_theta_model.predict(row)[0]),
        }

    curve, shape_family, day1_fraction, used_params = _forecast_curve(
        channel_baseline=channel_baseline,
        log_m=log_m,
        shape_form=shape_form,
        params=params,
        horizon=int(config.get("horizon_days", 7)),
    )

    # --- uncertainty band (required -- see MODEL_INTEGRATION.md 4.5) --------
    residual_std = float(config.get("residual_std", 1.09))
    band = float(config.get("band_multiplier", 0.8))
    range_low = channel_baseline * math.exp(log_m - band * residual_std)
    range_high = channel_baseline * math.exp(log_m + band * residual_std)

    return {
        "status": "ok",
        "channel_baseline": float(channel_baseline),
        "avg_views_per_video": float(channel_baseline),
        "multiplier": float(math.exp(log_m)),
        "point_estimate_7d": float(curve[-1]["views"]),
        "range_7d": {"low": float(range_low), "high": float(range_high)},
        "curve": curve,
        "shape_family": shape_family,
        "shape_params": used_params,
        "day1_fraction": day1_fraction,
        "based_on_videos": len(history),
        "warnings": warnings,
        "used_channel_context": True,
    }