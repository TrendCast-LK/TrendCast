-- =============================================================================
-- App Backend Migration
-- File: postgres/init/03_app_backend.sql
-- Purpose: Adds the tables backing the FastAPI backend's user-facing app
--          (accounts, saved predictions, notifications) on top of the
--          ETL-owned channel/video/timeseries tables from 01_schema.sql.
-- =============================================================================

-- =============================================================================
-- TABLE: users
-- One row per app account. `channel_data` is a JSONB snapshot fetched from
-- the YouTube Data API for the channel URL given at signup (title,
-- description, thumbnail_url, banner_url, country, published_at,
-- subscriber_count, view_count, video_count, subscriber_hidden, channel_id,
-- fetched_at) - kept separate from the ETL's channel_stats table since it's a
-- per-user profile snapshot, not a tracked forecasting-dataset channel.
-- =============================================================================
CREATE TABLE IF NOT EXISTS users (
    id                      BIGSERIAL       PRIMARY KEY,
    full_name               VARCHAR(255)    NOT NULL,
    email                   VARCHAR(255)    NOT NULL UNIQUE,
    password_hash           VARCHAR(255)    NOT NULL,

    -- Self-reported baseline metrics used as prediction context (Settings page)
    subscribers             BIGINT          NOT NULL DEFAULT 0,
    monthly_views           BIGINT          NOT NULL DEFAULT 0,

    channel_url             TEXT,
    channel_data            JSONB,
    channel_fetch_error     TEXT,

    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_users_subscribers_positive      CHECK (subscribers >= 0),
    CONSTRAINT chk_users_monthly_views_positive    CHECK (monthly_views >= 0)
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);

COMMENT ON TABLE users IS
    'App accounts for the FastAPI backend (auth, per-user channel binding, predictions, notifications).';
COMMENT ON COLUMN users.channel_data IS
    'JSONB snapshot fetched from the YouTube Data API for channel_url at signup / refresh.';

-- =============================================================================
-- TABLE: predictions
-- One row per saved/run prediction, owned by a user.
-- =============================================================================
CREATE TABLE IF NOT EXISTS predictions (
    id                      BIGSERIAL       PRIMARY KEY,
    user_id                 BIGINT          NOT NULL,

    title                   VARCHAR(255)    NOT NULL,
    category                VARCHAR(128),
    tags                    TEXT[]          NOT NULL DEFAULT '{}',
    target_date             DATE,
    target_time             VARCHAR(8),

    thumbnail_path          TEXT,
    dataset_path            TEXT,

    status                  VARCHAR(16)     NOT NULL DEFAULT 'draft',

    predicted_views         BIGINT,
    confidence              DOUBLE PRECISION,
    change_vs_avg           DOUBLE PRECISION,
    trajectory              JSONB,
    v_inf                   DOUBLE PRECISION,
    tau                     DOUBLE PRECISION,
    used_channel_context    BOOLEAN,

    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_predictions_user
        FOREIGN KEY (user_id)
        REFERENCES users (id)
        ON DELETE CASCADE,

    CONSTRAINT chk_predictions_status
        CHECK (status IN ('draft', 'complete'))
);

CREATE INDEX IF NOT EXISTS idx_predictions_user_created
    ON predictions (user_id, created_at DESC);

COMMENT ON TABLE predictions IS
    'Saved/run predictions created via POST /predictions, owned by a user.';

-- =============================================================================
-- TABLE: notifications
-- One row per in-app notification, owned by a user.
-- =============================================================================
CREATE TABLE IF NOT EXISTS notifications (
    id                      BIGSERIAL       PRIMARY KEY,
    user_id                 BIGINT          NOT NULL,

    type                    VARCHAR(32)     NOT NULL,
    title                   VARCHAR(255)    NOT NULL,
    message                 TEXT            NOT NULL,
    read                    BOOLEAN         NOT NULL DEFAULT FALSE,

    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_notifications_user
        FOREIGN KEY (user_id)
        REFERENCES users (id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_notifications_user_created
    ON notifications (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_notifications_user_unread
    ON notifications (user_id) WHERE NOT read;

COMMENT ON TABLE notifications IS
    'In-app notifications (welcome, channel fetch results, prediction completion) owned by a user.';
