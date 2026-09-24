"""The backend, wired for load testing. Start it with:

    uvicorn stubbed_app:app --app-dir backend/tests/load --port 8100 [--workers N]

What changes compared with `uvicorn main:app`:

* the database is LOAD_TEST_DB_URL (backend/.env is never used; the safety checks in
  load_config.py apply), and JWTs use LOAD_JWT_SECRET so the load test can sign tokens;
* the YouTube API is replaced by an instant in-process fake (signup, /channel/refresh and the
  channel history behind /predictions), so no quota is spent and no network is involved;
* uploads go to a temporary directory instead of backend/uploads;
* the forecast model is stubbed by default (LOAD_MODEL=stub) with a configurable cost, so the
  API and database can be measured without loading torch/CatBoost. LOAD_MODEL=real runs the real
  model (needs the artifacts and ~25 s to start); only the YouTube calls are still faked.

Environment:
    LOAD_MODEL             stub | real                (default stub)
    LOAD_STUB_MODEL_MS     cost of one stubbed forecast, milliseconds   (default 800)
    LOAD_STUB_MODEL_MODE   sleep | cpu                (default cpu: busy-loops, like real inference)
    LOAD_STUB_YOUTUBE_MS   latency of each faked YouTube call           (default 120)
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import load_config as lc  # noqa: E402

sys.path.insert(0, str(lc.BACKEND_DIR))

_url = lc.require_test_db_url(allow_remote=os.environ.get("LOAD_ALLOW_REMOTE") == "1")
# Set before config.py is imported: load_dotenv() does not override variables that already exist,
# so a real backend/.env cannot win over these.
os.environ["SUPABASE_DB_URL"] = _url
os.environ["JWT_SECRET_KEY"] = lc.JWT_SECRET
os.environ["YOUTUBE_API_KEY"] = "load-test-key-not-real"

MODEL_MODE = os.environ.get("LOAD_MODEL", "stub").lower()
STUB_MODEL_MS = float(os.environ.get("LOAD_STUB_MODEL_MS", 800))
STUB_MODEL_KIND = os.environ.get("LOAD_STUB_MODEL_MODE", "cpu").lower()
STUB_YOUTUBE_MS = float(os.environ.get("LOAD_STUB_YOUTUBE_MS", 120))


def _spend(ms: float, kind: str = "sleep") -> None:
    """Burns `ms` of wall time: sleeping, or spinning to hold the GIL like CPU-bound inference."""
    if ms <= 0:
        return
    if kind == "sleep":
        time.sleep(ms / 1000)
        return
    end = time.perf_counter() + ms / 1000
    x = 1.0
    while time.perf_counter() < end:
        x = (x * 1.0000001) % 1000.0


FAKE_FORECAST = {
    "point_estimate_7d": 10_000.0,
    "range_7d": {"low": 8_000.0, "high": 14_000.0},
    "channel_baseline": 8_000.0,
    "curve": [{"day": d, "views": d * 1_000} for d in range(1, 8)],
    "shape_params": {"t0": 2.5},
    "used_channel_context": True,
}


def _fake_channel_snapshot(channel_url: str) -> dict:
    _spend(STUB_YOUTUBE_MS)
    return {
        "channel_id": "UCLOADUSER00000000000001",
        "title": "Load Test Channel",
        "description": "Stubbed for load testing",
        "country": "LK",
        "published_at": "2020-01-01T00:00:00Z",
        "thumbnail_url": "https://img.example/t.jpg",
        "banner_url": None,
        "subscriber_count": 12345,
        "view_count": 999999,
        "video_count": 42,
        "subscriber_hidden": False,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _fake_history(channel_id: str) -> list[dict]:
    _spend(STUB_YOUTUBE_MS * 3)  # the real call is three requests
    now = datetime.now(timezone.utc)
    return [
        {"video_id": f"h{i}", "published_at": now - timedelta(days=7 * (i + 1)),
         "view_count": 5000 + i * 100, "category_id": 22}
        for i in range(30)
    ]


def _install_stub_inference() -> None:
    stub = types.ModuleType("inference")
    stub.ChannelNotFoundError = type("ChannelNotFoundError", (ValueError,), {})
    stub.InsufficientHistoryError = type("InsufficientHistoryError", (ValueError,), {})
    stub.QuotaExceededError = type("QuotaExceededError", (RuntimeError,), {})
    stub.ThumbnailDownloadError = type("ThumbnailDownloadError", (RuntimeError,), {})
    state = types.SimpleNamespace(ready=True, error=None, device="stub", load_time_seconds=0.0)
    stub.get_state = lambda: state
    stub.load_artifacts = lambda: None

    def run_forecast_on_image(state, **kwargs):
        _fake_history(kwargs.get("channel_id"))  # same call order as the real pipeline
        _spend(STUB_MODEL_MS, STUB_MODEL_KIND)
        return dict(FAKE_FORECAST)

    stub.run_forecast_on_image = run_forecast_on_image
    stub.run_forecast = lambda state, **kwargs: run_forecast_on_image(state, **kwargs)
    sys.modules["inference"] = stub


# uploads -> a throwaway directory. Must happen before main.py runs ensure_uploads_dir()/mounts it.
import storage  # noqa: E402

UPLOADS_TMP = Path(tempfile.mkdtemp(prefix="trendcast-load-uploads-"))
storage.UPLOADS_DIR = UPLOADS_TMP

if MODEL_MODE == "stub":
    _install_stub_inference()
elif MODEL_MODE == "real":
    import inference  # noqa: E402

    inference._fetch_channel_history = _fake_history  # no YouTube quota, no network
else:
    raise SystemExit("LOAD_MODEL must be 'stub' or 'real'")

import config  # noqa: E402

assert config.SUPABASE_DB_URL == _url, "backend is not pointed at LOAD_TEST_DB_URL"

from routers import channel as channel_router  # noqa: E402

channel_router.resolve_channel = _fake_channel_snapshot

from main import app  # noqa: E402

# Lets smoke_check.py / locustfile.py / run_load_tests.ps1 confirm they are talking to THIS app and not,
# say, a normal dev server running against the real database.
@app.get("/__loadtest__", include_in_schema=False)
def _loadtest_marker():
    return {"loadtest_stub": True, "model": MODEL_MODE}


print(f"[load] database={lc.describe(_url)} model={MODEL_MODE} uploads={UPLOADS_TMP}", flush=True)
