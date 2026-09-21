### 3.1.7 Failover and Recovery Testing

Failover and recovery testing ensures that TrendCast can fail over and recover from a variety of hardware, software and network malfunctions without undue loss of data or data integrity.

Recovery testing is an antagonistic test process in which the application or system is exposed to extreme conditions, or simulated conditions, to cause a failure, such as a killed process, a lost database connection or a full disk. Recovery processes are then invoked, and the system is monitored and inspected to verify that proper application and data recovery has been achieved.

TrendCast is not a system with client PCs, a physical server or DASD storage. It is a stateless FastAPI process in front of a managed PostgreSQL database (Supabase), plus scheduled ETL jobs on GitHub Actions runners. The failure conditions of the test plan were therefore mapped to their TrendCast equivalents (see Technique). The full plan is in [FAILOVER_RECOVERY_TEST_PLAN.md](FAILOVER_RECOVERY_TEST_PLAN.md).

**Technique Objective:**

Simulate the failure conditions and exercise the recovery processes (manual and automated) to restore the database, application and system to a desired, known state. The following conditions are included in the testing, to observe and log target behaviour after recovery:

- Server interruption: the backend process or the database is killed, restarted or frozen
- Communication interruption: loss of the connection between the backend and the database, YouTube or the model host
- Incomplete cycles: an interrupted transaction, an ETL batch cut off before its commit, a signup or prediction cut off half way
- Storage failure: the uploads disk is full
- Invalid or corrupted data elements in the database
- Failures under high workload
- Loss of all data, and reloading from a backup

**Technique:**

The mapping from the conditions of the test plan to TrendCast, and the test cases (F-nn) that cover each:

| Condition in the test plan | TrendCast equivalent | Cases |
|---|---|---|
| Power interruption to the server | Database killed with SIGKILL (`docker kill`) or restarted; ETL run cut off before commit | F-02, F-03, F-06 |
| Communication interruption | Database unreachable or frozen; YouTube API down or out of quota; model files missing | F-03, F-04, F-05, F-08, F-09, F-10 |
| DASD / controller failure | The local `uploads/` disk is full | F-11 |
| Incomplete cycles | Interrupted signup, prediction save, ETL batch, migration | F-02, F-06, F-16 |
| Invalid database pointers or keys, corrupted data elements | Rows planted with foreign keys and triggers switched off, malformed JSON, orphaned rows | F-14 |
| Failures under high workload | Load beyond the connection pool, then recovery | F-17 |
| Backing up and reloading | `pg_dump` / `pg_restore` after every row is deleted | F-15 |
| Power interruption to the client | Not automated (browser behaviour), manual cases | F-12, F-13 |

The tests already created for Function Testing were used as the basis: the same real API routers, the same helpers and the same checks, run before and after each fault. Recovery is judged by what the database and the API show afterwards, compared with the known state before the fault.

How the faults were injected (all in disposable infrastructure):

- **Server power loss and restarts:** a dedicated PostgreSQL container that the tests kill (`docker kill`, an unclean shutdown), restart, freeze (`docker pause`) and start again. The backend's real connection pool and routers are pointed at it.
- **Interrupted transactions:** open transactions are left uncommitted when the database is killed; ETL functions are made to fail part-way through a batch, and the job's connection is then closed, as when a runner is cancelled.
- **Overlapping runs:** two ETL collector runs and a stand-in for the ingestion job write to the same rows at the same time, from opposite directions.
- **Dependency outages:** YouTube timeouts, HTTP 403 quota errors and 500 errors are replayed from scripted fakes; the real key-rotation code is exercised with a fake service; model artifacts are made missing or corrupt.
- **Full disk:** writes into the uploads folder are made to fail with "No space left on device", including after the first of two files has been written.
- **Corrupted data:** rows the schema forbids are inserted with `session_replication_role = replica`.
- **Total loss:** every row is truncated after a backup, and the backup is restored into a new database that the backend is then pointed at.
- **Failed migration:** migration 003 is applied to a database with rows that violate it.

**Oracles:**

The tests are automated with pytest ([test_failover_recovery.py](test_failover_recovery.py)). The oracle for each case is:

