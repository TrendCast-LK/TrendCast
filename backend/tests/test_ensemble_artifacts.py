"""Deploy sanity test for the CatBoost + HistAttnV2 forecast ensemble.

Loads the exported artifacts fresh from ensemble_artifacts/ through
inference.load_artifacts() and replays the reference cases the export scripts
wrote, rebuilding each case's inputs from the training corpus through the
serving code (feature assembly, PCA, scaler, history arrays), not the training
scripts' arrays:

1. CatBoost alone: reference_predictions.json (deployed full-corpus model).
2. HistAttnV2 alone: histattn_reference_predictions.json.
3. Blend arithmetic: expected_log_m_ensemble, with w from histattn_config.json.

The ensemble reference's CatBoost term comes from export_histattn_v2.py's
validation-split CatBoost, not the deployed catboost_magnitude.cbm, so the
blended number is checked as arithmetic on the reference terms rather than
end-to-end. The end-to-end tests at the bottom run run_forecast_on_image with
real encoders and a fake channel (no network, no database).

Needs the corpus in artifacts/ (training_corpus_h7_final.csv,
text_embeddings.npy, image_embeddings_final.npy); skipped without it.
"""

import json
import math
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import BACKEND_DIR, REPO_ROOT
from test_api_function import _real_inference

pytest.importorskip("torch")
pd = pytest.importorskip("pandas")

ENSEMBLE_DIR = REPO_ROOT / "ensemble_artifacts"
CORPUS_DIR = REPO_ROOT / "artifacts"
CORPUS_FILES = ["training_corpus_h7_final.csv", "text_embeddings.npy", "image_embeddings_final.npy"]

CATBOOST_ATOL = 1e-6   # same model file, same features: only float noise
PCA_ATOL = 1e-5        # one-row vs whole-corpus-batch float32 PCA rounding
# CatBoost bins each float against fixed split borders, so PCA rounding noise
# of ~1e-6 can flip a branch for a value sitting on a border: on the 20
# reference cases that moves log(m) by up to ~0.012 (about 1% of views),
# against a residual_std of ~1.56.
CATBOOST_SERVING_DRIFT = 0.05
HISTATTN_ATOL = 1e-4   # float32 network; training may have run on another device


