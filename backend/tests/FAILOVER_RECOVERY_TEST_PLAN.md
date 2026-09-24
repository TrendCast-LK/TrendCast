# TrendCast – Failover and Recovery Test Plan

| | |
|---|---|
| **System** | TrendCast – YouTube view forecasting web application (FastAPI backend, PostgreSQL on Supabase, React frontend, GitHub Actions ETL) |
| **Document** | Failover and Recovery Testing – plan (section 3.1.7 of the test plan) |
| **Status** | Automated part built and run: [test_failover_recovery.py](test_failover_recovery.py), 39 passed and 5 expected failures (defects, section 6). F-01, F-12 and F-13 are manual and not yet run. |

---

## 1. Purpose

Show that TrendCast survives hardware, software and network faults **without losing data or leaving data inconsistent**, and that its recovery procedures (automatic and manual) return it to a known good state.

The reference test plan (3.1.7) is written for a PHP/MySQL web application with client PCs, a physical server, DASD storage and UPS units. TrendCast has none of that. It is a stateless API process in front of a managed Postgres, plus scheduled jobs on GitHub-hosted runners. Each failure condition in the reference plan is therefore mapped to its TrendCast equivalent in section 3, and the ones with no equivalent are dropped with the reason stated.

## 2. What can fail, and what we already have

| Component | Holds state? | Failure it must survive | Existing behaviour (from the code) |
|---|---|---|---|
| FastAPI process (`backend/main.py`) | No, except `uploads/` on local disk | Crash, kill, restart, host power loss | Model artifacts are re-loaded on startup (`inference.load_artifacts`) |
| DB connection pool (`backend/db.py`) | Connections only | Database restart, network drop, DB unreachable at startup | `get_cursor` rolls back on exception; 10-connection pool with a 30 s semaphore wait |
| Supabase Postgres | **Yes, all of it** | Connection loss mid-transaction, corruption, bad migration, data loss | Backup/restore drill in [DB_TESTING.md](DB_TESTING.md) section 4; RLS on |
| Forecast model (`inference.py`) | No | Artifact missing/corrupt, Hugging Face model download fails, out of memory | Sets `state.ready=False`; `/forecast` and `POST /predictions` return 503; `/forecast/health` reports the error |
| YouTube Data API (`youtube.py`, `inference.py`) | No | Timeout, 403 quota, 5xx, network loss | 10–15 s timeouts; `QuotaExceededError` mapped to 503; failed channel refresh keeps last good `channel_data` |
| Local upload storage (`storage.py`) | **Yes** (thumbnails, datasets) | Disk write fails, crash between file write and DB insert | Uploads are deleted if the DB insert fails |
| ETL Job 1 / Job 2 (GitHub Actions) | Writes to DB | Runner killed or timed out mid-batch, API keys exhausted, overlapping runs | Batch write then explicit commit; rows sorted by PK against deadlocks; key rotation; deleted videos flagged, not retried |
| React frontend | No (JWT in browser) | Backend down, 401/503 responses | To be observed (see F-12) |

## 3. Mapping from the reference plan

| Reference condition (3.1.7) | TrendCast equivalent | Tests |
|---|---|---|
| Power interruption to the client | Browser closed/tab killed mid-request; client network drops | F-12, F-13 |
| Power interruption to the server | Backend process hard-killed; Supabase restart; GitHub runner cancelled | F-01, F-02, F-03, F-06 |
| Communication interruption via network | Backend ↔ Supabase, backend ↔ YouTube, backend ↔ Hugging Face, client ↔ backend | F-03, F-04, F-05, F-09, F-10 |
| DASD / controller loss | Postgres storage fault is Supabase's responsibility and cannot be simulated. **Local disk** for `uploads/` can be: full or read-only disk | F-11 |
| Incomplete cycles (interrupted transaction, sync interrupted) | Signup, prediction save, ETL batch, channel refresh cut mid-way | F-02, F-06, F-13 |
| Invalid database pointers or keys | Orphan foreign keys, duplicate keys, NULLs planted by hand | F-14 |
| Corrupted data elements | Bad rows in `predictions`, `channel_data` JSON, model artifact files | F-09, F-14 |
| Failures under high workload | Load past the 10-connection pool, then recovery | F-17 |
| Backup and reload | `pg_dump` / `pg_restore` drill | F-15, F-16 |
| Dropped | Physical cable pulling, UPS devices: the app is not on hardware we control. Docker network and process controls replace them (section 5) | – |

