# Database testing and live-database checks

## Running the automated tests

```
cd backend
pip install -r requirements-dev.txt
python -m pytest                  # everything (needs Docker; ~2.5 min)
python -m pytest tests/test_db_constraints.py -k "channel"   # a slice
```

A throwaway `pgvector/pgvector:pg16` container is started for the session and
removed afterwards. Tests never read your real `SUPABASE_DB_URL`: the backend is
loaded with its settings pointed at the throwaway server (a fixture asserts it),
so the live database cannot be reached. To use another disposable server instead of Docker, set
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
| `test_failover_recovery.py` | G | Database kill/restart/hang, interrupted ETL, quota and model outages, full disk, corrupt rows, restore after total loss (plan: [FAILOVER_RECOVERY_TEST_PLAN.md](FAILOVER_RECOVERY_TEST_PLAN.md); starts its own Postgres container) |

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

Do this once per quarter and before any risky migration. Restore into a scratch
database, never over the live one. A throwaway Docker container works well. Use
the same Postgres major version as Supabase (`psql $env:SUPABASE_DB_URL -tAc "show server_version"`;
the drill on 2026-09-20 was 17.6, hence `pg17` below). Work outside the repo:
the dump contains password hashes.

Run these as separate steps (PowerShell):

```powershell
# 1. set up
mkdir $HOME\tc-drill; cd $HOME\tc-drill
$env:SUPABASE_DB_URL = (Select-String -Path <repo>\backend\.env -Pattern '^SUPABASE_DB_URL=(.*)$').Matches.Groups[1].Value.Trim('"')
$admin   = "postgresql://postgres:scratch@127.0.0.1:5544/postgres"
$scratch = "postgresql://postgres:scratch@127.0.0.1:5544/scratch_restore"

# 2. start the scratch server and wait until it accepts TCP connections (it restarts once while initialising)
docker run -d --name tc-scratch -e POSTGRES_PASSWORD=scratch -p 5544:5432 pgvector/pgvector:pg17
do { Start-Sleep 2; pg_isready -h 127.0.0.1 -p 5544 -q } until ($LASTEXITCODE -eq 0)

# 3. create the database; the vector extension lives in `public` on Supabase too
psql $admin -c "CREATE DATABASE scratch_restore"
psql $scratch -c "CREATE EXTENSION vector"

# 4. back up and restore, noting both durations
Measure-Command { pg_dump $env:SUPABASE_DB_URL -Fc --no-owner --no-acl --schema=public -f backup.dump }
Measure-Command { pg_restore --no-owner --no-acl -d $scratch backup.dump }
```

The restore prints one harmless error, `schema "public" already exists`
(a new database already has that schema). Anything else needs a look.

Compare exact row counts on both sides:

```powershell
foreach ($t in 'channel_stats','videos','view_timeseries','video_features','users','predictions','notifications','channel_stats_archive','videos_archive','view_timeseries_archive') {
  "$t  live=" + (psql $env:SUPABASE_DB_URL -tAc "select count(*) from $t") + "  scratch=" + (psql $scratch -tAc "select count(*) from $t")
}
```

Then run the same two checks against the restored copy (from `backend\`):

```powershell
python -m tools.data_quality --db-url $scratch
$env:LIVE_DB_CHECK_URL = $scratch
python -m pytest tests/test_schema_diff.py -k live -s
```

Clean up: `docker rm -f tc-scratch`, delete `$HOME\tc-drill`, and remove the
`SUPABASE_DB_URL` / `LIVE_DB_CHECK_URL` environment variables.

Pass: identical counts, no data-quality errors, `1 passed` from the schema check.
The schema check ignores the harmless rewording Postgres applies to `IN (...)`
constraints after a restore, but still reports any real change.

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
| 2026-09-20 | Schema drift | Supabase (backend DB) | Found: missing `chk_notifications_type` | Migration 003 was not applied; applied afterwards |
| 2026-09-20 | Row-level security | Supabase (backend DB) | Found: off on 9 of 10 tables, incl. `users` | Migration 004 written and applied |
| 2026-09-20 | Data quality | Supabase (backend DB) | Pass, 16/16 | Mostly vacuous: pipeline tables are empty here; only 1 user, 2 predictions, 4 notifications were checked. Run against the pipeline database for the time-series checks |
| 2026-09-20 | Backup and restore | Supabase to local scratch (pg17) | Pass | Dump 7.2 s, restore 1.5 s; all 10 table counts matched; data quality 16/16; schema check flagged only Postgres's rewording of 3 `IN` constraints (now normalised) |
