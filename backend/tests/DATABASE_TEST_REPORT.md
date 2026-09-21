# TrendCast – Data and Database Integrity Test Report

| | |
|---|---|
| **System** | TrendCast – YouTube view forecasting web application (FastAPI backend, PostgreSQL on Supabase) |
| **Document** | Data and Database Integrity Testing – test report |
| **Test date** | 20 September 2026 |
| **Status** | Testing complete. One known defect remains open (see section 5). |

---

## 1. Evaluation Mission and Test Motivation

This report covers the data and database testing done for TrendCast. TrendCast lets a content creator forecast the first seven days of views for a planned YouTube video. To do that it stores user accounts (including password hashes), each user's linked channel data, saved predictions, notifications, and the channel, video and view-count history that the forecasting model is built on.

The database is the most important part of the system: every feature reads from it or writes to it, and it holds sensitive information. A wrong value, a broken relationship or an exposed table would either give users wrong forecasts or leak account data. So the database and the data processes were tested as an independent subsystem, before and apart from the user interface.

The objectives of this testing were:

- Verify that the SQL scripts build exactly the schema that is documented, and that they can be re-applied and upgraded safely.
- Verify that the database itself rejects invalid data, and that relationships and cascades keep the data consistent.
- Verify that the backend writes the correct data to the correct tables, and that users can only reach their own data.
- Verify that derived values (engagement figures, channel tiers) are calculated correctly.
- Verify that a backup can be restored to an identical database.
- Verify the live (production) database against the same expectations, and record what was found.

## 2. Target Test Items

| Item | Description |
|---|---|
| Schema scripts | `backend/schema/init/01`–`04` (fresh database) and `backend/schema/migrations/002`–`004` (existing database) |
| Core tables | `channel_stats`, `videos`, `view_timeseries`, `video_features` (768- and 512-dimension embeddings) |
| Archive tables | `channel_stats_archive`, `videos_archive`, `view_timeseries_archive` |
| Application tables | `users`, `predictions`, `notifications` |
| View | `channel_stats_enriched` (engagement KPIs, size tier, channel age, dominant category) |
| Data-access layer | `backend/db.py` connection pool; the auth, channel, notifications and predictions routers |
| Live database | The Supabase PostgreSQL 17.6 database used by the backend |

Out of scope for this report: the user interface, forecast model accuracy, and the dashboard-summary, trends and public listing endpoints.

## 3. Testing Techniques and Types

### 3.1.1 Data and Database Integrity Testing

The database and the database processes were tested as an independent subsystem. The reference approach of inspecting tables by hand in a database viewer was replaced with an automated test suite, so every check can be repeated identically after any change.

