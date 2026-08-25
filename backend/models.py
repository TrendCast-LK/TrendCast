"""Pydantic response models mirroring database views/tables."""

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field


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


# ---- Auth / users ---------------------------------------------------------


class SignupRequest(BaseModel):
    full_name: str
    email: EmailStr
    password: str = Field(min_length=8)
    channel_url: str


class UpdateProfileRequest(BaseModel):
    full_name: Optional[str] = None
    subscribers: Optional[int] = Field(default=None, ge=0)
    monthly_views: Optional[int] = Field(default=None, ge=0)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class UserOut(BaseModel):
    id: int
    full_name: str
    email: str
    subscribers: int
    monthly_views: int
    channel_url: Optional[str] = None
    channel_thumbnail_url: Optional[str] = None


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ---- Channel ----------------------------------------------------------------


class ChannelOut(BaseModel):
    channel_id: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    country: Optional[str] = None
    published_at: Optional[str] = None
    thumbnail_url: Optional[str] = None
    banner_url: Optional[str] = None
    subscriber_count: Optional[int] = None
    view_count: Optional[int] = None
    video_count: Optional[int] = None
    subscriber_hidden: bool = False
    fetched_at: Optional[str] = None
    channel_url: Optional[str] = None
    fetch_error: Optional[str] = None


# ---- Notifications ------------------------------------------------------------


class NotificationOut(BaseModel):
    id: int
    type: str
    title: str
    message: str
    read: bool
    created_at: datetime


class NotificationsResponse(BaseModel):
    notifications: List[NotificationOut]
    unread_count: int


# ---- Dashboard / trends -----------------------------------------------------


class DashboardSummary(BaseModel):
    full_name: str
    subscribers: int
    monthly_views: int


class CategoryBreakdown(BaseModel):
    category: str
    count: int
    average_views: float


class TrendsTimelinePoint(BaseModel):
    title: str
    predicted_views: int


class TrendsSummary(BaseModel):
    total_predictions: int
    draft_predictions: int
    completed_predictions: int
    average_predicted_views: float
    average_confidence: Optional[float] = None
    best_category: Optional[str] = None
    timeline: List[TrendsTimelinePoint]
    category_breakdown: List[CategoryBreakdown]


# ---- Predictions ----------------------------------------------------------


class PredictionOut(BaseModel):
    id: int
    title: str
    category: Optional[str] = None
    tags: List[str]
    status: str
    target_date: Optional[date] = None
    target_time: Optional[str] = None
    thumbnail_url: Optional[str] = None
    predicted_views: Optional[int] = None
    confidence: Optional[float] = None
    change_vs_avg: Optional[float] = None
    trajectory: List[ForecastPoint] = Field(default_factory=list)
    v_inf: Optional[float] = None
    tau: Optional[float] = None
    used_channel_context: Optional[bool] = None
    created_at: datetime
