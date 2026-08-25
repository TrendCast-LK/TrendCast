from fastapi import APIRouter, Depends
from psycopg2.extras import Json

from db import get_cursor
from models import ChannelOut
from routers.notifications import create_notification
from security import get_current_user
from youtube import YouTubeResolutionError, resolve_channel

router = APIRouter(prefix="/channel", tags=["channel"])

UPDATE_CHANNEL_SQL = """
    UPDATE users
    SET channel_data = %(channel_data)s, channel_fetch_error = %(channel_fetch_error)s
    WHERE id = %(user_id)s
"""


def refresh_user_channel(user_id: int, channel_url: str) -> tuple[dict | None, str | None]:
    """Resolves channel_url via the YouTube API, persists the result on the
    user row, and writes a channel_fetch_success/error notification. Returns
    (channel_data, fetch_error) - exactly one is non-None."""
    channel_data: dict | None = None
    fetch_error: str | None = None

    try:
        channel_data = resolve_channel(channel_url)
    except YouTubeResolutionError as exc:
        fetch_error = str(exc)

    with get_cursor(commit=True) as cur:
        cur.execute(
            UPDATE_CHANNEL_SQL,
            {
                "channel_data": Json(channel_data) if channel_data is not None else None,
                "channel_fetch_error": fetch_error,
                "user_id": user_id,
            },
        )

    if channel_data:
        create_notification(
            user_id,
            "channel_fetch_success",
            "Channel connected",
            f"We pulled the latest stats for {channel_data.get('title') or 'your channel'}.",
        )
    else:
        create_notification(
            user_id,
            "channel_fetch_error",
            "Couldn't fetch your channel",
            fetch_error or "Something went wrong fetching your channel data.",
        )

    return channel_data, fetch_error


def channel_out(channel_url: str | None, channel_data: dict | None, fetch_error: str | None) -> ChannelOut:
    channel_data = channel_data or {}
    return ChannelOut(
        channel_id=channel_data.get("channel_id"),
        title=channel_data.get("title"),
        description=channel_data.get("description"),
        country=channel_data.get("country"),
        published_at=channel_data.get("published_at"),
        thumbnail_url=channel_data.get("thumbnail_url"),
        banner_url=channel_data.get("banner_url"),
        subscriber_count=channel_data.get("subscriber_count"),
        view_count=channel_data.get("view_count"),
        video_count=channel_data.get("video_count"),
        subscriber_hidden=bool(channel_data.get("subscriber_hidden", False)),
        fetched_at=channel_data.get("fetched_at"),
        channel_url=channel_url,
        fetch_error=fetch_error,
    )


@router.get("/me", response_model=ChannelOut)
def get_my_channel(user: dict = Depends(get_current_user)):
    return channel_out(user.get("channel_url"), user.get("channel_data"), user.get("channel_fetch_error"))


@router.post("/refresh", response_model=ChannelOut)
def refresh_my_channel(user: dict = Depends(get_current_user)):
    channel_data, fetch_error = refresh_user_channel(user["id"], user.get("channel_url") or "")
    return channel_out(user.get("channel_url"), channel_data, fetch_error)
