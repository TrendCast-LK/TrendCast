-- =============================================================================
-- Channel History Cache
-- File: backend/schema/init/02_channel_history_cache.sql
-- Purpose: Pre-encoded channel history for the HistAttnV2 half of the forecast
--          ensemble (backend/channel_cache.py). Warmed in the background at
--          signup / channel refresh so a prediction reads embeddings instead
--          of downloading and encoding up to 20 thumbnails per request.
--          Owned by the FastAPI backend, keyed on the YouTube channel id (one
--          entry is shared by every user linked to that channel).
-- =============================================================================

-- =============================================================================
-- TABLE: channel_history_cache
-- One row per warmed channel. warmed_at is NULL until a warm succeeds.
-- =============================================================================
CREATE TABLE IF NOT EXISTS channel_history_cache (
    channel_id              VARCHAR(64)     PRIMARY KEY,

    -- text encoder | image encoder | text template the embeddings were made
    -- with; a mismatch with the loaded config means re-encode everything
    encoder                 TEXT,
    warmed_at               TIMESTAMPTZ,
    last_error              TEXT,

    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- TABLE: channel_history_videos
-- The channel's most recent uploads (up to max_hist) with raw CLIP embeddings.
-- =============================================================================
CREATE TABLE IF NOT EXISTS channel_history_videos (
    channel_id              VARCHAR(64)     NOT NULL,
    video_id                VARCHAR(64)     NOT NULL,

    published_at            TIMESTAMPTZ     NOT NULL,
    view_count              BIGINT          NOT NULL,
    duration_s              DOUBLE PRECISION,

    -- raw (pre-PCA, un-normalised) 512-dim CLIP embeddings; image is NULL when
    -- the thumbnail could not be downloaded
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

-- Same as every other table: RLS on with no policies, so Supabase's REST API
-- sees nothing; the backend connects as the owner and is unaffected.
ALTER TABLE channel_history_cache  ENABLE ROW LEVEL SECURITY;
ALTER TABLE channel_history_videos ENABLE ROW LEVEL SECURITY;
