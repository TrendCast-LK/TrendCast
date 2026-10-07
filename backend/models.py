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
    thumbnail_url: Optional[str] = None
    scheduled_upload_time: Optional[datetime] = None
    channel_id: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    duration: Optional[str] = None


class ForecastRange(BaseModel):
    low: float
    high: float


class ForecastPoint(BaseModel):
    day: int
    views: int


class ForecastResponse(BaseModel):
    status: str = "ok"
    channel_baseline: Optional[float] = None
    multiplier: Optional[float] = None
    point_estimate_7d: Optional[float] = None
    range_7d: Optional[ForecastRange] = None
    curve: List[ForecastPoint]
    shape_family: Optional[str] = None
    day1_fraction: Optional[float] = None
    based_on_videos: Optional[int] = None
    warnings: List[str] = Field(default_factory=list)
    v_inf: Optional[float] = None
    tau: Optional[float] = None
    used_channel_context: Optional[bool] = None


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


class ChangeChannelRequest(BaseModel):
    channel_url: str = Field(min_length=1)


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


# ---- Admin dashboard ----------------------------------------------------------


class AdminOut(BaseModel):
    id: int
    full_name: str
    email: str
    last_login_at: Optional[datetime] = None


class AdminAuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    admin: AdminOut


class AdminUserCounts(BaseModel):
    total: int
    active: int
    disabled: int
    new_7d: int
    new_30d: int
    with_channel: int
    with_fetch_error: int


class AdminPredictionCounts(BaseModel):
    total: int
    complete: int
    draft: int
    last_7d: int


class AdminForecastSplit(BaseModel):
    """Complete predictions by model since `since` (None = all time)."""
    since: Optional[datetime] = None
    ensemble: int
    catboost_only: int


class AdminDailyPoint(BaseModel):
    day: date
    signups: int
    predictions: int
    ensemble: int
    catboost_only: int


class AdminSystemStatus(BaseModel):
    database: str = "ok"
    model_ready: bool
    model_error: Optional[str] = None
    model_device: Optional[str] = None
    model_load_time_seconds: Optional[float] = None


class AdminOverview(BaseModel):
    users: AdminUserCounts
    predictions: AdminPredictionCounts
    forecasts: AdminForecastSplit
    daily: List[AdminDailyPoint]
    system: AdminSystemStatus


class AdminUserRow(BaseModel):
    id: int
    full_name: str
    email: str
    is_active: bool
    created_at: datetime
    subscribers: int
    channel_url: Optional[str] = None
    channel_title: Optional[str] = None
    channel_thumbnail_url: Optional[str] = None
    has_fetch_error: bool
    prediction_count: int
    last_prediction_at: Optional[datetime] = None


class AdminUserList(BaseModel):
    total: int
    items: List[AdminUserRow]


class AdminPredictionRow(BaseModel):
    id: int
    title: str
    category: Optional[str] = None
    status: str
    predicted_views: Optional[int] = None
    confidence: Optional[float] = None
    used_channel_context: Optional[bool] = None
    created_at: datetime


class AdminAuditEntry(BaseModel):
    id: int
    admin_id: Optional[int] = None
    admin_email: Optional[str] = None
    action: str
    target_type: Optional[str] = None
    target_id: Optional[int] = None
    details: dict
    created_at: datetime


class AdminAuditList(BaseModel):
    total: int
    items: List[AdminAuditEntry]


class AdminUserDetail(BaseModel):
    id: int
    full_name: str
    email: str
    is_active: bool
    created_at: datetime
    subscribers: int
    monthly_views: int
    channel: ChannelOut
    prediction_count: int
    complete_count: int
    draft_count: int
    notification_count: int
    unread_notification_count: int
    recent_predictions: List[AdminPredictionRow]
    recent_activity: List[AdminAuditEntry]


class AdminDeleteUserRequest(BaseModel):
    # Must equal the user's email: a server-side guard against deleting the wrong row.
    confirm_email: str


class AdminPredictionListRow(AdminPredictionRow):
    user_id: int
    user_email: str
    user_name: str
    thumbnail_url: Optional[str] = None


class AdminHistogramBucket(BaseModel):
    low: float
    high: float
    count: int


class AdminCategoryCount(BaseModel):
    category: str
    count: int


class AdminPredictionSummary(BaseModel):
    """Over every prediction matching the filters, not just the page."""
    count: int
    complete: int
    avg_confidence: Optional[float] = None
    median_views: Optional[float] = None
    p90_views: Optional[float] = None
    views_histogram: List[AdminHistogramBucket]  # log10 buckets of complete predictions' views
    confidence_histogram: List[AdminHistogramBucket]  # tenths of 0-1, empty buckets included
    categories: List[AdminCategoryCount]


class AdminPredictionList(BaseModel):
    total: int
    items: List[AdminPredictionListRow]
    summary: AdminPredictionSummary
    all_categories: List[str]


class AdminPredictionDetail(PredictionOut):
    user_id: int
    user_email: str
    user_name: str
    dataset_url: Optional[str] = None


class AdminCacheRow(BaseModel):
    channel_id: str
    channel_title: Optional[str] = None
    linked_users: int
    has_entry: bool  # FALSE: a user links this channel but it was never warmed or failed before a row existed
    status: str  # fresh | stale | never
    encoder: Optional[str] = None
    encoder_matches: Optional[bool] = None  # None while the model isn't loaded
    warmed_at: Optional[datetime] = None
    last_error: Optional[str] = None
    updated_at: Optional[datetime] = None
    video_count: int
    newest_video_at: Optional[datetime] = None


class AdminCacheSummary(BaseModel):
    total: int
    fresh: int
    stale: int
    never: int
    with_error: int
    # Complete predictions in the last 7 days (and since the ensemble launch)
    # that found warm channel history, of all complete ones.
    hits_7d: int
    forecasts_7d: int


class AdminCacheList(BaseModel):
    total: int
    items: List[AdminCacheRow]
    summary: AdminCacheSummary
    model_ready: bool
    ttl_hours: float


class AdminWarmResult(BaseModel):
    queued: int
    channel_ids: List[str]
