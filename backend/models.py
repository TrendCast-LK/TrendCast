"""Pydantic response models mirroring database views/tables."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class ChannelStatsEnriched(BaseModel):
    channel_id: str
    channel_title: str
    channel_description: Optional[str] = None
    published_at: Optional[datetime] = None
    country: Optional[str] = None
    total_views: int
    subscriber_count: int
    video_count: int
    processed_at: datetime
    created_at: datetime
    avg_views_per_video: float
    views_per_subscriber: float
    engagement_ratio: float
    size_tier: str
    channel_age_days: Optional[int] = None


class Video(BaseModel):
    video_id: str
    channel_id: str
    published_at: datetime
    status: str
    last_polled_at: Optional[datetime] = None
    next_poll_at: Optional[datetime] = None
    current_interval_hours: float
    created_at: datetime


class ViewTimeseries(BaseModel):
    id: int
    video_id: str
    scraped_at: datetime
    view_count: int
    like_count: int
    comment_count: int


class ForecastRequest(BaseModel):
    title: str
    thumbnail_url: str
    scheduled_upload_time: datetime
    channel_id: Optional[str] = None


class ForecastPoint(BaseModel):
    day: int
    views: int


class ForecastResponse(BaseModel):
    curve: List[ForecastPoint]
    v_inf: float
    tau: float
    used_channel_context: bool
