from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from db import get_cursor
from inference import ThumbnailDownloadError, get_state, load_artifacts, run_forecast
from models import (
    ChannelStatsEnriched,
    ForecastRequest,
    ForecastResponse,
    Video,
    ViewTimeseries,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_artifacts()
    yield


app = FastAPI(title="Trendcast API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite dev server
        # TODO: add the production frontend URL here once it's deployed
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TIMESERIES_BY_VIDEO_SQL = """
    SELECT
        id,
        video_id,
        scraped_at,
        view_count,
        like_count,
        comment_count
    FROM view_timeseries
    WHERE video_id = %(video_id)s
    ORDER BY scraped_at ASC
"""

VIDEOS_BY_CHANNEL_SQL = """
    SELECT
        video_id,
        channel_id,
        published_at,
        status,
        last_polled_at,
        next_poll_at,
        current_interval_hours,
        created_at
    FROM videos
    WHERE channel_id = %(channel_id)s
    ORDER BY published_at DESC
"""

CHANNELS_ENRICHED_SQL = """
    SELECT
        channel_id,
        channel_title,
        channel_description,
        published_at,
        country,
        total_views,
        subscriber_count,
        video_count,
        processed_at,
        created_at,
        avg_views_per_video,
        views_per_subscriber,
        engagement_ratio,
        size_tier,
        channel_age_days
    FROM channel_stats_enriched
"""


@app.get("/health")
def health():
    with get_cursor() as cur:
        cur.execute("SELECT 1")
        cur.fetchone()
    return {"status": "ok", "db": "connected"}


@app.get("/channels", response_model=List[ChannelStatsEnriched])
def get_channels():
    with get_cursor() as cur:
        cur.execute(CHANNELS_ENRICHED_SQL)
        columns = [col.name for col in cur.description]
        rows = cur.fetchall()
    return [dict(zip(columns, row)) for row in rows]


@app.get("/videos/{video_id}/timeseries", response_model=List[ViewTimeseries])
def get_video_timeseries(video_id: str):
    with get_cursor() as cur:
        cur.execute(TIMESERIES_BY_VIDEO_SQL, {"video_id": video_id})
        columns = [col.name for col in cur.description]
        rows = cur.fetchall()
    return [dict(zip(columns, row)) for row in rows]


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
def forecast(request: ForecastRequest):
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
        )
    except ThumbnailDownloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/channels/{channel_id}/videos", response_model=List[Video])
def get_channel_videos(channel_id: str):
    with get_cursor() as cur:
        cur.execute(VIDEOS_BY_CHANNEL_SQL, {"channel_id": channel_id})
        columns = [col.name for col in cur.description]
        rows = cur.fetchall()
    return [dict(zip(columns, row)) for row in rows]
