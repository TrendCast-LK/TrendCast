# Database testing and live-database checks

## Running the automated tests

```
cd backend
pip install -r requirements-dev.txt
python -m pytest                  # everything (needs Docker; ~2.5 min)
python -m pytest tests/test_db_constraints.py -k "channel"   # a slice
```

A throwaway `pgvector/pgvector:pg16` container is started for the session and
removed afterwards. Tests never read `SUPABASE_DB_URL` and never touch the live
database. To use another disposable server instead of Docker, set
`TEST_DB_ADMIN_URL` to a superuser URL (the backup/restore tests need Docker).

| File | Layer | What it proves |
|---|---|---|
| `test_db_schema.py` | A | Scripts build the documented schema; re-runnable; migrations 002/003 reach the same schema as a fresh build; archive tables mirror the core tables |
| `test_db_constraints.py` | B | Bad data is rejected (CHECK, FK, UNIQUE, NOT NULL, vector size); boundaries and defaults; cascades |
| `test_api_data.py` | C | The real API routers write the right rows; ownership; error mapping; no orphaned uploads |
| `test_db_pool.py` | C | The connection pool is safe under concurrent requests |
| `test_db_views.py` | D | `channel_stats_enriched` KPIs, tiers, `tier_category`, age |
| `test_data_quality.py` | E | The read-only data-quality checks catch each planted problem |
| `test_schema_diff.py` | A6 / F | Schema drift is detected; optional live comparison |
| `test_backup_restore.py` | F | A `pg_dump` restores to an identical database |

Tests marked `xfail(strict=True)` are known defects with the reason attached.
They flip to a failure when fixed, which is the cue to delete the marker.

## Checks to run against the live database

Everything below only reads, except the restore drill, which writes to a
scratch database. `$SUPABASE_DB_URL` is the direct Postgres connection string.

### 1. Data quality (run before every model retrain)

```
cd backend
python -m tools.data_quality                 # report; exit 1 on integrity errors
python -m tools.data_quality --strict        # warnings fail too
python -m tools.data_quality --json > dq.json
python -m tools.data_quality --stale-days 3 --max-missing-pct 5
```

Errors (orphans, duplicate snapshots, future timestamps, inconsistent
predictions, case-variant duplicate emails) should never occur. Warnings
(decreasing view counts, stale channels, missing metadata) need a look; some are
legitimate.

### 2. Schema drift

Compares the live `public` schema with a fresh build of `schema/init/01`..`04`:

```
cd backend
LIVE_DB_CHECK_URL="$SUPABASE_DB_URL" python -m pytest tests/test_schema_diff.py -k live -s
```

A failure lists each missing, unexpected or changed column, index, constraint
or view. A database that never received migration 003, for example, reports
`missing constraint: notifications.chk_notifications_type`.

### 3. Access control (Supabase)

```sql
-- every table should have RLS enabled
SELECT relname, relrowsecurity FROM pg_class
WHERE relnamespace = 'public'::regnamespace AND relkind = 'r' ORDER BY 1;

-- what the API roles can reach
SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants
WHERE table_schema = 'public' AND grantee IN ('anon', 'authenticated') ORDER BY 2, 1;
```

Any table with `relrowsecurity = false` that `anon` can read is exposed through
the REST API. `users` (password hashes) must never be. The backend connects as
the database owner, which bypasses RLS, so enabling it does not affect the
backend. Migration `004_enable_row_level_security.sql` turns it on for every
table, and the schema drift check (step 2) now includes each table's RLS
setting, so a table losing it is reported.

### 4. Backup and restore drill

Do this once per quarter and before any risky migration. Use a scratch database
on a server with pgvector available; never restore over the live one.

```
pg_dump "$SUPABASE_DB_URL" -Fc --no-owner --no-acl -f backup.dump    # note the duration
createdb scratch_restore
pg_restore --no-owner -d scratch_restore backup.dump                  # note the duration
```

Then compare per-table row counts on both sides:

```sql
SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY 1;   -- approximate
-- exact: run SELECT COUNT(*) FROM <table> for each table on both databases
```

and run the same two checks against the restored copy:

```
python -m tools.data_quality --db-url postgresql://.../scratch_restore
LIVE_DB_CHECK_URL=postgresql://.../scratch_restore python -m pytest tests/test_schema_diff.py -k live -s
```

Pass: identical counts, no data-quality errors, no schema differences. Also
confirm a new row gets an id above the old maximum (sequences restored).

### 5. Applying a migration to the live database

1. Restore the latest backup to a scratch database (step 4).
2. Apply the migration to the scratch copy: `psql "$SCRATCH_URL" -f schema/migrations/00N_....sql`.
3. Re-run it; it must be a no-op (migrations are written to be idempotent).
4. Run steps 1 and 2 above against the scratch copy.
5. Take a fresh backup of the live database.
6. Apply to live: `psql "$SUPABASE_DB_URL" -f schema/migrations/00N_....sql`.
7. Re-run steps 1 and 2 against live.

Migrations that add a constraint (such as 003) fail if existing rows violate
it; the migration's header has a query to find them first.

## Run log

Record each drill so results can be compared over time.

| Date | Check | Database | Result | Notes (duration, defects found) |
|---|---|---|---|---|
| | | | | |