| | |
|---|---|
| **Technique Objective** | Test the database on its own, without the user interface or the model. Show that the schema is built correctly by the scripts, that invalid data is rejected, that relationships stay consistent, that the backend stores the right data for the right user, that calculated values are correct, and that the data can be backed up and restored. |
| **Technique** | An automated `pytest` suite runs against a throwaway PostgreSQL 16 server (with the pgvector extension) started in Docker. The schema is built from the real SQL scripts, and every test runs in its own copy of that database, so tests cannot affect each other. The suite is organised in six layers:<br><br>**A – Schema and migrations.** Scripts apply in order; every script can be re-run without changing data or structure; each migration takes an older database to exactly the same schema as a fresh build; the archive tables mirror the core tables; the live schema can be diffed against the scripts.<br>**B – Constraints and relationships.** For every table, invalid rows are inserted directly with SQL and must be rejected (CHECK, foreign key, UNIQUE, NOT NULL, column length, vector dimension); valid and boundary rows must be accepted; deleting a parent removes exactly its children.<br>**C – Backend data.** The real FastAPI routers are driven through a test client and the resulting rows are inspected. The YouTube lookup and the forecast model are replaced with fakes; everything else, including the database, is real. Concurrent access through the connection pool is also tested.<br>**D – Derived data.** Channels with known numbers are inserted and the view's output is compared with hand-calculated values.<br>**E – Data quality.** A read-only tool runs 16 integrity and quality checks (orphans, duplicates, impossible timestamps, inconsistent predictions and so on). Each check is proven by planting exactly one problem in healthy data and confirming that only that check fails.<br>**F – Backup and restore.** A seeded database is dumped with `pg_dump` and restored with `pg_restore` into an empty database, then compared with the original. |
| **Oracles** | The documented schema (column types, keys, constraints, indexes); the rules written into the SQL scripts; hand-calculated expected values for every derived figure; a fresh build of the scripts as the reference for migrated and restored databases; the rows actually stored after each backend operation. |
| **Required Tools** | Python and `pytest` 9.1 (test framework); `psycopg2` 2.9 (database driver); Docker 28 with the `pgvector/pgvector` image (throwaway PostgreSQL); FastAPI `TestClient` (drives the routers); `pg_dump`, `pg_restore` and `psql` (backup, restore and live checks); `backend/tools/data_quality.py` (read-only data-quality checker); the Supabase SQL editor (row-level security inspection). |
| **Success Criteria** | Every valid case is accepted and every invalid case is rejected. All scripts build the documented schema and can be re-applied. Every migrated or restored database equals a fresh build. Backend operations store the expected rows and never expose one user's data to another. Derived values equal the hand-calculated values exactly. A restored backup has identical schema, rows and behaviour. The data-quality checks report no errors. Every defect found is fixed, or recorded with a reason. |
| **Special Considerations** | The tests never read the live connection string. Where the backend itself is loaded (Layer C), its settings are pointed at the throwaway server and a fixture asserts that before any test runs, so the live database cannot be reached. Known defects are recorded as "strict expected failures": the suite stays green, but the test fails loudly as soon as the defect is fixed, which prompts removal of the marker. After a restore, PostgreSQL re-words `IN (...)` check constraints without changing their meaning; the schema comparison normalises this but still reports any genuine change. The live database currently holds only application data (1 user, 2 predictions, 4 notifications), so data-quality checks over collected video history pass trivially there. The tests are safe to repeat: the container is created and removed on every run. |

## 4. Test Results

Final full run, 20 September 2026: **249 passed, 1 skipped, 1 expected failure** (251 tests, 3 min 58 s). No test containers were left running.

| Layer | Test file | Tests | What it proves |
|---|---|---:|---|
| A | `test_db_schema.py` | 29 | Scripts build the documented schema; every script re-runs without changes; migrations 002, 003 and 004 reach the same schema as a fresh build; archive tables mirror the core tables; row-level security is on for every table |
| A / F | `test_schema_diff.py` | 16 | Schema drift is detected: a missing, extra or changed column, index, constraint, view or security setting is reported; harmless restore re-wording is not |
| B | `test_db_constraints.py` | 49 | 28 kinds of invalid data are rejected; boundaries, defaults and values beyond 32-bit range are accepted; cascades work |
| C | `test_api_data.py` | 76 | Passwords are stored only as bcrypt hashes; duplicate and invalid sign-ups write nothing; notifications and predictions are stored correctly; users see only their own data; model and validation errors persist nothing |
| C | `test_db_pool.py` | 7 | The connection pool is safe under concurrent use, waits instead of failing under bursts, and releases connections after errors |
| D | `test_db_views.py` | 29 | Engagement KPIs match hand-calculated values; every size-tier boundary; dominant-category logic; divide-by-zero cases return 0 |
| E | `test_data_quality.py` | 34 | Healthy data passes all 16 checks; each planted problem trips exactly its own check; command-line exit codes; the connection is read-only |
| F | `test_backup_restore.py` | 11 | A restored backup has identical schema, rows, special values (Unicode, large numbers, JSON, arrays, vectors), working id sequences, working constraints and cascades |
| | **Total** | **251** | |