def _load_json(name):
    with (ENSEMBLE_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def inf():
    module = _real_inference()
    assert module.ARTIFACTS_DIR == ENSEMBLE_DIR
    module.load_artifacts()
    state = module.get_state()
    assert state.ready, state.error
    return module


@pytest.fixture(scope="module")
def histattn_module(inf):
    import histattn
    return histattn


# ---------------------------------------------------------------------------
# Loaded state: nothing refit, nothing left in training mode
# ---------------------------------------------------------------------------

def test_histattn_is_in_eval_mode_with_every_dropout_off(inf):
    model = inf.get_state().histattn_model
    assert model.training is False
    assert all(not m.training for m in model.modules())


def test_ensemble_weight_and_dims_come_from_the_exported_config(inf):
    state, cfg = inf.get_state(), _load_json("histattn_config.json")
    assert state.ensemble_weight == cfg["ensemble_weight"]
    assert state.max_hist == cfg["max_hist"]
    assert state.histattn_model.tab[0].in_features == cfg["n_tab"] == len(state.histattn_tab_columns)


def test_scaler_and_pca_are_the_exported_objects_not_refit(inf):
    import pickle

    import joblib

    state = inf.get_state()
    with (ENSEMBLE_DIR / "histattn_scaler.pkl").open("rb") as fh:
        scaler = pickle.load(fh)
    np.testing.assert_array_equal(state.histattn_scaler.mean_, scaler.mean_)
    np.testing.assert_array_equal(state.histattn_scaler.scale_, scaler.scale_)
    np.testing.assert_array_equal(
        state.pca_text.components_, joblib.load(ENSEMBLE_DIR / "pca_text.pkl").components_
    )


def test_histattn_tab_columns_are_catboost_columns_without_the_pca_block(inf):
    state = inf.get_state()
    expected = [c for c in state.feature_columns if not re.match(r"(text|img)_pc\d+$", c)]
    assert state.histattn_tab_columns == expected


def test_histattn_forward_is_deterministic(inf, histattn_module):
    state = inf.get_state()
    rng = np.random.RandomState(0)
    history = (
        rng.randn(1, state.max_hist, 1024).astype(np.float32),
        rng.randn(1, state.max_hist, 3).astype(np.float32),
        np.ones((1, state.max_hist), dtype=np.float32),
    )
    args = (state.histattn_model, rng.randn(30), rng.randn(1024), history)
    assert histattn_module.predict_log_m(*args) == histattn_module.predict_log_m(*args)


# ---------------------------------------------------------------------------
# Reference cases, rebuilt from the training corpus
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def corpus(inf):
    missing = [f for f in CORPUS_FILES if not (CORPUS_DIR / f).exists()]
    if missing:
        pytest.skip(f"training corpus not available in artifacts/: {missing}")
    df = pd.read_csv(CORPUS_DIR / "training_corpus_h7_final.csv").copy()
    text = np.load(CORPUS_DIR / "text_embeddings.npy").astype(np.float32)
    image = np.load(CORPUS_DIR / "image_embeddings_final.npy").astype(np.float32)
    assert len(df) == len(text) == len(image)

    df["published_at"] = pd.to_datetime(df["published_at"], utc=True)
    df["day_7_views"] = df["day_7_views"].astype(float)
    # Channel statistics over the whole corpus, as both export scripts compute them.
    grouped = df.groupby("channel_id")
    df["channel_video_count"] = grouped["id"].transform("count")
    df["channel_median_views"] = grouped["day_7_views"].transform("median")
    df["channel_view_std"] = grouped["day_7_views"].transform("std")
    df["channel_log_volatility"] = grouped["day_7_views"].transform(
        lambda x: np.std(np.log(x.clip(lower=1)))
    )
    df["channel_category_diversity"] = grouped["category_id"].transform("nunique")
    df = df.copy()  # defragment after the column inserts
    row_of = {str(v): i for i, v in enumerate(df["id"].astype(str))}
    # PCA over the whole corpus in one float32 batch, bit-for-bit as
    # export_artifacts.py fed CatBoost; see test_serving_pca_matches_training_pca
    # for the one-row path a request takes.
    state = inf.get_state()
    return SimpleNamespace(
        df=df, text=text, image=image, row_of=row_of,
        text_pca=state.pca_text.transform(text), image_pca=state.pca_image.transform(image),
    )


def _count_tags(tags):
    if not isinstance(tags, str):
        return 0
    try:
        parsed = json.loads(tags)
        return len(parsed) if isinstance(parsed, list) else 0
    except Exception:  # noqa: BLE001
        return tags.count(",") + 1 if tags.strip() else 0


def _image_or_none(corpus, i):
    img = corpus.image[i]
    return img if np.linalg.norm(img) > 1e-6 else None


def _feature_vector(inf, corpus, i, text_pca=None, image_pca=None):
    """The CatBoost row for corpus row i, assembled by the serving code."""
    state = inf.get_state()
    row = corpus.df.iloc[i]
    text, img = corpus.text[i], corpus.image[i]
    has_thumbnail = int(np.linalg.norm(img) > 1e-6)
    duration_s = inf._parse_duration_seconds(row["video_duration"])
    return inf._build_feature_vector(
        state,
        duration_s=0.0 if math.isnan(duration_s) else duration_s,  # training: fillna(0)
        title_length=len("" if pd.isna(row["title"]) else row["title"]),
        description_length=len("" if pd.isna(row["description"]) else row["description"]),
        tag_count=_count_tags(row["tags"]),
        publish_time=row["published_at"].to_pydatetime(),
        # training: cosine of the raw vectors, 0 for a zero (missing) image
        thumbnail_alignment=inf._cosine_similarity(img, text),
        has_thumbnail=has_thumbnail,
        # CatBoost trained on PCA of the corpus vectors as stored (zeros for a
        # missing thumbnail), so replay those rather than the serving-time
        # mean-embedding fallback
        text_pca=corpus.text_pca[i] if text_pca is None else text_pca,
        image_pca=corpus.image_pca[i] if image_pca is None else image_pca,
        category_id=int(row["category_id"]),
        channel_video_count=row["channel_video_count"],
        channel_median_views=row["channel_median_views"],
        channel_view_std=0.0 if pd.isna(row["channel_view_std"]) else row["channel_view_std"],
        channel_log_volatility=row["channel_log_volatility"],
        channel_category_diversity=row["channel_category_diversity"],
    )


def _history(inf, histattn, corpus, i):
    """HistAttnV2's history arrays for corpus row i: every earlier video of the
    same channel with its true day-7 count, through the serving builder."""
    state = inf.get_state()
    df = corpus.df
    target = df.iloc[i]
    same_channel = np.flatnonzero((df["channel_id"] == target["channel_id"]).values)
    videos = []
    for j in same_channel:
        if j == i:
            continue
        dur = inf._parse_duration_seconds(df.iloc[j]["video_duration"])
        videos.append({
            "published_at": df.iloc[j]["published_at"].to_pydatetime(),
            "day7_views": float(df.iloc[j]["day_7_views"]),
            "duration_s": 0.0 if math.isnan(dur) else dur,
            "embedding": histattn.joint_embedding(corpus.text[j], _image_or_none(corpus, j)),
        })
    return histattn.build_history_arrays(
        videos, target["published_at"].to_pydatetime(), float(target["S"]),
        state.maturation_curve, state.max_hist,
    )


def test_catboost_reproduces_reference_predictions(inf, corpus):
    state = inf.get_state()
    refs = _load_json("reference_predictions.json")
    assert refs
    for ref in refs:
        i = corpus.row_of[ref["video_id"]]
        got = float(state.magnitude_model.predict(_feature_vector(inf, corpus, i).reshape(1, -1))[0])
        assert got == pytest.approx(ref["expected_log_m"], abs=CATBOOST_ATOL), ref["video_id"]


def test_serving_pca_matches_training_pca(inf, corpus):
    """The one-row path encode_target() takes lands within float noise of the
    batch values, and that noise moves CatBoost's log(m) only slightly."""
    state = inf.get_state()
    drift = {}
    for ref in _load_json("reference_predictions.json"):
        i = corpus.row_of[ref["video_id"]]
        text_pca = state.pca_text.transform(inf._prepare_for_pca(corpus.text[i]).reshape(1, -1))[0]
        image_pca = state.pca_image.transform(inf._prepare_for_pca(corpus.image[i]).reshape(1, -1))[0]
        np.testing.assert_allclose(text_pca, corpus.text_pca[i], atol=PCA_ATOL)
        np.testing.assert_allclose(image_pca, corpus.image_pca[i], atol=PCA_ATOL)
        row = _feature_vector(inf, corpus, i, text_pca=text_pca, image_pca=image_pca)
        drift[ref["video_id"]] = abs(float(state.magnitude_model.predict(row.reshape(1, -1))[0]) - ref["expected_log_m"])
    assert max(drift.values()) < CATBOOST_SERVING_DRIFT, drift


def test_histattn_reproduces_reference_predictions(inf, histattn_module, corpus):
    state = inf.get_state()
    refs = _load_json("histattn_reference_predictions.json")
    assert refs
    errors = {}
    for ref in refs:
        i = corpus.row_of[ref["video_id"]]
        history = _history(inf, histattn_module, corpus, i)
        if history is None:  # a channel's first video: training attended over nothing
            history = (np.zeros((1, state.max_hist, 1024), np.float32),
                       np.zeros((1, state.max_hist, 3), np.float32),
                       np.zeros((1, state.max_hist), np.float32))
        got = histattn_module.predict_log_m(
            state.histattn_model,
            inf.histattn_tab_features(state, _feature_vector(inf, corpus, i)),
            histattn_module.joint_embedding(corpus.text[i], _image_or_none(corpus, i)),
            history,
        )
        errors[ref["video_id"]] = abs(got - ref["expected_log_m_histattn"])
    assert max(errors.values()) < HISTATTN_ATOL, errors


def test_blend_matches_reference_ensemble_with_the_configured_weight(inf):
    state = inf.get_state()
    assert state.ensemble_weight == _load_json("histattn_config.json")["ensemble_weight"]
    for ref in _load_json("histattn_reference_predictions.json"):
        got = inf.blend_log_m(state, ref["expected_log_m_catboost"], ref["expected_log_m_histattn"])
        assert got == pytest.approx(ref["expected_log_m_ensemble"], abs=1e-12)
    assert inf.blend_log_m(state, 0.7, None) == 0.7  # no history: CatBoost alone


# ---------------------------------------------------------------------------
# End to end: run_forecast_on_image with real encoders and a fake channel
# ---------------------------------------------------------------------------

CHANNEL_ID = "UCtesttesttesttesttest00"
NOW = datetime.now(timezone.utc)


def _channel_videos(n=25):
    rng = np.random.RandomState(1)
    return [
        {
            "video_id": f"v{k:02d}", "published_at": NOW - timedelta(days=2 * k + 1),
            "view_count": int(rng.randint(500, 5000)), "category_id": 22,
            "title": f"video {k}", "tags": [], "thumbnail_url": None, "duration_s": 300.0,
        }
        for k in range(n)
    ]


def _cached(videos, fresh=True):
    rng = np.random.RandomState(2)
    rows = [
        {**v, "text_embedding": rng.randn(512).tolist(), "image_embedding": rng.randn(512).tolist()}
        for v in videos
    ]
    return SimpleNamespace(videos=rows, fresh=fresh)


@pytest.fixture
def forecast(inf, monkeypatch):
    from PIL import Image

    videos = _channel_videos()
    monkeypatch.setattr(inf, "_fetch_channel_history", lambda channel_id, **kw: videos)
    image = Image.new("RGB", (320, 180), (200, 40, 40))
    scheduled = []

    def run(loader, **overrides):
        kwargs = dict(tags=["travel"], duration="PT8M", category_id=22)
        kwargs.update(overrides)
        return inf.run_forecast_on_image(
            inf.get_state(), title="Sri Lanka travel vlog", image=image,
            scheduled_upload_time=NOW + timedelta(days=1), channel_id=CHANNEL_ID,
            history_loader=loader, schedule_warm=scheduled.append, **kwargs,
        )

    return SimpleNamespace(run=run, videos=videos, scheduled=scheduled)


def test_cache_miss_falls_back_to_catboost_and_schedules_a_warm(inf, forecast):
    result = forecast.run(lambda cid: None)
    assert result["used_channel_context"] is False
    assert result["history_cache"] == "miss" and result["log_m_histattn"] is None
    assert forecast.scheduled == [CHANNEL_ID]
    cfg = inf.get_state().config
    expected = min(max(result["log_m_catboost"], cfg["log_m_min"]), cfg["log_m_max"])
    assert math.log(result["multiplier"]) == pytest.approx(expected)


def test_cache_read_failure_falls_back_instead_of_failing(forecast):
    def broken(cid):
        raise RuntimeError("database unavailable")

    result = forecast.run(broken)
    assert result["used_channel_context"] is False and result["history_cache"] == "error"
    assert forecast.scheduled == [CHANNEL_ID]


def test_cache_hit_runs_the_ensemble_without_scheduling_a_warm(inf, forecast, caplog):
    import logging

    caplog.set_level(logging.INFO)
    result = forecast.run(lambda cid: _cached(forecast.videos))
    state = inf.get_state()
    assert result["used_channel_context"] is True and result["history_cache"] == "hit"
    assert result["history_videos"] == state.max_hist
    assert forecast.scheduled == []
    expected = inf.blend_log_m(state, result["log_m_catboost"], result["log_m_histattn"])
    expected = min(max(expected, state.config["log_m_min"]), state.config["log_m_max"])
    assert math.log(result["multiplier"]) == pytest.approx(expected)
    assert set(result["timings_ms"]) == {"encode", "channel", "catboost", "history", "histattn", "total"}
    assert any("forecast_timing" in r.getMessage() for r in caplog.records)


def test_stale_or_behind_cache_is_used_and_rewarmed(forecast):
    stale = forecast.run(lambda cid: _cached(forecast.videos, fresh=False))
    assert stale["used_channel_context"] is True and stale["history_cache"] == "stale"

    behind = forecast.run(lambda cid: _cached(forecast.videos[1:]))  # newest upload not cached yet
    assert behind["used_channel_context"] is True and behind["history_cache"] == "stale"
    assert forecast.scheduled == [CHANNEL_ID, CHANNEL_ID]


def test_the_target_is_encoded_once(inf, forecast, monkeypatch):
    calls = {"text": 0, "image": 0}
    real_texts, real_images = inf.encode_texts, inf.encode_images

    def count(kind, fn):
        def wrapper(*args, **kwargs):
            calls[kind] += 1
            return fn(*args, **kwargs)
        return wrapper

    monkeypatch.setattr(inf, "encode_texts", count("text", real_texts))
    monkeypatch.setattr(inf, "encode_images", count("image", real_images))
    forecast.run(lambda cid: _cached(forecast.videos))
    assert calls == {"text": 1, "image": 1}


def test_older_channel_metadata_does_not_keep_rewarming_a_newer_cache(forecast):
    """The request's channel metadata can be 6h older than the cache. A video
    that has since dropped out of the cached newest set is not a new upload."""
    newer = {**forecast.videos[0], "video_id": "brand-new", "published_at": NOW - timedelta(hours=1)}
    result = forecast.run(lambda cid: _cached([newer, *forecast.videos[:19]]))
    assert result["history_cache"] == "hit" and forecast.scheduled == []


@pytest.mark.parametrize("missing", [{"duration": None}, {"duration": "not-iso"}, {"category_id": None, "tags": []}])
def test_optional_form_fields_left_blank_still_give_a_finite_ensemble_forecast(forecast, missing):
    """Training zero-filled missing features; a NaN reaching HistAttnV2 made the
    whole forecast NaN (POST /predictions 500, 'cannot convert float NaN')."""
    result = forecast.run(lambda cid: _cached(forecast.videos), **missing)
    assert result["used_channel_context"] is True
    assert math.isfinite(result["log_m_histattn"]) and math.isfinite(result["multiplier"])
    assert all(math.isfinite(p["views"]) for p in result["curve"])
