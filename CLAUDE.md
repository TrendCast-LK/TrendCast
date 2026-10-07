# trendcast-githubactions

## Database (Supabase / PostgreSQL)

The schema is owned by the backend and lives in [backend/schema/](backend/schema/):

- `init/` builds a fresh database: [01_schema.sql](backend/schema/init/01_schema.sql) (core tables and the `channel_stats_enriched` view), [02_archive_and_switch_channels.sql](backend/schema/init/02_archive_and_switch_channels.sql) (`channel_stats_archive`, `videos_archive`, `view_timeseries_archive`: snapshots of rows before a channel-set rotation, mirroring the core tables plus `archive_id`/`archived_at`), [03_app_backend.sql](backend/schema/init/03_app_backend.sql) (`users`/`predictions`/`notifications`, see "App-layer tables" below) [04_video_features.sql](backend/schema/init/04_video_features.sql) (cached title/thumbnail embeddings), [05_channel_history_cache.sql](backend/schema/init/05_channel_history_cache.sql) (the forecast ensemble's channel history cache, see below) and [06_admin.sql](backend/schema/init/06_admin.sql) (admin dashboard accounts and audit log, see below).
- `migrations/` holds idempotent changes for a database that already exists: `002` video metadata columns, `003` the `notifications.type` CHECK constraint, `004` row-level security on every table, `005` the channel history cache tables, `006` the admin dashboard (`users.is_active`, `admins`, `admin_audit_log`). Each mirrors a change already made in `init/`, which stays the source of truth for a fresh database.

Init scripts only auto-apply to a fresh local Postgres container. Against the live Supabase DB, apply them or a migration manually, e.g. `psql "$SUPABASE_DB_URL" -f backend/schema/migrations/004_enable_row_level_security.sql`.

Every table has row-level security enabled with no policies, so Supabase's REST API (anon/authenticated keys) can read nothing. The backend connects directly as the database owner, which bypasses it. How the schema and data are tested, and how to check the live database, is in [backend/tests/DB_TESTING.md](backend/tests/DB_TESTING.md).

### Connection pattern

- Connection string comes from the `SUPABASE_DB_URL` environment variable (a standard Postgres connection URL).
- Bulk writes use `psycopg2.extras.execute_batch` with named-parameter SQL templates (`%(name)s`), followed by an explicit `conn.commit()`.
- The FastAPI backend in `backend/` uses **its own Supabase database**, separate from the one the data-collection pipeline wrote to. It reads `SUPABASE_DB_URL` from `backend/.env` and connects through [backend/db.py](backend/db.py): a `ThreadedConnectionPool` (FastAPI runs sync endpoints on threads) with a semaphore so bursts wait for a free connection instead of failing. That database holds the full schema above, including the pipeline tables that `/channels` and `/videos` read.
- The pool (`db.KeepIdlePool`) keeps every connection it has opened: psycopg2's pool closes returned connections beyond `minconn`, and opening one against the remote Supabase pooler can take tens of seconds. `db.warm_pool()` opens a few in the background at startup. Admin endpoints are written as one SQL statement per read (JSON-aggregated) to keep round trips down.

### Core tables

These were written by a YouTube data-collection pipeline (ETL) that has been removed from this repo. The tables and the read-only `/channels` and `/videos` endpoints remain, but nothing populates them any more.

**`channel_stats`** — one row per YouTube channel (PK: `channel_id`, format `UCxxxxxxxxxxxxxxxxxxxxxx`).
- `channel_title`, `channel_description`, `published_at` (channel creation time), `country` (ISO 3166-1 alpha-2)
- `total_views`, `subscriber_count` (both `BIGINT`, channels can exceed 2^31), `video_count`
- `processed_at` — last successful extraction timestamp; `created_at` — first insert time
- Extension columns (added via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`): `title` (backfilled copy of `channel_title`), `tier_category`, `uploads_playlist_id`, `last_checked_at`
- Checks: `total_views`, `subscriber_count`, `video_count` all `>= 0`
- Indexes: `subscriber_count DESC`, `processed_at DESC`, `total_views DESC`, `country`, `last_checked_at DESC`

**`videos`** — polling queue / status per video (PK: `video_id`).
- `channel_id` FK → `channel_stats.channel_id` (`ON DELETE CASCADE`)
- `published_at`, `status` (`active` | `archived` | `deleted`, default `active`)
- `last_polled_at`, `next_poll_at` — drive the polling queue
- `current_interval_hours` (`NUMERIC(5,2)`, must be `> 0`) — current polling cadence for this video
- Indexes: `next_poll_at`, `(status, next_poll_at)` for queue picks, `channel_id`

**`view_timeseries`** — raw metric snapshots, one row per poll (PK: `id BIGSERIAL`).
- `video_id` FK → `videos.video_id` (`ON DELETE CASCADE`)
- `scraped_at`, `view_count`, `like_count`, `comment_count` (all `>= 0`)
- Indexes: `(video_id, scraped_at DESC)`, `scraped_at DESC`

**`channel_stats_enriched`** (VIEW, not a table) — wraps `channel_stats` with computed engagement KPIs:
- `avg_views_per_video` = `total_views / video_count`
- `views_per_subscriber` = `total_views / subscriber_count`
- `engagement_ratio` = `(subscriber_count / total_views) * 100` (%)
- `size_tier` — categorical bucket from `subscriber_count`: Micro (<1K), Small (1K–10K), Mid (10K–100K), Large (100K–1M), Mega (1M+)
- `channel_age_days` — days since `published_at`
- All ratio calculations are divide-by-zero guarded (`CASE WHEN ... > 0`)

### App-layer tables (backend/)

Added by `03_app_backend.sql`, owned by the FastAPI backend. Plain `BIGSERIAL` PKs, no UUIDs.

**`users`** — one row per app account (email/password auth, JWT issued on login).
- `full_name`, `email` (unique), `password_hash` (bcrypt)
- `subscribers`, `monthly_views` (`BIGINT`, self-reported baseline used as prediction context — editable in Settings, not scraped)
- `is_active` — FALSE once an admin disables the account: login and every authenticated request return 403 (login checks the password first)
- `channel_url` (pasted at signup), `channel_data` (`JSONB` snapshot fetched from the YouTube Data API — title, description, thumbnail_url, banner_url, country, published_at, subscriber_count, view_count, video_count, subscriber_hidden, channel_id, fetched_at), `channel_fetch_error`
- Kept separate from `channel_stats`: that table held the removed ETL's tracked forecasting-dataset channels, not a per-user profile cache. The forecast does not read `channel_stats`: `backend/inference.py` fetches the channel's recent history from the YouTube API using `channel_data.channel_id`, and `/predictions` returns 400 when the user has no linked channel.
- The forecast is a CatBoost + HistAttnV2 ensemble loaded from `ensemble_artifacts/` (see `backend/inference.py`, `backend/histattn.py`). `predictions.used_channel_context` is TRUE only when HistAttnV2 ran on cached channel history; FALSE means a CatBoost-only forecast (cache miss, e.g. right after signup). A failed channel refresh keeps the last good `channel_data` and only sets `channel_fetch_error`. `PUT /channel` changes the linked channel: it resolves the new URL first and returns 400 without touching the row if that fails; on success it replaces `channel_url`/`channel_data`, sets `subscribers` from the new channel (as signup does) and warms the new channel's history cache.

**`predictions`** — one row per saved/run prediction (PK: `id`, FK `user_id` → `users`, `ON DELETE CASCADE`).
- `title`, `category`, `tags` (`TEXT[]`), `target_date`, `target_time`, `thumbnail_path`/`dataset_path` (served from `/uploads`)
- `status` (`draft` | `complete`) — drafts skip the model call entirely
- `predicted_views`, `confidence` (heuristic, not a model output — see `backend/routers/predictions.py`), `change_vs_avg`, `trajectory` (`JSONB` curve), `v_inf`, `tau`, `used_channel_context`

**`channel_history_cache`** / **`channel_history_videos`** — added by `05_channel_history_cache.sql`, owned by [backend/channel_cache.py](backend/channel_cache.py). Keyed on YouTube `channel_id` (shared by every user linked to the channel), not on `users`.
- `channel_history_cache`: `encoder` (text encoder | image encoder | text template the embeddings came from; a mismatch reads as a miss), `warmed_at` (NULL until a warm succeeds; 24h TTL), `last_error`
- `channel_history_videos` (PK `(channel_id, video_id)`, FK → `channel_history_cache`, `ON DELETE CASCADE`): the channel's newest `max_hist` (20) uploads with `published_at`, `view_count`, `duration_s` and raw CLIP-512 `text_embedding` / `image_embedding` (`REAL[]`, image NULL when the thumbnail failed; 512-dim CHECKs)
- Warmed by a FastAPI background task from `routers/channel.refresh_user_channel` (signup and `/channel/refresh`) and re-warmed when a prediction finds the entry missing, stale or behind the channel's uploads. Warms are incremental: only new videos are encoded.

**`admins`** / **`admin_audit_log`** — added by `06_admin.sql`, owned by [backend/routers/admin.py](backend/routers/admin.py). Admins are separate accounts from `users`, created only with `python -m tools.create_admin` (from `backend/`); emails are stored lower-cased. Admin JWTs carry `aud=trendcast-admin` and last 8h; `get_current_user` rejects them and `get_current_admin` requires them, so neither token works on the other side. `get_current_admin` caches the admin row for 30s (`security.ADMIN_CACHE_SECONDS`), so disabling an admin via the CLI locks out an already-issued token within 30s. Every admin write inserts an `admin_audit_log` row (`action` e.g. `user.disable`, `target_type`/`target_id` with no FK so entries outlive deleted users, `details` JSONB) in the same transaction as the change.

**`notifications`** — one row per in-app notification (PK: `id`, FK `user_id` → `users`, `ON DELETE CASCADE`).
- `type` (`welcome` | `channel_fetch_success` | `channel_fetch_error` | `prediction_complete`, enforced by the `chk_notifications_type` CHECK constraint), `title`, `message`, `read`
