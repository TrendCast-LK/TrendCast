"""Forecast inference pipeline for TrendCast: a CatBoost + HistAttnV2 ensemble.

    log(m) = w * log(m)_HistAttnV2 + (1 - w) * log(m)_CatBoost

with w = ensemble_weight from histattn_config.json. Artifacts come from
ensemble_artifacts/ (export_artifacts.py, then export_histattn_v2.py). They are
loaded once; the PCA objects and the HistAttnV2 scaler are never refit.

One request encodes the target video once. The raw 512-dim CLIP text/image
vectors feed HistAttnV2 and their PCA-32 projections feed CatBoost; the two
forms must not be swapped. HistAttnV2 also needs the channel's prior videos,
already encoded. Those come from the channel history cache (channel_cache.py),
which is filled in the background at signup / channel refresh. When the cache
has nothing for the channel, the forecast is CatBoost-only and
used_channel_context is False.
"""

from __future__ import annotations

import io
import json
import logging
import math
import os
import pickle
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import joblib
import numpy as np
import pandas as pd
import requests
from catboost import CatBoostClassifier, CatBoostRegressor
from PIL import Image
from sentence_transformers import SentenceTransformer

import histattn

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = REPO_ROOT / "ensemble_artifacts"

REQUIRED_ARTIFACTS = [
    "catboost_magnitude.cbm", "catboost_shape_form.cbm",
    "catboost_shape_c.cbm", "catboost_shape_theta.cbm",
    "catboost_shape_k.cbm", "catboost_shape_t0.cbm",
    "pca_text.pkl", "pca_image.pkl",
    "feature_columns.json", "maturation_curve.json", "config.json",
    "histattn_v2.pt", "histattn_scaler.pkl", "histattn_config.json", "histattn_tab_columns.json",
]

THUMBNAIL_DOWNLOAD_TIMEOUT_SECONDS = 10.0
THUMBNAIL_DOWNLOAD_WORKERS = 8
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

    histattn_model: Any = None
    histattn_scaler: Any = None
    histattn_config: dict[str, Any] = field(default_factory=dict)
    histattn_tab_columns: list[str] = field(default_factory=list)
    histattn_tab_indices: list[int] = field(default_factory=list)  # positions in feature_columns
    ensemble_weight: float = 0.0
    max_hist: int = 0
    encoder_signature: str = ""


@dataclass
class TargetEncoding:
    """One encoder pass over the target video, in both forms the ensemble needs."""
    text_512: np.ndarray          # raw text embedding -> HistAttnV2
    image_512: np.ndarray | None  # raw image embedding -> HistAttnV2; None without a thumbnail
    text_pca: np.ndarray          # PCA-32 of text_512 -> CatBoost
    image_pca: np.ndarray         # PCA-32 of image_512 (or the mean embedding) -> CatBoost
    has_thumbnail: int
    thumbnail_alignment: float


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
        for name in REQUIRED_ARTIFACTS:
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

        _load_histattn(state)

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