## 4. Approach and safety rules

1. **Never run these against the live Supabase database.** Every destructive test uses the throwaway `pgvector/pgvector:pg16` container that the existing suite already starts (see [DB_TESTING.md](DB_TESTING.md)). Live-database work is limited to read-only checks and the restore drill into a scratch database.
2. **Reuse the existing tests as the "did it recover" checks**, as the reference plan suggests: after each fault, re-run the relevant function tests (`test_api_function.py`), the data-quality tool (`python -m tools.data_quality`) and the schema check (`test_schema_diff.py`).
3. **Run the backend under the stubbed app** from `tests/load/` (fake YouTube, temp uploads) for anything that would otherwise burn API quota.
4. **Record a "known state" before each test**: row counts of the 10 tables, list of files in `uploads/`, `/health` and `/forecast/health` output. Recovery is judged against it.
5. Each test records: fault injected, time of injection, time to first successful request, data lost or duplicated (yes/no with evidence), manual steps needed.

### Tools

| Need | Tool |
|---|---|
| Disposable Postgres with a start/stop/kill switch | Docker (`docker stop`, `docker kill`, `docker restart`, `docker pause`) |
| Network faults between app and DB | `docker network disconnect/connect`, or Toxiproxy in front of Postgres for latency, timeouts and mid-stream resets |
| Kill the API process | `Stop-Process -Force` (PowerShell) or `kill -9`; also `uvicorn` under a restarting supervisor to test auto-restart |
| Fault the YouTube / Hugging Face calls | Point `API_BASE` / hosts at a local stub that returns 403 quota, 500, or hangs; or block with a firewall rule |
| Full or read-only disk | A small tmpfs/Docker volume for `uploads/`, or a read-only bind mount |
| Corrupt data / files | `psql` (`SET session_replication_role = replica` to bypass FKs), overwrite bytes in an artifact file |
| Observe | `/health`, `/forecast/health`, backend log, `pg_stat_activity`, Locust for load in F-17 |
| Backup / restore | `pg_dump`, `pg_restore`, existing `test_backup_restore.py` |

## 5. Test cases

Priority: **H** = data-loss or outage risk, **M** = degraded behaviour, **L** = cosmetic or unlikely.

### A. Backend process and database

| ID | Pri | Fault | Steps | Expected (pass criteria) |
|---|---|---|---|---|
| F-01 | H | Backend killed mid-request | Start `POST /predictions` (complete, with thumbnail); `kill -9` the process during the model run. Restart. | Restart succeeds and artifacts reload (`/forecast/health` ready). No half-written `predictions` row (no row, or a `draft`/`complete` row with consistent fields, never `complete` with NULL results). Any orphaned file in `uploads/` is found and reported (known gap, see section 6). User can log in with the old JWT. |
| F-02 | H | DB connection lost mid-transaction | During `POST /auth/signup`, and separately during `POST /predictions`, `docker kill` Postgres between the statements (use Toxiproxy reset or a debugger pause to hit the window). | Client gets a clean 5xx, not a hang. After DB restart: no partial user, no user without its welcome notification if the two are meant to be atomic, no prediction without its `prediction_complete` notification. Transaction rolled back, not committed half. |
| F-03 | H | Database restarts while the API is running | Warm the pool with 10 requests. `docker restart` Postgres. Send requests immediately, then after 10 s and 60 s. | Records time to recovery. **Key question:** do the pooled connections that died with the restart cause errors on later requests, or does the pool discard them? Pass = service self-heals within one minute without restarting the backend and without a request storm of 500s. |
| F-04 | H | Database unreachable at backend startup | Stop Postgres, start the backend. Then start Postgres. | Backend fails fast with a clear message (the pool is created at import in `db.py`) **or** starts and recovers. Pass = behaviour is documented and there is no silent broken state. Restart after the DB is up works with no manual clean-up. |
| F-05 | M | Slow DB / network partition | Add 5–35 s latency with Toxiproxy; call `/health`, `/channels`, `POST /predictions`. | Requests time out or fail with 5xx in bounded time (the pool waits up to 30 s); the semaphore is released, so after the fault clears the pool is not exhausted (check `db_active` returns to 0). |

