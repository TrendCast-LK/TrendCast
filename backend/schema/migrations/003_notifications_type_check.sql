-- =============================================================================
-- Migration: Enforce the allowed notifications.type values
-- File: backend/schema/migrations/003_notifications_type_check.sql
-- Run manually against the live Supabase database (idempotent — safe to
-- re-run). Mirrors the constraint now declared inline in
-- backend/schema/init/03_app_backend.sql, which remains the source of truth for a
-- fresh DB init.
--
-- Fails if existing rows hold a type outside the four values below; find them
-- first with:
--   SELECT DISTINCT type FROM notifications
--   WHERE type NOT IN ('welcome', 'channel_fetch_success',
--                      'channel_fetch_error', 'prediction_complete');
-- =============================================================================
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_notifications_type'
          AND conrelid = 'notifications'::regclass
    ) THEN
        ALTER TABLE notifications
            ADD CONSTRAINT chk_notifications_type
            CHECK (type IN ('welcome', 'channel_fetch_success', 'channel_fetch_error', 'prediction_complete'));
    END IF;
END $$;
