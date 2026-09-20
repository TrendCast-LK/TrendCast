-- =============================================================================
-- Archive & Switch Channels Migration
-- File: backend/schema/init/02_archive_and_switch_channels.sql
-- Purpose: Creates archive tables to preserve historical data before
--          switching the pipeline to a new channel seed set.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. Archive Tables — mirror structure of active tables + archived_at column
-- ---------------------------------------------------------------------------

-- 1a. channel_stats_archive
CREATE TABLE IF NOT EXISTS channel_stats_archive (
    archive_id              BIGSERIAL       PRIMARY KEY,
    archived_at             TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    -- Original columns from channel_stats
    channel_id              VARCHAR(64)     NOT NULL,
    channel_title           VARCHAR(255)    NOT NULL,
    channel_description     TEXT,
    published_at            TIMESTAMPTZ,
    country                 VARCHAR(10),
    total_views             BIGINT          NOT NULL DEFAULT 0,
    subscriber_count        BIGINT          NOT NULL DEFAULT 0,
    video_count             INTEGER         NOT NULL DEFAULT 0,
    processed_at            TIMESTAMPTZ,
    created_at              TIMESTAMPTZ,
    title                   VARCHAR(255),
    tier_category           VARCHAR(64),
    uploads_playlist_id     VARCHAR(64),
    last_checked_at         TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_channel_stats_archive_archived
    ON channel_stats_archive (archived_at DESC);

CREATE INDEX IF NOT EXISTS idx_channel_stats_archive_channel
    ON channel_stats_archive (channel_id);

COMMENT ON TABLE channel_stats_archive IS
    'Snapshot archive of channel_stats rows preserved before a channel-set rotation.';


-- 1b. videos_archive
CREATE TABLE IF NOT EXISTS videos_archive (
    archive_id              BIGSERIAL       PRIMARY KEY,
    archived_at             TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    -- Original columns from videos
    video_id                VARCHAR(64)     NOT NULL,
    channel_id              VARCHAR(64)     NOT NULL,
    published_at            TIMESTAMPTZ     NOT NULL,
    status                  VARCHAR(16)     NOT NULL DEFAULT 'active',
    last_polled_at          TIMESTAMPTZ,
    next_poll_at            TIMESTAMPTZ,
    current_interval_hours  INTEGER         NOT NULL DEFAULT 6,
    created_at              TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_videos_archive_archived
    ON videos_archive (archived_at DESC);

CREATE INDEX IF NOT EXISTS idx_videos_archive_video
    ON videos_archive (video_id);

COMMENT ON TABLE videos_archive IS
    'Snapshot archive of video polling-queue rows preserved before a channel-set rotation.';


-- 1c. view_timeseries_archive
CREATE TABLE IF NOT EXISTS view_timeseries_archive (
    archive_id              BIGSERIAL       PRIMARY KEY,
    archived_at             TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    -- Original columns from view_timeseries
    original_id             BIGINT,
    video_id                VARCHAR(64)     NOT NULL,
    scraped_at              TIMESTAMPTZ     NOT NULL,
    view_count              BIGINT          NOT NULL DEFAULT 0,
    like_count              BIGINT          NOT NULL DEFAULT 0,
    comment_count           BIGINT          NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_view_ts_archive_archived
    ON view_timeseries_archive (archived_at DESC);

CREATE INDEX IF NOT EXISTS idx_view_ts_archive_video
    ON view_timeseries_archive (video_id);

CREATE INDEX IF NOT EXISTS idx_view_ts_archive_scraped
    ON view_timeseries_archive (video_id, scraped_at DESC);

COMMENT ON TABLE view_timeseries_archive IS
    'Snapshot archive of view_timeseries metric rows preserved before a channel-set rotation.';

-- Row-level security. Supabase exposes every public-schema table through its REST
-- API to the anon/authenticated keys unless RLS is on; with RLS on and no
-- policies those roles see nothing. The backend and scripts connect directly as
-- the database owner (bypasses RLS), so they are unaffected.
ALTER TABLE channel_stats_archive   ENABLE ROW LEVEL SECURITY;
ALTER TABLE videos_archive          ENABLE ROW LEVEL SECURITY;
ALTER TABLE view_timeseries_archive ENABLE ROW LEVEL SECURITY;
