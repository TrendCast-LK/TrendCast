from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from psycopg2.extras import Json

from channel_cache import schedule_warm
from db import get_cursor
from models import ChangeChannelRequest, ChannelOut
from routers.notifications import create_notification
from security import get_current_user
from youtube import YouTubeResolutionError, resolve_channel

router = APIRouter(prefix="/channel", tags=["channel"])

UPDATE_CHANNEL_SQL = """
    UPDATE users
    SET channel_data = COALESCE(%(channel_data)s, channel_data),
        channel_fetch_error = %(channel_fetch_error)s
    WHERE id = %(user_id)s
"""


def refresh_user_channel(
    user_id: int, channel_url: str, background_tasks: BackgroundTasks | None = None
) -> tuple[dict | None, str | None]:
    """Resolves channel_url via the YouTube API, persists the result on the
    user row, and writes a channel_fetch_success/error notification. Returns
    (channel_data, fetch_error) - exactly one is non-None. A failed fetch
    records the error but keeps the last good snapshot already on the user.

    On success, also queues a warm of the forecast's channel history cache
    (channel_cache.py) on background_tasks, so it runs after the response is
    sent. Every channel_url resolution goes through here (signup and
    /channel/refresh), which makes it the one trigger for cache warming."""
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

    if channel_data and channel_data.get("channel_id") and background_tasks is not None:
        schedule_warm(background_tasks, channel_data["channel_id"])

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

# Unlike a refresh, a change only lands once the new URL resolves: a typo must
# not replace a working channel with a broken one. subscribers follows the new
# channel, as it does at signup.
CHANGE_CHANNEL_SQL = """
    UPDATE users
    SET channel_url = %(channel_url)s,
        channel_data = %(channel_data)s,
        channel_fetch_error = NULL,
        subscribers = COALESCE(%(subscribers)s, subscribers)
    WHERE id = %(user_id)s
"""


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


@router.put("", response_model=ChannelOut)
def change_my_channel(
    request: ChangeChannelRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    """Points the account at a different YouTube channel. Forecasts use the new
    channel from the next prediction on; its history cache warms in the
    background, so the first forecasts may be CatBoost-only."""
    channel_url = request.channel_url.strip()
    try:
        channel_data = resolve_channel(channel_url)
    except YouTubeResolutionError as exc:
        raise HTTPException(status_code=400, detail=f"Couldn't use that channel: {exc}") from exc

    with get_cursor(commit=True) as cur:
        cur.execute(
            CHANGE_CHANNEL_SQL,
            {
                "channel_url": channel_url,
                "channel_data": Json(channel_data),
                "subscribers": channel_data.get("subscriber_count"),
                "user_id": user["id"],
            },
        )

    create_notification(
        user["id"],
        "channel_fetch_success",
        "Channel changed",
        f"Your forecasts now use {channel_data.get('title') or 'your new channel'}.",
    )
    if channel_data.get("channel_id"):
        schedule_warm(background_tasks, channel_data["channel_id"])
    return channel_out(channel_url, channel_data, None)


@router.post("/refresh", response_model=ChannelOut)
def refresh_my_channel(background_tasks: BackgroundTasks, user: dict = Depends(get_current_user)):
    channel_data, fetch_error = refresh_user_channel(
        user["id"], user.get("channel_url") or "", background_tasks
    )
    # on failure the previous snapshot is still stored, so keep showing it
    return channel_out(user.get("channel_url"), channel_data or user.get("channel_data"), fetch_error)
