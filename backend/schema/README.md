# Database schema

The backend's database schema. `init/` builds a fresh database; `migrations/`
changes an existing one (each migration is idempotent and mirrors a change made
in `init/`, which stays the source of truth).

```
init/        01_app_backend.sql   02_channel_history_cache.sql   03_admin.sql
migrations/  003_notifications_type_check.sql   004_enable_row_level_security.sql
             005_channel_history_cache.sql   006_admin_dashboard.sql   007_drop_pipeline_tables.sql
```

Apply to a fresh database:

```
for f in backend/schema/init/0*.sql; do psql "$DB_URL" -f "$f"; done
```

Apply a migration to an existing database:

```
psql "$SUPABASE_DB_URL" -f backend/schema/migrations/00N_....sql
```

## Tables

Every table has row-level security enabled with no policies, so Supabase's REST
API (anon/authenticated keys) can read nothing. The backend connects directly as
the database owner, which bypasses it.

**`users`** (`01_app_backend.sql`): one row per app account (email/password, JWT on login).
`full_name`, `email` (unique), `password_hash` (bcrypt), `subscribers` and
`monthly_views` (self-reported, editable in Settings), `channel_url`,
`channel_data` (JSONB snapshot from the YouTube Data API, including `channel_id`),
`channel_fetch_error`, `is_active` (FALSE once an admin disables the account).
A failed channel refresh keeps the last good `channel_data` and only sets
`channel_fetch_error`.

**`predictions`** (`01_app_backend.sql`): one row per saved or run prediction, FK `user_id`
(`ON DELETE CASCADE`). `title`, `category`, `tags`, `target_date`, `target_time`,
`thumbnail_path`/`dataset_path` (served from `/uploads`), `status` (`draft` | `complete`;
drafts skip the model), `predicted_views`, `confidence` (heuristic), `change_vs_avg`,
`trajectory` (JSONB curve), `v_inf`, `tau`, and `used_channel_context` (TRUE only when
HistAttnV2 ran on cached channel history; FALSE means a CatBoost-only forecast).

**`notifications`** (`01_app_backend.sql`): FK `user_id` (`ON DELETE CASCADE`). `type`
(`welcome` | `channel_fetch_success` | `channel_fetch_error` | `prediction_complete`,
enforced by `chk_notifications_type`), `title`, `message`, `read`.

**`channel_history_cache`** / **`channel_history_videos`** (`02_channel_history_cache.sql`):
the forecast ensemble's channel history cache, owned by `channel_cache.py` and keyed on the
YouTube `channel_id` (shared by every user linked to that channel). The cache row holds
`encoder` (a mismatch reads as a miss), `warmed_at` (24h freshness) and `last_error`; the
videos table holds the channel's newest 20 uploads with `published_at`, `view_count`,
`duration_s` and raw CLIP-512 `text_embedding` / `image_embedding`. Warmed in the
background at signup and channel refresh, and re-warmed when a prediction finds it
missing, stale or behind the channel's uploads. Warms only encode new videos.

**`admins`** / **`admin_audit_log`** (`03_admin.sql`): admin accounts, separate from
`users`, created only with `python -m tools.create_admin` (emails stored lower-cased).
Every admin write adds an audit row (`action`, `target_type`/`target_id` with no FK so
entries outlive deleted users, `details` JSONB) in the same transaction as the change.

Checking the result, drift and backups: [../tests/DB_TESTING.md](../tests/DB_TESTING.md).