def _load_histattn(state: InferenceState) -> None:
    """Loads HistAttnV2 and its scaler, and checks them against the CatBoost
    artifacts they were exported alongside."""
    cfg = _load_json(ARTIFACTS_DIR / "histattn_config.json")
    tab_columns = _load_json(ARTIFACTS_DIR / "histattn_tab_columns.json")
    with (ARTIFACTS_DIR / "histattn_scaler.pkl").open("rb") as fh:
        scaler = pickle.load(fh)  # fitted StandardScaler; transform only, never fit

    if cfg.get("ensemble_uses_dino"):
        raise ValueError("histattn_config.json expects DINOv2 embeddings, which this backend does not compute")
    if not 0.0 <= float(cfg["ensemble_weight"]) <= 1.0:
        raise ValueError(f"ensemble_weight must be in [0, 1], got {cfg['ensemble_weight']}")
    if int(cfg["n_tab"]) != len(tab_columns) or int(scaler.n_features_in_) != len(tab_columns):
        raise ValueError(
            f"HistAttnV2 tabular width mismatch: config n_tab={cfg['n_tab']}, "
            f"scaler={scaler.n_features_in_}, histattn_tab_columns.json={len(tab_columns)}"
        )
    if list(getattr(scaler, "feature_names_in_", tab_columns)) != tab_columns:
        raise ValueError("histattn_scaler.pkl column order differs from histattn_tab_columns.json")
    missing = [c for c in tab_columns if c not in state.feature_columns]
    if missing:
        raise ValueError(f"HistAttnV2 tabular columns not in feature_columns.json: {missing}")

    state.histattn_config = cfg
    state.histattn_tab_columns = tab_columns
    state.histattn_tab_indices = [state.feature_columns.index(c) for c in tab_columns]
    state.histattn_scaler = scaler
    state.histattn_model = histattn.load_model(ARTIFACTS_DIR / "histattn_v2.pt", cfg)
    state.ensemble_weight = float(cfg["ensemble_weight"])
    state.max_hist = int(cfg["max_hist"])
    # Cached history embeddings are only valid for the encoders and text
    # template that produced them; channel_cache.py re-encodes on a change.
    state.encoder_signature = "|".join(
        str(state.config.get(k, "")) for k in ("text_encoder", "image_encoder", "text_input_template")
    )


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