### B. ETL pipeline (scheduled jobs)

| ID | Pri | Fault | Steps | Expected |
|---|---|---|---|---|
| F-06 | H | Job 2 killed mid-run | Seed due videos; run `job2_timeseries_collector.py`; kill it after the API fetch but before `conn.commit()`, and again between two batches. Re-run. | Uncommitted batch leaves no partial rows. Videos not yet updated are still due (`next_poll_at` unchanged) and are picked up by the next run. No duplicate `view_timeseries` snapshots (data-quality check "duplicate snapshots" stays clean). |
| F-07 | H | Overlapping runs / deadlock | Start two Job 2 runs, and Job 1 alongside Job 2, on the same videos. | No deadlock error, no duplicate snapshots, final state equals a single run's. (Validates the sorted-by-PK rule in CLAUDE.md.) |
| F-08 | M | YouTube quota exhausted or API outage | Stub returns 403 `quotaExceeded` for key 1, then for all keys; separately return 500s and timeouts. | Key rotates on quota. When all keys are exhausted the job stops cleanly with a clear log and non-zero exit, **without** marking videos `deleted` (a quota error must not be mistaken for "video missing"). Next run resumes normally. |

### C. Model, external services and storage

| ID | Pri | Fault | Steps | Expected |
|---|---|---|---|---|
| F-09 | H | Model artifacts missing or corrupt / Hugging Face unreachable at startup | (a) delete `catboost_magnitude.cbm`; (b) truncate a `.pkl`; (c) block huggingface.co with no local cache. Start the backend. | Backend still starts and serves non-forecast endpoints. `/forecast/health` shows `ready:false` and the error. `POST /forecast` and completed `POST /predictions` return 503 with a message. **Drafts still save.** Restoring the files and restarting returns to `ready:true`. Note whether recovery needs a restart (currently yes, there is no retry). |
| F-10 | M | YouTube API down during a user action | Stub timeouts/500/403 for signup, `/channel/refresh`, and `POST /predictions`. | Signup still succeeds if intended, with `channel_fetch_error` set and a `channel_fetch_error` notification. Refresh failure keeps the last good `channel_data`. Prediction returns 503, no row saved as `complete`. After the API returns, refresh and predictions work with no manual repair. |
| F-11 | M | Disk full / read-only `uploads/` | Mount `uploads/` on a 1 MB volume, then read-only; submit predictions with thumbnails. | Clean 5xx error; no `predictions` row pointing at a file that does not exist; already-stored uploads remain readable; recovery once space is freed. |

### D. Client side

| ID | Pri | Fault | Steps | Expected |
|---|---|---|---|---|
| F-12 | M | Backend down while the UI is open | Stop the backend with the frontend loaded; click through Dashboard, Trends, New Prediction, Settings. Restart the backend. | No blank screen or unhandled crash; user sees an error state; retry or reload works **without re-login** if the JWT is still valid. A form filled in before the outage is not silently lost (or the loss is documented). |
| F-13 | M | Client connection drops mid-submit | Submit a prediction, then cut the browser network (DevTools offline) before the response. Reconnect and reload. | The prediction either exists once or not at all. Retrying does not create duplicates that the user cannot tell apart (note if it does). |

### E. Data corruption and backup

