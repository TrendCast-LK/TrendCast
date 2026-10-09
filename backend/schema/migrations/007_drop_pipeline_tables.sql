-- =============================================================================
-- Migration: Drop the retired data-collection pipeline's tables
-- File: backend/schema/migrations/007_drop_pipeline_tables.sql
-- Run manually against the live Supabase database (idempotent — safe to
-- re-run). A fresh database built from backend/schema/init/ never has them.
--
-- The YouTube data-collection pipeline that filled these tables has been
-- removed, and nothing in the app reads them. Take a backup first if the
-- collected data should be kept.
-- =============================================================================
DROP VIEW  IF EXISTS channel_stats_enriched;
DROP TABLE IF EXISTS video_features;
DROP TABLE IF EXISTS view_timeseries;
DROP TABLE IF EXISTS videos;
DROP TABLE IF EXISTS channel_stats;
DROP TABLE IF EXISTS view_timeseries_archive;
DROP TABLE IF EXISTS videos_archive;
DROP TABLE IF EXISTS channel_stats_archive;