def _fetch_channel_history(channel_id: str, *, use_cache: bool = True) -> list[dict[str, Any]]:
    """Last <=30 uploads, newest first, with view counts, category IDs and the
    title/tags/thumbnail/duration the channel history cache encodes.

    Fetches ONE page of 50 playlist items, not the full history. A large
    channel can have tens of thousands of uploads; paginating through all of
    them would cost hundreds of API calls per request and exhaust the daily
    quota, only to discard all but 30 rows.
    """
    cache_key = channel_id.strip()
    now = time.time()
    cached = _CHANNEL_HISTORY_CACHE.get(cache_key)
    if use_cache and cached and (now - cached["fetched_at"]) < CHANNEL_HISTORY_TTL_SECONDS:
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
        params={"part": "snippet,statistics,contentDetails", "id": ",".join(video_ids), "key": api_key},
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
        thumbnails = snippet.get("thumbnails") or {}
        thumbnail = thumbnails.get("high") or thumbnails.get("medium") or thumbnails.get("default") or {}
        duration_s = _parse_duration_seconds((item.get("contentDetails") or {}).get("duration"))
        history.append({
            "video_id": item.get("id"),
            "published_at": datetime.fromisoformat(
                published_at.replace("Z", "+00:00")
            ).astimezone(timezone.utc),
            "view_count": int(stats.get("viewCount", 0) or 0),
            "category_id": int(category_id) if category_id else None,
            "title": snippet.get("title") or "",
            "tags": list(snippet.get("tags") or []),
            "thumbnail_url": thumbnail.get("url"),
            "duration_s": None if math.isnan(duration_s) else duration_s,
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
    text_pca: np.ndarray,
    image_pca: np.ndarray,
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

    values.extend(float(v) for v in text_pca)
    values.extend(float(v) for v in image_pca)

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
    # Both export scripts train on X.fillna(0): a missing value (e.g. no
    # duration on the form) was 0 in training, never NaN. CatBoost would
    # accept a NaN, but HistAttnV2's scaler and network propagate it into a
    # NaN forecast.
    return np.nan_to_num(np.asarray(values, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)


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
# Encoding (shared by the target video and the channel history cache)
# ---------------------------------------------------------------------------

def _download_thumbnail(url: str) -> tuple[Image.Image | None, str | None]:
    try:
        resp = requests.get(url, timeout=THUMBNAIL_DOWNLOAD_TIMEOUT_SECONDS)
        if resp.status_code != 200 or not resp.content:
            raise ThumbnailDownloadError("thumbnail URL could not be fetched")
        return Image.open(io.BytesIO(resp.content)).convert("RGB"), None
    except Exception:  # noqa: BLE001
        return None, "thumbnail_unavailable"


def _text_input(title: str, tags: list[str]) -> str:
    # config.json text_input_template: "{title}. {title}. {tags_joined}"
    return f"{title}. {title}. {' '.join(tags[:10])}"


def encode_texts(state: InferenceState, texts: list[str]) -> np.ndarray:
    """Raw (un-normalised) 512-dim text embeddings, one row per input."""
    return state.text_model.encode(texts, convert_to_numpy=True, show_progress_bar=False)


def encode_images(state: InferenceState, images: list[Image.Image]) -> np.ndarray:
    """Raw (un-normalised) 512-dim image embeddings, one row per input."""
    return state.image_model.encode(images, convert_to_numpy=True, show_progress_bar=False)


def encode_target(
    state: InferenceState, title: str, tags: list[str], image: Image.Image | None
) -> TargetEncoding:
    """Runs each encoder once and derives both forms from that one output: raw
    512 for HistAttnV2, PCA-32 (loaded, never refit) for CatBoost."""
    text_512 = encode_texts(state, [_text_input(title, tags)])[0]

    # A missing thumbnail gives CatBoost the stored MEAN embedding, not a zero
    # vector and not a black image: both are specific, unusual points in
    # embedding space that the model would read as a real (weird) thumbnail.
    # HistAttnV2 gets a zero image half instead (see histattn.joint_embedding).
    if image is not None:
        image_512 = encode_images(state, [image])[0]
        catboost_image = image_512
        has_thumbnail = 1
        thumbnail_alignment = _cosine_similarity(image_512, text_512)
    else:
        image_512 = None
        catboost_image = state.mean_image_embedding
        has_thumbnail = 0
        thumbnail_alignment = 0.0

    return TargetEncoding(
        text_512=text_512,
        image_512=image_512,
        text_pca=state.pca_text.transform(_prepare_for_pca(text_512).reshape(1, -1))[0],
        image_pca=state.pca_image.transform(_prepare_for_pca(catboost_image).reshape(1, -1))[0],
        has_thumbnail=has_thumbnail,
        thumbnail_alignment=thumbnail_alignment,
    )


def encode_history_videos(state: InferenceState, videos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Raw 512-dim text/image embeddings for channel videos from
    _fetch_channel_history, encoded exactly like a target video. Adds
    text_embedding and image_embedding (None when the thumbnail could not be
    downloaded) to a copy of each row."""
    if not videos:
        return []
    texts = encode_texts(state, [_text_input(v.get("title") or "", v.get("tags") or []) for v in videos])

    def _fetch(video: dict[str, Any]) -> Image.Image | None:
        url = video.get("thumbnail_url")
        return _download_thumbnail(url)[0] if url else None

    with ThreadPoolExecutor(max_workers=THUMBNAIL_DOWNLOAD_WORKERS) as pool:
        images = list(pool.map(_fetch, videos))
    present = [i for i, img in enumerate(images) if img is not None]
    image_rows = dict(zip(present, encode_images(state, [images[i] for i in present]))) if present else {}

    return [
        {**video, "text_embedding": texts[i], "image_embedding": image_rows.get(i)}
        for i, video in enumerate(videos)
    ]


# ---------------------------------------------------------------------------
# Ensemble
# ---------------------------------------------------------------------------

class _StageTimer:
    def __init__(self) -> None:
        self.ms: dict[str, float] = {}

    @contextmanager
    def stage(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.ms[name] = (time.perf_counter() - start) * 1000.0


def blend_log_m(state: InferenceState, log_m_catboost: float, log_m_histattn: float | None) -> float:
    if log_m_histattn is None:
        return log_m_catboost
    w = state.ensemble_weight
    return w * log_m_histattn + (1.0 - w) * log_m_catboost


def histattn_tab_features(state: InferenceState, catboost_row: np.ndarray) -> np.ndarray:
    """HistAttnV2's 30 tabular inputs are CatBoost's 94 minus the PCA columns,
    picked by name and scaled with the loaded scaler."""
    tab = np.asarray(catboost_row, dtype=float).reshape(-1)[state.histattn_tab_indices]
    frame = pd.DataFrame([tab], columns=state.histattn_tab_columns)
    return state.histattn_scaler.transform(frame)[0]


def _history_context(
    state: InferenceState,
    channel_id: str,
    target_time: datetime,
    anchor_s: float,
    channel_videos: list[dict[str, Any]],
    history_loader: Callable[[str], Any] | None,
    schedule_warm: Callable[[str], None] | None,
) -> tuple[tuple[np.ndarray, np.ndarray, np.ndarray] | None, str]:
    """HistAttnV2's history arrays from the channel history cache, plus the
    cache status: hit | stale | miss | error | disabled.

    Never encodes anything itself. A miss, a stale entry or uploads the cache
    has not seen yet schedule a background re-warm; a miss (e.g. a brand-new
    signup whose warm is still running) returns no history, so the forecast
    falls back to CatBoost-only instead of failing or blocking.

    history_loader(channel_id) returns None or an object with .videos (rows
    with video_id, published_at, view_count, duration_s, text_embedding,
    image_embedding) and .fresh."""
    if history_loader is None:
        return None, "disabled"

    try:
        cached = history_loader(channel_id)
        status = "miss" if cached is None else ("hit" if cached.fresh else "stale")
    except Exception:  # noqa: BLE001 - a cache outage must not fail the forecast
        logger.exception("[forecast] channel history cache read failed for %s", channel_id)
        cached, status = None, "error"

    if cached is not None and status == "hit" and cached.videos:
        # The channel uploaded since the last warm. Compared by date, not by
        # id: channel_videos can be up to 6h older than the cache, and an old
        # video that has since dropped out of the cached newest-max_hist must
        # not trigger a re-warm on every request.
        newest_cached = max(v["published_at"] for v in cached.videos)
        if any(v["published_at"] > newest_cached for v in channel_videos):
            status = "stale"

    if status != "hit" and schedule_warm is not None:
        schedule_warm(channel_id)
    if cached is None:
        return None, status

    # View counts from the metadata fetched for this request are fresher than
    # the cached ones; embeddings only come from the cache.
    fresh_views = {v["video_id"]: v["view_count"] for v in channel_videos}
    now = datetime.now(timezone.utc)
    videos = [
        {
            "published_at": v["published_at"],
            "day7_views": histattn.day7_estimate(
                fresh_views.get(v["video_id"], v["view_count"]),
                (now - v["published_at"]).total_seconds() / 86400.0,
                state.maturation_curve,
            ),
            "duration_s": v["duration_s"],
            "embedding": histattn.joint_embedding(v["text_embedding"], v["image_embedding"]),
        }
        for v in cached.videos
    ]
    arrays = histattn.build_history_arrays(
        videos, target_time, anchor_s, state.maturation_curve, state.max_hist
    )
    return arrays, status


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

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
    history_loader: Callable[[str], Any] | None = None,
    schedule_warm: Callable[[str], None] | None = None,
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
        history_loader=history_loader,
        schedule_warm=schedule_warm,
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
    history_loader: Callable[[str], Any] | None = None,
    schedule_warm: Callable[[str], None] | None = None,
) -> dict:
    """7-day forecast from the CatBoost + HistAttnV2 ensemble.

    history_loader reads the channel history cache (channel_cache.load_history)
    and schedule_warm queues a background re-warm of it; without a loader the
    forecast is CatBoost-only. used_channel_context in the result is True only
    when HistAttnV2 ran on cached channel history."""
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
    timer = _StageTimer()

    # --- target encoding: one pass, raw-512 and PCA-32 ----------------------
    with timer.stage("encode"):
        target = encode_target(state, title, tags, image)
    if not target.has_thumbnail and "thumbnail_unavailable" not in warnings:
        warnings.append("thumbnail_unavailable")

    # --- channel features (YouTube metadata, in-process cache) --------------
    with timer.stage("channel"):
        channel_videos = _fetch_channel_history(channel_id)
        if len(channel_videos) < min_prior:
            raise InsufficientHistoryError(
                f"This channel has {len(channel_videos)} prior uploads; "
                f"at least {min_prior} are needed for a reliable baseline."
            )
        ch = _channel_features(channel_videos, state.maturation_curve, min_prior)
    channel_baseline = ch["channel_baseline"]

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
        thumbnail_alignment=target.thumbnail_alignment,
        has_thumbnail=target.has_thumbnail,
        text_pca=target.text_pca,
        image_pca=target.image_pca,
        category_id=category_id,
        channel_video_count=ch["channel_video_count"],
        channel_median_views=channel_baseline,
        channel_view_std=ch["channel_view_std"],
        channel_log_volatility=ch["channel_log_volatility"],
        channel_category_diversity=ch["channel_category_diversity"],
    )
    row = features.reshape(1, -1)

    # --- CatBoost: magnitude + shape ----------------------------------------
    with timer.stage("catboost"):
        log_m_catboost = float(state.magnitude_model.predict(row)[0])
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

    # --- channel history (cache-first) --------------------------------------
    with timer.stage("history"):
        history, cache_status = _history_context(
            state, channel_id, publish_time, channel_baseline, channel_videos,
            history_loader, schedule_warm,
        )

    # --- HistAttnV2 ----------------------------------------------------------
    log_m_histattn: float | None = None
    with timer.stage("histattn"):
        if history is not None:
            log_m_histattn = histattn.predict_log_m(
                state.histattn_model,
                histattn_tab_features(state, features),
                histattn.joint_embedding(target.text_512, target.image_512),
                history,
            )
    if log_m_histattn is not None and not math.isfinite(log_m_histattn):
        logger.error("[forecast] HistAttnV2 returned %s for %s; using CatBoost only", log_m_histattn, channel_id)
        log_m_histattn = None
    used_channel_context = log_m_histattn is not None
    history_videos = int(history[2].sum()) if history is not None else 0
    if not used_channel_context:
        warnings.append("channel_history_unavailable")

    log_m = float(_clip(
        blend_log_m(state, log_m_catboost, log_m_histattn),
        float(config["log_m_min"]), float(config["log_m_max"]),
    ))

    curve, shape_family, day1_fraction, used_params = _forecast_curve(
        channel_baseline=channel_baseline,
        log_m=log_m,
        shape_form=shape_form,
        params=params,
        horizon=int(config.get("horizon_days", 7)),
    )

    # --- uncertainty band (required -- see MODEL_INTEGRATION.md 4.5) --------
    # residual_std is CatBoost's out-of-sample residual spread; the ensemble's
    # own residual spread was not exported.
    residual_std = float(config.get("residual_std", 1.09))
    band = float(config.get("band_multiplier", 0.8))
    range_low = channel_baseline * math.exp(log_m - band * residual_std)
    range_high = channel_baseline * math.exp(log_m + band * residual_std)

    timings = {name: round(ms, 1) for name, ms in timer.ms.items()}
    timings["total"] = round(sum(timer.ms.values()), 1)
    logger.info(
        "forecast_timing channel=%s cache=%s history_videos=%d used_channel_context=%s "
        "encode_ms=%.1f channel_ms=%.1f catboost_ms=%.1f history_ms=%.1f histattn_ms=%.1f total_ms=%.1f",
        channel_id, cache_status, history_videos, used_channel_context,
        timings["encode"], timings["channel"], timings["catboost"],
        timings["history"], timings["histattn"], timings["total"],
    )

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
        "based_on_videos": len(channel_videos),
        "warnings": warnings,
        "used_channel_context": used_channel_context,
        "log_m_catboost": log_m_catboost,
        "log_m_histattn": log_m_histattn,
        "ensemble_weight": state.ensemble_weight if used_channel_context else 0.0,
        "history_cache": cache_status,
        "history_videos": history_videos,
        "timings_ms": timings,
    }