| ID | Pri | Fault | Steps | Expected |
|---|---|---|---|---|
| F-14 | H | Corrupted / inconsistent data | In a scratch DB plant: orphan `videos.channel_id`, duplicate `(video_id, scraped_at)`, `predictions` with `status='complete'` and NULL results, malformed `users.channel_data`, negative counts (via bypassed constraints). Run the API journeys and `tools.data_quality`. | `tools.data_quality` reports each plant as an error. API returns a controlled error (404/422/5xx with a message), never an unhandled traceback that breaks other requests. After the bad rows are removed, the same journeys pass. |
| F-15 | H | Total loss and reload | Run the backup/restore drill from DB_TESTING.md section 4 for the backend database. Then also **delete a table's rows in the scratch DB** and restore from the dump. Point the backend at the restored database and run the function tests. | Row counts identical; data quality 16/16; schema check passes; the backend runs against it and existing JWTs still validate for the same users (same `JWT` secret). Record dump/restore times against the target below. |
| F-16 | M | Bad migration | Apply a migration that fails halfway (e.g. 003 with violating rows) to a scratch copy. | Migration leaves the database unchanged (or in a documented state); the procedure "restore, fix, re-apply" in DB_TESTING.md section 5 works. |

### F. High workload (recovery afterwards)

| ID | Pri | Fault | Steps | Expected |
|---|---|---|---|---|
| F-17 | M | Overload past the pool, then recover | Re-use the `spike` and `ramp` scenarios in `tests/load/`. When the app is saturated, stop the load. | After load stops, latency returns to baseline within 1 minute, `db_active` returns to 0, no stuck connections, no restart needed. Errors during the spike are timeouts/503s, not corrupted data. |

## 6. Weak points: what the tests found

Hypotheses from reading the code, and what the automated run showed. The confirmed defects are `xfail(strict=True)` tests: they turn into failures when the code is fixed, which is the cue to remove the marker.

| # | Hypothesis | Test | Result |
|---|---|---|---|
| 1 | Dead pooled connections are handed out after a database restart (F-03) | `test_f03_first_request_after_a_restart_succeeds` | **Confirmed, minor.** The first request after a restart fails with a 500. psycopg2's pool keeps only one idle connection (`minconn=1`), so at most one request fails; the pool then discards it and recovers by itself (`test_f03_pool_heals_itself...`, `test_f03_http_requests_recover...` pass). Fix: validate the connection with `SELECT 1` or retry once in `get_connection` |
| 2 | Pool is created at import, so an unreachable database stops the app (F-04) | `test_f04_...` | **Confirmed as designed.** `import db` fails at once with `OperationalError` and works with no clean-up when the database is back. A supervisor must restart the backend; it will not wait for the database |
| 3 | A failed model load is never retried (F-09) | `test_f09_*` | **Partly tested.** A missing or corrupt artifact is reported in `/forecast/health` and answered with 503, the app starts, drafts and data endpoints keep working, and completed predictions work again when the model returns. That recovery *without a restart* was not tested because it needs the real Hugging Face models: still a manual check |
| 4 | A hard kill leaves an orphaned upload (F-01) | not automated | **Open.** Manual test; the cleanup code only runs while the process is alive |
| 5 | Signup is not atomic (F-02) | `test_f02_signup_is_all_or_nothing` | **Confirmed defect.** The user row is committed, then the welcome notification is written separately. If the second write fails the client gets a 500, retrying answers "account already exists", and the account has no welcome notification. The account itself works (`test_f02_signup_failure_after_the_user_row...` passes). Fix: create both in one transaction |
| 6 | A quota error could be mistaken for a deleted video (F-08) | `test_f08_*` | **Refuted.** Exhausted keys, a non-quota 403 and a 500 on one batch never mark videos as deleted, and the next run resumes normally |
| 7 | Exhausted keys are forgotten by the next run (F-08) | not tested | **Accepted.** Each 5-minute run starts with a full key pool by design; only costs a wasted request per key |
| 8 | Two databases (CLAUDE.md) | – | Recovery tests state which one they restore: `test_f15` restores the backend database |
| 9 | Malformed `users.channel_data` (new, F-14) | `test_f14_malformed_channel_data...` | **Confirmed defect, low severity.** A value that is not the expected JSON object makes `/auth/me` and `/channel/me` return an unhandled 500. Only reachable through data corruption. Other users are unaffected (test passes) |
| 10 | No query timeout (new, F-05) | `test_f05_a_hung_database...` | **Observation.** With the database frozen, a request waits indefinitely (there is no `statement_timeout` or socket timeout) and completes when the database returns. Requests then pile up on the 30 s connection wait and fail, which is bounded, but the first 10 wait forever |