The one skipped test compares a real database with the schema scripts and runs only when a live connection is supplied. It was run manually (section 6). The one expected failure is the open defect D10.

## 5. Defects Found

| ID | Defect | How it was found | Severity | Resolution |
|---|---|---|---|---|
| D1 | Row-level security was off on 9 of 10 tables, including `users` (password hashes). On Supabase, such tables can be read through the public REST API with the anon key. | Schema review, then the live security query | High | Fixed: migration 004 and the init scripts enable it on every table. Applied to the live database. |
| D2 | The live database was missing the notification-type constraint (migration 003 had never been applied). | Live schema drift check | Medium | Applied to the live database; no existing rows violated it. |
| D3 | `notifications.type` had no CHECK constraint; the four allowed values were documented but not enforced. | Constraint tests | Medium | Fixed: `chk_notifications_type` added (init script and migration 003). |
| D4 | `01_schema.sql` failed when re-run on an existing database (the view was defined twice and PostgreSQL cannot drop a view column). | Idempotency tests | Medium | Fixed: the redundant first definition was removed. |
| D5 | A failed channel refresh overwrote the user's stored channel data with NULL, which then blocked predictions. | Backend data tests | Medium | Fixed: the last good snapshot is kept and only the error is recorded. |
| D6 | Rejected prediction requests left orphaned uploaded files on disk. | Backend data tests | Low | Fixed: files are saved only after validation and the model succeed, and removed if the insert fails. |
| D7 | A malformed target date or time returned HTTP 500 instead of a client error. | Backend data tests | Low | Fixed: validated up front and returned as HTTP 422. |
| D8 | The connection pool was not thread-safe and failed outright when more than 10 requests arrived at once. | Code review; confirmed by tests that fail on the old code | High | Fixed: thread-safe pool plus a wait queue with a timeout. |
| D9 | The schema comparison reported a false difference on a restored database (constraint re-wording). | Live restore drill | Low (test tooling) | Fixed in the comparison, with tests proving real changes are still detected. |
| D10 | `videos_archive` has drifted from `videos`: `current_interval_hours` is an integer instead of `NUMERIC(5,2)`, and six metadata columns are missing. | Archive parity test | Low | **Open.** Recorded as a strict expected failure; not fixed. |

The project's written documentation was also corrected where the tests showed it out of date (the database ownership, the removed channel-statistics fallback, the notification constraint and the security settings).

## 6. Live Database Verification

Checks run against the real database on 20 September 2026. All of them only read, except the restore drill, which wrote to a scratch database.

| Check | Result | Notes |
|---|---|---|
| Schema drift (live schema against the scripts) | Found D2 | The only difference was the missing notification constraint; applied afterwards |
| Row-level security | Found D1 | Off on 9 of 10 tables; migration 004 applied |
| Data quality | Pass, 16 of 16 | Live data is 1 user, 2 predictions, 4 notifications; collected video tables are empty in this database |
| Backup and restore drill (Supabase to a local PostgreSQL 17 scratch database) | Pass | Dump 7.2 s, restore 1.5 s; row counts identical on all 10 tables; data-quality checks pass on the restored copy; the one restore message (`schema "public" already exists`) is harmless; the schema comparison exposed D9 |

## 7. Conclusion

The data and database layer was tested as an independent subsystem in six layers, with 251 automated tests. Nine defects were found, including one high-severity security exposure (D1) and one high-severity concurrency defect (D8), and all nine were fixed. One low-severity defect (D10) is open and documented. The database can be rebuilt from its scripts, upgraded through its migrations to an identical schema, backed up and restored without any difference, and its live state matches the documented schema.

## Appendix – How to reproduce

From the `backend` folder, with Docker running:

```
pip install -r requirements-dev.txt
python -m pytest
```

Live-database checks and the backup drill are described step by step in [DB_TESTING.md](DB_TESTING.md).