- the HTTP status code and body,
- the state of the database read directly afterwards (row counts, statuses, exact rows before and after a crash, schema fingerprint before and after a failed migration),
- the files left on disk in the uploads folder,
- whether the connection pool is completely free again (all slots and connections returned),
- the time taken, where a bounded response time is the requirement.

Tests that record a known weakness are marked `xfail(strict=True)`. The assertion states the correct behaviour; while the weakness exists the test is reported as an "expected failure". When it is fixed the test unexpectedly passes, strict mode fails the run, and the marker must be removed.

**Required Tools:**

- pytest, FastAPI `TestClient`, psycopg2 (with its `ThreadedConnectionPool`), `google-api-python-client` (ETL error types)
- Docker 28.4 with the `pgvector/pgvector:pg16` image (start, kill, restart, pause), `pg_dump` and `pg_restore` inside the container
- The ETL job, key pool and backend modules of this repository, run unmodified
- Test file: `backend/tests/test_failover_recovery.py`; run with `cd backend && python -m pytest tests/test_failover_recovery.py -v`
- Not used: a network fault proxy (Toxiproxy) and a process supervisor. Slow-network tests (F-05 latency) were replaced by freezing the database (`docker pause`)

**Success Criteria:**

- After the backend, the database or an ETL run is killed at any point, no partial transaction remains and the service returns to a healthy state without manual repair of data.
- An interruption in the middle of a save leaves the database exactly as before or exactly as after, never in between.
- When the database, YouTube, the model or the disk fails, the user gets a bounded-time, meaningful error (never wrong data marked `complete`, videos wrongly marked `deleted`, or orphaned files), and the system resumes when the dependency returns.
- A backup restores to a database in which the same users can log in with their old tokens and see their data.

**Special Considerations:**

- Recovery testing is highly intrusive. Everything was run against disposable containers on a local machine, never the live Supabase database. The suite starts its own database container on a free port, separate from the one used by the other tests, and removes it afterwards.
- Supabase's own failover, storage and backup schedule cannot be tested from outside and are the provider's responsibility. Whether point-in-time recovery is enabled for this project is still to be confirmed.
- The real forecast models (Hugging Face) were not loaded. Model failures were reproduced with small placeholder files, because the loader checks presence and reads the configuration files before it loads any model.
- The ETL jobs were exercised as Python functions against a real database, not as GitHub Actions runners.

---

#### Results

**Run:** `python -m pytest tests/test_failover_recovery.py`, 44 test cases, 4 min 54 s, on a Windows 11 machine with Docker 28.4 and PostgreSQL 16. Full suite afterwards (`python -m pytest`): 461 passed, 1 skipped, 30 expected failures, 0 failures, in 10 min 31 s, so the new file did not disturb any other test.

**Outcome: 39 passed, 5 expected failures (known defects), 0 unexpected failures.**

| Area | Cases | Passed | Known defects (xfail) |
|---|---:|---:|---:|
| F-02 Interrupted transactions | 5 | 4 | 1 |
| F-03 Database restart | 5 | 4 | 1 |
| F-04 Database down at startup | 1 | 1 | 0 |
| F-05 Slow or frozen database, pool exhaustion | 2 | 2 | 0 |
| F-17 Overload, then recovery | 1 | 1 | 0 |
| F-06 ETL killed mid-run | 2 | 2 | 0 |
| F-07 Overlapping ETL runs | 1 | 1 | 0 |
| F-08 YouTube quota and outage | 5 | 5 | 0 |
| F-09 Model unavailable | 4 | 4 | 0 |
| F-10 YouTube unavailable during user actions | 7 | 7 | 0 |
| F-11 Disk full | 3 | 3 | 0 |
| F-14 Corrupted data | 6 | 3 | 3 |
| F-15 Total loss and restore | 1 | 1 | 0 |
| F-16 Failed migration | 1 | 1 | 0 |
| **Total** | **44** | **39** | **5** |

Not run (manual): F-01 (kill the real API process during a model run), F-12 (browser with the backend down), F-13 (browser network drop during a submit).

##### Behaviour verified