## 7. Success criteria

Mirrors the reference plan, adapted:

- **Server-side crash:** after a backend or ETL process is killed at any point, the database has no partial transaction and the service returns to healthy after a plain restart, with no manual data repair.
- **Rollback:** an interruption in the middle of signup, prediction save, ETL batch or refresh leaves the database exactly as before or exactly as after, never in between.
- **Dependency loss:** database, YouTube API, Hugging Face or disk failure produces a bounded-time, meaningful error (4xx/5xx with a message), never a hang, and never data marked `complete` or `deleted` wrongly. The service resumes automatically when the dependency returns, with the exceptions recorded in section 6.
- **Backup and reload:** a backup restores to a database with identical row counts, clean data-quality and schema checks, and the application works against it.
- **Targets to agree before running** (proposed): database reconnect within 60 s of DB return (F-03); dump plus restore under 5 minutes, which is comfortable against the 7.2 s + 1.5 s measured on 2026-09-20; recovery point of one day, as the drill is manual (see recommendation below).

## 8. Environment, schedule and responsibilities

- **Environment:** local machine with Docker; throwaway Postgres 16 (use pg17 for the restore drill to match Supabase, as in DB_TESTING.md); backend under the stubbed app; Toxiproxy container for network faults. No live systems.
- **Order:** E (backup, F-15) first, because every other test relies on being able to reset; then A, B, C, D, F.
- **Duration:** about one to two days to script and run, most of it F-01/F-02/F-06, which need precise timing.
- **Run at a quiet time or on an isolated machine**, as the reference plan says. Here that means never while the live ETL schedule is pointed at the same database.
- **Who:** the tester runs the faults; the database owner confirms the Supabase side (backups, point-in-time recovery, restart behaviour) since it cannot be tested from outside.

## 9. Deliverables

- [test_failover_recovery.py](test_failover_recovery.py): F-02 to F-11 and F-14 to F-17 automated. F-17 only in miniature (the full spike is in `tests/load/`). Manual: F-01, F-12, F-13, and the real-model recovery in section 6 item 3.
- A results table filled in per test (fault, time to recover, data loss, pass/fail, defects) and `FAILOVER_RECOVERY_TEST_REPORT.md` in the layout of the existing reports: not yet written.
- Defects raised for the confirmed items in section 6 (1, 5, 9), and an entry in the run log in DB_TESTING.md for each drill on the live database.

Run it (Docker required, about 5 minutes; the first test using the model imports torch, about 100 s):

```
cd backend
python -m pytest tests/test_failover_recovery.py -v
python -m pytest tests/test_failover_recovery.py -k "f03 or f04 or f05"   # database restart cases only
```

The suite starts its own throwaway Postgres container (`trendcast-flaky-*`) on a random free port, separate from the one the rest of the suite uses, and removes it at the end. It never reads `SUPABASE_DB_URL`.

## 10. Recommendations that testing may motivate

- Confirm Supabase's own backup schedule and whether point-in-time recovery is on for this project. The reference plan calls for weekly backups; the current procedure here is only run quarterly and by hand.
- Add a scheduled `pg_dump` (a second GitHub Actions workflow) if the platform does not provide one.
- `/health` already runs `SELECT 1`, so it returns 500 while the database is down (`test_f03_requests_fail_fast...`); a supervisor can use it. Consider a 503 with a clear body.
- Validate or retry a pooled connection once in `db.get_connection` (section 6 item 1), and set a `statement_timeout` (item 10).
- Make signup a single transaction (item 5).
- Retry the model load in the background rather than only at startup.
