-- =============================================================================
-- Migration: Admin dashboard
-- File: backend/schema/migrations/006_admin_dashboard.sql
-- Run manually against the live Supabase database (idempotent — safe to
-- re-run). Mirrors users.is_active in backend/schema/init/01_app_backend.sql
-- and backend/schema/init/03_admin.sql, which remain the source of truth for
-- a fresh DB init.
--
-- After applying, create the first admin from backend/:
--     python -m tools.create_admin --email you@example.com --name "Your Name"
-- =============================================================================
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

CREATE TABLE IF NOT EXISTS admins (
    id                      BIGSERIAL       PRIMARY KEY,
    full_name               VARCHAR(255)    NOT NULL,
    email                   VARCHAR(255)    NOT NULL UNIQUE,
    password_hash           VARCHAR(255)    NOT NULL,
    is_active               BOOLEAN         NOT NULL DEFAULT TRUE,
    last_login_at           TIMESTAMPTZ,
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_admins_email_lowercase CHECK (email = LOWER(email))
);

COMMENT ON TABLE admins IS
    'Admin dashboard accounts, separate from app users. Created via backend/tools/create_admin.py.';

CREATE TABLE IF NOT EXISTS admin_audit_log (
    id                      BIGSERIAL       PRIMARY KEY,
    admin_id                BIGINT,
    action                  VARCHAR(64)     NOT NULL,
    target_type             VARCHAR(32),
    target_id               BIGINT,
    details                 JSONB           NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_admin_audit_log_admin
        FOREIGN KEY (admin_id)
        REFERENCES admins (id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_admin_audit_log_created
    ON admin_audit_log (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_admin_audit_log_target
    ON admin_audit_log (target_type, target_id, created_at DESC);

COMMENT ON TABLE admin_audit_log IS
    'Audit trail of admin dashboard actions (login, disable/enable/delete user, channel refresh, ...).';

ALTER TABLE admins          ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin_audit_log ENABLE ROW LEVEL SECURITY;