| ID | Fault | Result |
|---|---|---|
| F-02 | Database killed with SIGKILL in the middle of a transaction (user, notification and prediction written but not committed) | Pass. After restart only the committed user exists; none of the in-flight rows survive |
| F-02 | Database killed after committing a full data set (every table) | Pass. Every row in five tables is identical after crash recovery |
| F-02 | Signup fails after the user row is written | Pass. The client gets a 500 but the account can log in, so a retry is not a dead end (but see D-1) |
| F-02 | Notification write fails after a prediction is saved | Pass. The prediction is kept and the request returns 200 |
| F-03 | Database restarted while the API is running | Pass. The pool discards the dead connection and serves requests again without restarting the backend; the pool ends with no leaked connections |
| F-03 | The same, through HTTP requests | Pass. Only 200 or a plain 500 is returned, then 15 requests in a row return 200 |
| F-03 | Database killed and started again | Pass. The same user and token work afterwards; nothing was lost |
| F-03 | Database down while requests arrive | Pass. Requests fail with a 500 within 20 s (no hang) and no connection or slot is leaked |
| F-04 | Database down when the backend starts | Pass. Startup fails at once with a clear connection error; when the database is back, the backend starts with no manual clean-up |
| F-05 | All 10 connections busy | Pass. An eleventh request waits for the configured time, then fails with a clear error; the pool is fully usable afterwards |
| F-05 | Database frozen (`docker pause`) for 4 s | Pass. The request is held, completes correctly when the database resumes, and the pool is free again (but see D-4) |
| F-17 | 40 overlapping requests on a 10-connection pool, with timeouts and SQL errors | Pass. Failures are only pool timeouts and the SQL errors; afterwards the pool is free and a query answers in under a second |
| F-06 | ETL job killed after writing the time-series rows but before its commit | Pass. No partial rows; all 5 videos are still due; the next run writes exactly one row per video and moves each video's next poll into the future |
| F-06 | A video flagged deleted, then the next write fails | Pass. The deletion flag stays; the other videos remain active with no time-series rows |
| F-07 | Two collector runs and a stand-in ingestion job update the same 40 videos at once, 8 rounds, opposite input orders | Pass. No deadlock and no failed batch; exactly 640 time-series rows |
| F-08 | All API keys exhausted, at the first or the second batch | Pass. Videos are never marked deleted because of a quota error; only videos the API really left out are flagged |
| F-08 | HTTP 500 on one batch of 50 | Pass. Its videos are neither lost nor marked deleted; the next batch is still processed |
| F-08 | A whole run with every key exhausted | Pass. The job ends cleanly, nothing changes, and the next run with quota polls the same videos |
| F-08 | Key rotation | Pass. A quota error moves to the next key; when all keys are used up the pool raises the exhausted error; a 403 that is not a quota error is raised and the key is not burned |
| F-09 | A model artifact is missing, or corrupt | Pass. Loading never raises, the app starts, and the state reports the error (`ready = false`, error names the file or exception) |
| F-09 | Model down | Pass. A completed prediction returns 503 and leaves no row and no uploaded file; drafts still save; the list works; once the model is back the same request succeeds |
| F-09 | Model down | Pass. `/forecast/health` shows the error, `/forecast` returns 503, `/health` and `/channels` keep working |
| F-10 | YouTube timeout, connection error, HTTP 500, HTTP 403 | Pass (4 cases). Each becomes a controlled channel-resolution error |
| F-10 | YouTube down during signup | Pass. The account is still created, the error is stored and a `channel_fetch_error` notification is written; a refresh after the outage repairs it |
| F-10 | YouTube down during a refresh | Pass. The last good channel snapshot is kept |
| F-10 | YouTube quota error during a forecast | Pass. 503 with a "temporarily unavailable" message; nothing saved |
| F-11 | Disk full while saving a prediction | Pass. 500, no row and no file; when space returns the same request succeeds and the file exists |
| F-11 | Disk fills between the thumbnail and the dataset | Pass. The thumbnail is removed; no orphan file and no row |
| F-11 | Disk full, draft without files, and reads | Pass. Unaffected |
| F-14 | A completed prediction with no result values | Pass. The list, dashboard and trends still return 200 |
| F-14 | One user's corrupt row | Pass. Other users' requests and predictions are unaffected |
| F-14 | Orphaned video and time-series rows (no channel) | Pass. The public endpoints return 200 |
| F-15 | Backup taken, every row deleted, backup restored into a new database | Pass. Before the restore login and old tokens fail (the loss is real); after the restore the same user logs in with the same password and id, the old token works, and predictions and notifications are back |
| F-16 | Migration 003 applied to data that violates it | Pass. It fails, the schema and rows are unchanged; after fixing the rows it applies, and applying it again changes nothing |

