import logging
import threading
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import channel_cache
from db import get_cursor, warm_pool
from inference import ThumbnailDownloadError, get_state, load_artifacts, run_forecast
from models import ForecastRequest, ForecastResponse
from routers import admin, auth, channel, dashboard, notifications, predictions, trends
from storage import UPLOADS_DIR, ensure_uploads_dir


# INFO so the per-stage forecast_timing / channel_warm lines are visible
# (uvicorn configures only its own loggers).
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per Hugging Face / API request otherwise


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Opening DB connections overlaps the ~30s model load instead of landing
    # on the first requests.
    threading.Thread(target=warm_pool, daemon=True, name="warm-db-pool").start()
    load_artifacts()
    yield


ensure_uploads_dir()  # StaticFiles below requires the directory to exist at mount time

app = FastAPI(title="Trendcast API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite dev server
        "http://localhost:5174",  # Vite fallback when 5173 is taken
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        # TODO: add the production frontend URL here once it's deployed
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")

app.include_router(auth.router)
app.include_router(channel.router)
app.include_router(notifications.router)
app.include_router(dashboard.router)
app.include_router(trends.router)
app.include_router(predictions.router)
app.include_router(admin.router)


@app.get("/health")
def health():
    with get_cursor() as cur:
        cur.execute("SELECT 1")
        cur.fetchone()
    return {"status": "ok", "db": "connected"}


@app.get("/forecast/health")
def forecast_health():
    state = get_state()
    return {
        "ready": state.ready,
        "error": state.error,
        "device": state.device,
        "load_time_seconds": state.load_time_seconds,
    }


@app.post("/forecast", response_model=ForecastResponse)
def forecast(request: ForecastRequest, background_tasks: BackgroundTasks):
    state = get_state()
    if not state.ready:
        raise HTTPException(
            status_code=503,
            detail=f"Forecast model artifacts are not available: {state.error}",
        )

    try:
        return run_forecast(
            state,
            title=request.title,
            thumbnail_url=request.thumbnail_url,
            scheduled_upload_time=request.scheduled_upload_time,
            channel_id=request.channel_id,
            history_loader=channel_cache.load_history,
            schedule_warm=lambda cid: channel_cache.schedule_warm(background_tasks, cid),
        )
    except ThumbnailDownloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
