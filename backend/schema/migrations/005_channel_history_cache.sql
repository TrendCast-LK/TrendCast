-- =============================================================================
-- Migration: Channel history cache for the HistAttnV2 forecast ensemble
-- File: backend/schema/migrations/005_channel_history_cache.sql
-- Run manually against the live Supabase database (idempotent — safe to
-- re-run). Mirrors backend/schema/init/02_channel_history_cache.sql, which
-- remains the source of truth for a fresh DB init.
--
-- Until this is applied the backend still serves forecasts: reading the
-- missing cache is logged and every forecast falls back to CatBoost-only.
-- =============================================================================
CREATE TABLE IF NOT EXISTS channel_history_cache (
    channel_id              VARCHAR(64)     PRIMARY KEY,
    encoder                 TEXT,
    warmed_at               TIMESTAMPTZ,
    last_error              TEXT,
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS channel_history_videos (
    channel_id              VARCHAR(64)     NOT NULL,
    video_id                VARCHAR(64)     NOT NULL,
    published_at            TIMESTAMPTZ     NOT NULL,
    view_count              BIGINT          NOT NULL,
    duration_s              DOUBLE PRECISION,
    text_embedding          REAL[]          NOT NULL,
    image_embedding         REAL[],
    encoded_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_channel_history_videos
        PRIMARY KEY (channel_id, video_id),
    CONSTRAINT fk_channel_history_videos_channel
        FOREIGN KEY (channel_id)
        REFERENCES channel_history_cache (channel_id)
        ON DELETE CASCADE,
    CONSTRAINT chk_channel_history_videos_view_count
        CHECK (view_count >= 0),
    CONSTRAINT chk_channel_history_videos_text_dim
        CHECK (array_length(text_embedding, 1) = 512),
    CONSTRAINT chk_channel_history_videos_image_dim
        CHECK (image_embedding IS NULL OR array_length(image_embedding, 1) = 512)
);

COMMENT ON TABLE channel_history_cache IS
    'Per-channel warm state for the HistAttnV2 channel history cache (backend/channel_cache.py).';
COMMENT ON TABLE channel_history_videos IS
    'Recent uploads of a cached channel with raw CLIP-512 text/image embeddings for HistAttnV2.';

ALTER TABLE channel_history_cache  ENABLE ROW LEVEL SECURITY;
ALTER TABLE channel_history_videos ENABLE ROW LEVEL SECURITY;
