-- =============================================================================
-- Migration: Enable row-level security on every public table
-- File: backend/schema/migrations/004_enable_row_level_security.sql
-- Run manually against the live Supabase database (idempotent — safe to
-- re-run). Mirrors the ALTER TABLE ... ENABLE ROW LEVEL SECURITY statements
-- now at the end of backend/schema/init/01_app_backend.sql, which remains the
-- source of truth for a fresh DB init.
--
-- Why: Supabase exposes every public-schema table through its REST API to the
-- anon/authenticated keys unless RLS is on. Without it, anyone holding the
-- (public) anon key could read users.password_hash. With RLS on and no
-- policies, those roles can see nothing.
--
-- No effect on the backend or scripts: they connect directly as the database
-- owner (the `postgres` role), which bypasses RLS.
-- =============================================================================
ALTER TABLE users                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE predictions             ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications           ENABLE ROW LEVEL SECURITY;
