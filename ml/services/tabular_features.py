"""Pure feature-derivation functions shared by the offline training pipeline
(ml/features_simple.py) and the online inference path (backend/inference.py):
upload-time conversion, title-derived counts, duration parsing, and tag
counting. Every function here takes plain inputs and returns plain outputs
(no DataFrame, no DB, no model state), so it means exactly the same thing
whether it's called once per request or once per row of a training CSV.
"""

from __future__ import annotations

import ast
import re
from datetime import datetime, timedelta, timezone

import pandas as pd

SRI_LANKA_OFFSET = timedelta(hours=5, minutes=30)

_DURATION_RE = re.compile(
    r"^PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?$"
)


def title_char_length(title: str) -> int:
    return len(title)


def title_word_count(title: str) -> int:
    return len(title.split())


def compute_upload_timing(scheduled_at: datetime) -> tuple[int, int, bool]:
    """Converts a datetime to Sri Lanka time (UTC+5:30) and returns
    (upload_hour, upload_dayofweek, is_weekend). Naive datetimes are assumed
    UTC, matching how published_at is stored."""
    if scheduled_at.tzinfo is None:
        scheduled_utc = scheduled_at.replace(tzinfo=timezone.utc)
    else:
        scheduled_utc = scheduled_at.astimezone(timezone.utc)
    scheduled_slt = scheduled_utc + SRI_LANKA_OFFSET
    upload_hour = scheduled_slt.hour
    upload_dayofweek = scheduled_slt.weekday()  # Monday=0..Sunday=6, matches pandas .dt.dayofweek
    is_weekend = upload_dayofweek in (5, 6)
    return upload_hour, upload_dayofweek, is_weekend


def parse_iso8601_duration(value) -> float:
    """Parse an ISO 8601 duration (e.g. 'PT4M13S') into total seconds.

    Returns NaN for missing/malformed values, including a bare 'PT' with no
    components, which carries no actual duration information.
    """
    if pd.isna(value):
        return float("nan")
    match = _DURATION_RE.match(str(value).strip())
    if not match or not any(match.groups()):
        return float("nan")
    hours, minutes, seconds = match.groups()
    return int(hours or 0) * 3600 + int(minutes or 0) * 60 + float(seconds or 0)


def count_tags(value) -> int:
    """Number of tags. 0 for null/empty; tags are stored as a stringified
    Python list (e.g. "['a', 'b']") in the CSV."""
    if pd.isna(value):
        return 0
    try:
        tags = ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return 0
    return len(tags) if tags else 0
