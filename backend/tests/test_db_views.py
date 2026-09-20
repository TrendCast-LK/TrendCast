"""Layer D: derived-data tests for the channel_stats_enriched view.

Channels are inserted with known numbers and the view's output is compared
to hand-calculated values.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import db_helpers as h


def enriched(cur, channel_id="UC001") -> dict:
    cur.execute("SELECT * FROM channel_stats_enriched WHERE channel_id = %s", (channel_id,))
    row = cur.fetchone()
    return dict(zip([c.name for c in cur.description], row))


# ---------------------------------------------------------------------------
# KPI arithmetic
# ---------------------------------------------------------------------------

def test_kpis_match_hand_calculated_values(cur):
    # 1,000,000 / 200 = 5000; 1,000,000 / 25,000 = 40; 25,000 / 1,000,000 * 100 = 2.5%
    h.channel(cur, total_views=1_000_000, subscriber_count=25_000, video_count=200)
    row = enriched(cur)
    assert row["avg_views_per_video"] == Decimal("5000.00")
    assert row["views_per_subscriber"] == Decimal("40.0000")
    assert row["engagement_ratio"] == Decimal("2.500000")


def test_kpis_are_rounded_to_documented_precision(cur):
    # 10 / 3 = 3.3333...; 3 / 10 * 100 = 30
    h.channel(cur, total_views=10, subscriber_count=3, video_count=3)
    row = enriched(cur)
    assert row["avg_views_per_video"] == Decimal("3.33")       # 2 decimals
    assert row["views_per_subscriber"] == Decimal("3.3333")    # 4 decimals
    assert row["engagement_ratio"] == Decimal("30.000000")     # 6 decimals


def test_kpis_handle_counts_beyond_int32(cur):
    big = 2**31 + 1_000
    h.channel(cur, total_views=big * 10, subscriber_count=big, video_count=10)
    row = enriched(cur)
    assert row["views_per_subscriber"] == Decimal("10.0000")
    assert row["engagement_ratio"] == Decimal("10.000000")


ZERO_GUARDS = [
    pytest.param(dict(total_views=500, subscriber_count=50, video_count=0), "avg_views_per_video", id="no-videos"),
    pytest.param(dict(total_views=500, subscriber_count=0, video_count=5), "views_per_subscriber", id="no-subscribers"),
    pytest.param(dict(total_views=0, subscriber_count=50, video_count=5), "engagement_ratio", id="no-views"),
]


@pytest.mark.parametrize("values, kpi", ZERO_GUARDS)
def test_division_by_zero_is_guarded(cur, values, kpi):
    h.channel(cur, **values)
    assert enriched(cur)[kpi] == 0


def test_all_zero_channel_yields_zero_kpis_not_an_error(cur):
    h.channel(cur, total_views=0, subscriber_count=0, video_count=0)
    row = enriched(cur)
    assert (row["avg_views_per_video"], row["views_per_subscriber"], row["engagement_ratio"]) == (0, 0, 0)


# ---------------------------------------------------------------------------
# size_tier
# ---------------------------------------------------------------------------

TIER_BOUNDARIES = [
    (0, "Micro (<1K)"),
    (999, "Micro (<1K)"),
    (1_000, "Small (1K–10K)"),
    (9_999, "Small (1K–10K)"),
    (10_000, "Mid (10K–100K)"),
    (99_999, "Mid (10K–100K)"),
    (100_000, "Large (100K–1M)"),
    (999_999, "Large (100K–1M)"),
    (1_000_000, "Mega (1M+)"),
    (250_000_000, "Mega (1M+)"),
]


@pytest.mark.parametrize("subscribers, tier", TIER_BOUNDARIES)
def test_size_tier_boundaries(cur, subscribers, tier):
    h.channel(cur, subscriber_count=subscribers)
    assert enriched(cur)["size_tier"] == tier


def test_size_tier_follows_updates(cur):
    h.channel(cur, subscriber_count=500)
    assert enriched(cur)["size_tier"] == "Micro (<1K)"
    cur.execute("UPDATE channel_stats SET subscriber_count = 2_000_000 WHERE channel_id = 'UC001'")
    assert enriched(cur)["size_tier"] == "Mega (1M+)"


# ---------------------------------------------------------------------------
# tier_category (mode of the channel's video category_id)
# ---------------------------------------------------------------------------

def _videos(cur, categories, channel_id="UC001"):
    for i, category in enumerate(categories):
        h.video(cur, video_id=f"{channel_id}-v{i}", channel_id=channel_id, category_id=category)


def test_tier_category_is_the_most_common_category(cur):
    _videos(cur, ["10", "20", "20", "20", "10"])
    assert enriched(cur)["tier_category"] == "20"


def test_tier_category_tie_goes_to_lowest_category_id(cur):
    _videos(cur, ["27", "10", "27", "10"])
    assert enriched(cur)["tier_category"] == "10"


def test_tier_category_is_null_without_videos(cur):
    h.channel(cur)
    assert enriched(cur)["tier_category"] is None


def test_tier_category_is_null_when_no_video_is_categorised(cur):
    _videos(cur, [None, None])
    assert enriched(cur)["tier_category"] is None


def test_tier_category_ignores_uncategorised_videos(cur):
    _videos(cur, [None, None, None, "22"])
    assert enriched(cur)["tier_category"] == "22"


def test_tier_category_is_computed_per_channel(cur):
    _videos(cur, ["10", "10"], channel_id="UC_a")
    _videos(cur, ["20", "20"], channel_id="UC_b")
    assert enriched(cur, "UC_a")["tier_category"] == "10"
    assert enriched(cur, "UC_b")["tier_category"] == "20"


def test_tier_category_recomputes_as_videos_arrive(cur):
    _videos(cur, ["10"])
    assert enriched(cur)["tier_category"] == "10"
    for i in range(2):
        h.video(cur, video_id=f"late{i}", category_id="20")
    assert enriched(cur)["tier_category"] == "20"


# ---------------------------------------------------------------------------
# channel_age_days and pass-through columns
# ---------------------------------------------------------------------------

def test_channel_age_days_counts_whole_days_since_creation(cur):
    published = datetime.now(timezone.utc) - timedelta(days=10, hours=1)
    h.channel(cur, published_at=published)
    assert enriched(cur)["channel_age_days"] == 10


def test_channel_age_days_is_null_when_creation_date_unknown(cur):
    h.channel(cur, published_at=None)
    assert enriched(cur)["channel_age_days"] is None


def test_view_passes_through_channel_columns(cur):
    h.channel(cur, channel_description="About", country="LK", total_views=7)
    row = enriched(cur)
    assert (row["channel_title"], row["channel_description"], row["country"], row["total_views"]) == (
        "Test Channel", "About", "LK", 7,
    )


def test_view_has_exactly_one_row_per_channel(cur):
    for i in range(3):
        h.channel(cur, channel_id=f"UC{i}")
    _videos(cur, ["10", "20", "20"], channel_id="UC0")
    cur.execute("SELECT COUNT(*), COUNT(DISTINCT channel_id) FROM channel_stats_enriched")
    assert cur.fetchone() == (3, 3)
