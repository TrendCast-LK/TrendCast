# Database schema

The backend's database schema. `init/` builds a fresh database; `migrations/`
changes an existing one (each migration is idempotent and mirrors a change made
in `init/`, which stays the source of truth).

```
init/        01_schema.sql   02_archive_and_switch_channels.sql   03_app_backend.sql   04_video_features.sql
migrations/  002_add_video_metadata.sql   003_notifications_type_check.sql   004_enable_row_level_security.sql
```

Apply to a fresh database (needs the `vector` and `uuid-ossp` extensions):

```
for f in backend/schema/init/0*.sql; do psql "$DB_URL" -f "$f"; done
```

Apply a migration to an existing database:

```
psql "$SUPABASE_DB_URL" -f backend/schema/migrations/00N_....sql
```

Checking the result, drift and backups: [../tests/DB_TESTING.md](../tests/DB_TESTING.md).
