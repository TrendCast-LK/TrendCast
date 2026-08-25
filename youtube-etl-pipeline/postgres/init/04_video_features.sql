-- =============================================================================
-- Video Features Migration
-- File: postgres/init/04_video_features.sql
-- Purpose: Caches per-video title/thumbnail embeddings so weekly model
--          retraining doesn't have to re-embed every video from scratch.
--          Populated by youtube_extractor/embed_new_videos.py, which reuses
--          ml/services/title_embedding.py and ml/services/thumbnail_embedding.py
--          so the cached vectors match training-time embeddings exactly.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- =============================================================================
-- TABLE: video_features
-- One row per video, added once its title/thumbnail have been embedded.
-- =============================================================================
CREATE TABLE IF NOT EXISTS video_features (
    video_id                VARCHAR(64)     PRIMARY KEY,

    -- Raw (pre-PCA) LaBSE title embedding — sentence-transformers/LaBSE output dim
    title_embedding          vector(768),

    -- Raw (pre-PCA) CLIP thumbnail embedding — openai/clip-vit-base-patch32 projection dim
    thumbnail_embedding      vector(512),

    computed_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_video_features_video
        FOREIGN KEY (video_id)
        REFERENCES videos (video_id)
        ON DELETE CASCADE
);

-- Same pattern as the app-layer tables in 03_app_backend.sql: the backend and
-- ETL jobs connect with a direct Postgres connection (not anon/authenticated
-- Supabase keys), so RLS here is a defense-in-depth default that doesn't
-- affect either of them.
ALTER TABLE video_features ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE video_features IS
    'Cached title/thumbnail embeddings per video, populated by youtube_extractor/embed_new_videos.py so weekly model retraining does not need to re-embed every video from scratch.';

COMMENT ON COLUMN video_features.title_embedding IS
    'Raw (pre-PCA) LaBSE embedding computed by ml/services/title_embedding.py.';

COMMENT ON COLUMN video_features.thumbnail_embedding IS
    'Raw (pre-PCA) CLIP image embedding computed by ml/services/thumbnail_embedding.py.';
