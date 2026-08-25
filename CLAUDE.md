# trendcast-githubactions

## Database (Supabase / PostgreSQL)

Schema is defined in [youtube-etl-pipeline/postgres/init/01_schema.sql](youtube-etl-pipeline/postgres/init/01_schema.sql), applied automatically on first container start. A second migration, [02_archive_and_switch_channels.sql](youtube-etl-pipeline/postgres/init/02_archive_and_switch_channels.sql), adds `channel_stats_archive`, `videos_archive`, and `view_timeseries_archive` tables that snapshot rows before a channel-set rotation — mirror copies of the core tables below plus `archive_id`/`archived_at`. A third, [03_app_backend.sql](youtube-etl-pipeline/postgres/init/03_app_backend.sql), adds the `users`/`predictions`/`notifications` tables backing the FastAPI app layer (see "App-layer tables" below). These init scripts only auto-apply to a fresh local Postgres container — against the live Supabase DB they must be applied manually (e.g. `psql "$SUPABASE_DB_URL" -f path/to/migration.sql`).

### Connection pattern

- Connection string comes from the `SUPABASE_DB_URL` environment variable (a standard Postgres connection URL).
- ETL jobs connect with plain `psycopg2.connect(db_url)` — see [youtube_extractor/job2_timeseries_collector.py](youtube-etl-pipeline/youtube_extractor/job2_timeseries_collector.py) `main()`.
- Bulk writes use `psycopg2.extras.execute_batch` with named-parameter SQL templates (`%(name)s`), followed by an explicit `conn.commit()`.
- When writing to multiple tables that Job 1 (channel ingestion) also touches, rows are sorted deterministically by primary key (e.g. `video_id`) before the batch write to avoid Postgres deadlocks between concurrently running jobs.
- A **new FastAPI backend is being built in `backend/`** that will read from this same Supabase database — reuse `SUPABASE_DB_URL` and the same connection pattern rather than introducing a second DB config convention.

### Core tables

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

Added by `03_app_backend.sql`, owned by the FastAPI backend (not the ETL pipeline). Plain `BIGSERIAL` PKs, no UUIDs.

**`users`** — one row per app account (email/password auth, JWT issued on login).
- `full_name`, `email` (unique), `password_hash` (bcrypt)
- `subscribers`, `monthly_views` (`BIGINT`, self-reported baseline used as prediction context — editable in Settings, not scraped)
- `channel_url` (pasted at signup), `channel_data` (`JSONB` snapshot fetched from the YouTube Data API — title, description, thumbnail_url, banner_url, country, published_at, subscriber_count, view_count, video_count, subscriber_hidden, channel_id, fetched_at), `channel_fetch_error`
- Kept separate from `channel_stats`: that table is the ETL's tracked forecasting-dataset channels, not a per-user profile cache. If a user's resolved `channel_data.channel_id` happens to also exist in `channel_stats`, `/predictions` picks up real channel context for the model; otherwise it falls back to dataset-wide medians (see `backend/inference.py`'s `get_channel_stats`).

**`predictions`** — one row per saved/run prediction (PK: `id`, FK `user_id` → `users`, `ON DELETE CASCADE`).
- `title`, `category`, `tags` (`TEXT[]`), `target_date`, `target_time`, `thumbnail_path`/`dataset_path` (served from `/uploads`)
- `status` (`draft` | `complete`) — drafts skip the model call entirely
- `predicted_views`, `confidence` (heuristic, not a model output — see `backend/routers/predictions.py`), `change_vs_avg`, `trajectory` (`JSONB` curve), `v_inf`, `tau`, `used_channel_context`

**`notifications`** — one row per in-app notification (PK: `id`, FK `user_id` → `users`, `ON DELETE CASCADE`).
- `type` (`welcome` | `channel_fetch_success` | `channel_fetch_error` | `prediction_complete`), `title`, `message`, `read`

### Polling cadence (Job 2)

`job2_timeseries_collector.py` polls due videos every 5 minutes and adjusts `current_interval_hours` by video age (`select_interval_hours`):
- age ≤ 1h → poll every ~5 min
- age 1–2h → poll every 15 min
- age > 2h → poll every 1 hour

Videos missing from the YouTube API response (deleted/privatized) are flagged `status = 'deleted'` with `next_poll_at = NULL` so they drop out of the queue. Note `videos.status` is `VARCHAR(16)` — keep any new status values within that limit.