##### Findings (known defects)

| ID | Severity | Finding | Evidence | Suggested fix |
|---|---|---|---|---|
| D-1 | Medium | Signup is not atomic. The user row is committed, and the welcome notification is written in a separate transaction. If the second write fails, the client gets a 500, a retry answers "an account with that email already exists", and the account has no welcome notification | `test_f02_signup_is_all_or_nothing` | Create the user and its notification in one transaction |
| D-2 | Low | The first request after a database restart fails. The pool keeps one idle connection, which died with the restart, and hands it out without checking. At most one request fails, then the pool recovers by itself | `test_f03_first_request_after_a_restart_succeeds` | Check the connection with a cheap query, or retry once, in `db.get_connection` |
| D-3 | Low | A `users.channel_data` value that is not the expected JSON object (a string, an array, or a wrong-typed field) makes `/auth/me` and `/channel/me` return an unhandled 500. Only reachable through data corruption; other users are unaffected | `test_f14_malformed_channel_data_gives_a_controlled_response` (3 cases) | Treat a non-object as empty when reading it |

##### Observations (not failed tests)

- **D-4, no query timeout:** with the database frozen, a request waits without limit and completes only when the database returns. The 30 s limit applies only to waiting for a free connection. A `statement_timeout` or a socket timeout would make such an outage produce fast errors.
- **Startup depends on the database:** the connection pool is created when `db.py` is imported, so an unreachable database stops the whole backend from starting (F-04). This is safe and clear, but a supervisor must restart the backend; it will not wait for the database.
- **Restarts of the model:** a model that failed to load stays unavailable until the backend restarts. This was not tested with the real models (see below).

##### Assessment against the success criteria

| Criterion | Result |
|---|---|
| No partial transaction after a kill; service returns to healthy without data repair | **Met.** Crash, restart and kill of the database and of an ETL run left no partial rows; committed data survived unchanged |
| A save interrupted half-way leaves the database as before or as after | **Mostly met.** Prediction save, ETL batch and migration are all-or-nothing. Signup is not (D-1), though the account it leaves is usable |
| Meaningful, bounded errors; nothing wrongly marked; automatic resumption | **Mostly met.** YouTube, model, disk and database outages give clear errors, mark nothing wrongly and resume automatically. A frozen database is not bounded (D-4), and one request fails after a restart (D-2) |
| A restored backup lets the same users back in | **Met.** F-15 |

## Limitations and Remaining Work

- **Manual cases not run:** F-01 (kill the real API process during a model run; a file written just before the kill cannot be cleaned by the code that deletes it on failure, so an orphaned upload is possible), F-12 and F-13 (frontend behaviour with the backend down, and a network drop during a submit).
- **Real models:** recovery of the forecast model without a restart, after a failed load, was not tested because it needs the real Hugging Face models. Only the failure and degraded behaviour were tested.
- **Not testable from here:** Supabase's own failover and backup behaviour. Confirm the backup schedule and whether point-in-time recovery is on; the restore procedure in [DB_TESTING.md](DB_TESTING.md) is manual and quarterly.
- **Simulated, not physical:** power and network faults were simulated with container kill, restart and pause, not by cutting power or cabling. The ETL was tested as functions, not as GitHub Actions runners.
- **Actions the results point to (for the team to decide):** make signup a single transaction (D-1); validate or retry a pooled connection (D-2); read a malformed `channel_data` safely (D-3); set a database statement timeout (D-4); add a scheduled `pg_dump` if the platform does not provide backups.

## Appendix: How to reproduce

Docker must be running. From `backend`:

```
pip install -r requirements-dev.txt
python -m pytest tests/test_failover_recovery.py -v                 # the whole file, about 5 minutes
python -m pytest tests/test_failover_recovery.py -k "f03 or f04"    # database restart cases only
```

The first test that uses the model code imports torch and takes about 100 s. The suite starts a container named `trendcast-flaky-*` on a free port and removes it at the end; it never reads `SUPABASE_DB_URL`. If a run is interrupted, remove a leftover container with `docker rm -f $(docker ps -aq --filter name=trendcast-flaky)`.
