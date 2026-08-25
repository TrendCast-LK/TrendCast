"""Resolves a pasted YouTube channel URL to a channel snapshot via the YouTube
Data API v3, using the single YOUTUBE_API_KEY already set in backend/.env.

This is intentionally lighter than youtube-etl-pipeline/youtube_extractor's
APIKeyPool (multi-key rotation for bulk polling) - per-user signup/refresh
lookups here are low-volume enough that one key is fine.
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from config import YOUTUBE_API_KEY

API_BASE = "https://www.googleapis.com/youtube/v3"
REQUEST_TIMEOUT_SECONDS = 10.0
CHANNEL_PARTS = "snippet,statistics,brandingSettings"


class YouTubeResolutionError(Exception):
    """Raised when a channel URL can't be resolved to a real YouTube channel."""


def _get(path: str, **params) -> dict:
    params["key"] = YOUTUBE_API_KEY
    try:
        response = requests.get(f"{API_BASE}/{path}", params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise YouTubeResolutionError(f"could not reach the YouTube API: {exc}") from exc

    if response.status_code != 200:
        raise YouTubeResolutionError(f"YouTube API returned HTTP {response.status_code}")
    return response.json()


def _locator_from_url(channel_url: str) -> tuple[str, str]:
    """Returns (kind, value): kind is one of "id" | "handle" | "username" | "query"."""
    parsed = urlparse(channel_url.strip())
    path = parsed.path if parsed.netloc else channel_url.strip()
    segments = [s for s in path.split("/") if s]

    if not segments:
        raise YouTubeResolutionError("that doesn't look like a channel URL")

    if segments[0].startswith("@"):
        return "handle", segments[0][1:]
    if segments[0] == "channel" and len(segments) > 1:
        return "id", segments[1]
    if segments[0] == "user" and len(segments) > 1:
        return "username", segments[1]
    if segments[0] == "c" and len(segments) > 1:
        return "query", segments[1]

    # Bare handle/name pasted without a full URL.
    if segments[0].startswith("@"):
        return "handle", segments[0][1:]
    return "query", segments[0]


def _lookup_channel(**params) -> dict | None:
    data = _get("channels", part=CHANNEL_PARTS, **params)
    items = data.get("items") or []
    return items[0] if items else None


def _search_channel_id(query: str) -> str | None:
    data = _get("search", part="snippet", type="channel", maxResults=1, q=query)
    items = data.get("items") or []
    if not items:
        return None
    return items[0]["id"]["channelId"]


def _resolve_channel_json(channel_url: str) -> dict:
    kind, value = _locator_from_url(channel_url)

    channel = None
    if kind == "id":
        channel = _lookup_channel(id=value)
    elif kind == "handle":
        channel = _lookup_channel(forHandle=f"@{value}")
    elif kind == "username":
        channel = _lookup_channel(forUsername=value)

    if channel is None:
        # Custom "/c/Name" URLs and legacy usernames that forUsername/forHandle
        # can't resolve directly both fall back to a channel search.
        channel_id = _search_channel_id(value)
        if channel_id:
            channel = _lookup_channel(id=channel_id)

    if channel is None:
        raise YouTubeResolutionError(f"couldn't find a YouTube channel for {channel_url!r}")

    return channel


def _snapshot_from_channel_json(channel: dict) -> dict:
    snippet = channel.get("snippet", {})
    statistics = channel.get("statistics", {})
    branding = channel.get("brandingSettings", {}).get("image", {})
    thumbnails = snippet.get("thumbnails", {})
    thumbnail = thumbnails.get("high") or thumbnails.get("medium") or thumbnails.get("default") or {}

    return {
        "channel_id": channel.get("id"),
        "title": snippet.get("title"),
        "description": snippet.get("description"),
        "country": snippet.get("country"),
        "published_at": snippet.get("publishedAt"),
        "thumbnail_url": thumbnail.get("url"),
        "banner_url": branding.get("bannerExternalUrl"),
        "subscriber_count": int(statistics["subscriberCount"]) if "subscriberCount" in statistics else None,
        "view_count": int(statistics["viewCount"]) if "viewCount" in statistics else None,
        "video_count": int(statistics["videoCount"]) if "videoCount" in statistics else None,
        "subscriber_hidden": bool(statistics.get("hiddenSubscriberCount", False)),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def resolve_channel(channel_url: str) -> dict:
    """Returns a channel snapshot dict (see _snapshot_from_channel_json).
    Raises YouTubeResolutionError on any failure - callers treat that as
    non-fatal and store the message as channel_fetch_error."""
    channel_json = _resolve_channel_json(channel_url)
    return _snapshot_from_channel_json(channel_json)
