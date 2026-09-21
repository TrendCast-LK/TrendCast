# Load testing

Implements section 3.1.5 (Load Testing) of the test plan for the TrendCast **backend API and its
database**. The ETL pipeline is out of scope: the pipeline tables that `/channels` and `/videos` read
are seeded with static rows, and the ETL is never run.

**Tool: [Locust](https://locust.io).** Scenarios are plain Python next to the backend's own tests, it
has a live web UI and CSV/HTML output, and there is no user cap (LoadRunner's free edition stops at
50 virtual users, below the plan's "at least a hundred users"). k6 or JMeter would also work; this
kit is Locust-only.

## What is under test

| Journey (Locust user) | Share | Endpoints |
|---|---:|---|
| `BrowseUser` | 60 | `/dashboard/summary`, `/trends/summary`, `/notifications`, `/predictions`, `/predictions/{id}`, `/channel/me`, `/auth/me` |
| `DataUser` | 25 | `/channels` (unpaginated, largest payload), `/channels/{id}/videos`, `/videos/{id}/timeseries`, `/health`, `/forecast/health` |
| `PredictUser` | 10 | `POST /predictions` (model run and drafts), `DELETE /predictions/{id}`, `POST /forecast` |
| `AccountUser` | 5 | `POST /auth/signup`, `PATCH /auth/me`, `/auth/change-password`, `/channel/refresh`, login |
| `AuthStormUser` | (auth scenario only) | back-to-back `POST /auth/login` |

Things the tests are designed to expose:

- **DB pool is 10 connections** (`db.py` `MAX_CONNECTIONS`). Beyond that, requests queue on a semaphore for up to 30 s: expect latency to climb before any errors. `db_active` pinned at ~10 in the report is the sign.
- **bcrypt** (`security.py`) is CPU-heavy: login and signup storms can starve every other request in the process. The `auth` scenario isolates this.
- **`POST /predictions`** is the expensive operation (image + text embedding and the model).
- **`GET /channels`** returns every channel in one response.

## Safety: nothing here can touch your real data

- The database comes only from **`LOAD_TEST_DB_URL`**. `SUPABASE_DB_URL` and `backend/.env` are never read, and every tool refuses if `LOAD_TEST_DB_URL` points at the same database as either. Non-local hosts need `--allow-remote` (or `LOAD_ALLOW_REMOTE=1` for the app).
- Load is only sent to the **stubbed app** (`stubbed_app.py`), which serves a `/__loadtest__` marker. `smoke_check.py`, Locust and the runner all refuse to start against anything else, for example your normal `uvicorn main:app` dev server.
- Every seeded row is marked (`loadtest_*@example.com`, channels `UCLOAD*`, sign-ups `loadsignup_*@example.com`), and `cleanup` deletes only those.
- The YouTube API is faked in-process (no quota, no network). Uploads go to a temp directory, not `backend/uploads`.

## Setup (once)

```powershell
pip install -r backend/requirements.txt -r backend/tests/load/requirements-load.txt

# a disposable Postgres (pgvector, pg_stat_statements on, data in RAM):
docker compose -f backend/tests/load/docker-compose.load.yml up -d
$env:LOAD_TEST_DB_URL = "postgresql://postgres:loadtest@127.0.0.1:55432/postgres"
```

To use a dedicated (non-production) Supabase project instead, set `LOAD_TEST_DB_URL` to its direct
connection string and add `--allow-remote` to the commands below (and `$env:LOAD_ALLOW_REMOTE="1"` before
starting the app). Never a database with real user data.

## Run

```powershell
cd backend/tests/load

# 1. schema (if empty) + data. Scales: small | medium (default) | large, or set --users/--channels/...
python seed_load_data.py seed --scale medium --apply-schema

# 2. the backend, in a second terminal (same LOAD_TEST_DB_URL). Port 8100 so it never clashes with the dev server.
uvicorn stubbed_app:app --app-dir . --port 8100

# 3. check the setup: one request to every endpoint (expect 17/17)
python smoke_check.py

# 4a. the whole suite, with reports (about 45 min; add soak for another 30)
./run_load_tests.ps1
./run_load_tests.ps1 -Scenarios spike,soak
./run_load_tests.ps1 -Quick            # ~5 min dry run: checks the kit works, numbers are not meaningful

# 4b. or interactively with Locust's web UI on http://localhost:8089
locust -f locustfile.py --host http://127.0.0.1:8100
```

Results land in `results/<timestamp>/<scenario>/`: Locust CSVs, `<scenario>.html` (charts), `monitor.csv`
and `report.md` (verdicts, per-endpoint table, capacity by user count, server resources).

Afterwards: `python seed_load_data.py cleanup`, then `docker compose -f docker-compose.load.yml down`.

## Scenarios

| Scenario | Load | Purpose (test plan wording) |
|---|---|---|
| `baseline` | 1 user, 2 min | single user, normal workload: reference response times |
| `normal` | 20 users, 10 min | normal expected workload with concurrent users |
| `ramp` | +10 users every 2 min to 150 | worst case / exceeding the expected maximum: finds where latency or errors start. Informational: the result is the capacity table, not pass/fail |
| `peaks` | 3 cycles between 10 and 80 users | regular peaks |
| `spike` | 10 users, jump to 100 in ~10 s, hold 2 min, back to 10 | spiky peaks and recovery |
| `soak` | 30 users, 30 min | slow degradation: memory growth, connection leaks |
| `auth` | 20 users doing only logins | bcrypt saturation |
| `predict` | 20 users doing only predictions | model/inference throughput |

Every knob is an environment variable (`shapes.py`, `locustfile.py`): `LOAD_NORMAL_USERS`,
`LOAD_RAMP_MAX`, `LOAD_SPIKE_HIGH`, `LOAD_THINK_MIN/MAX`, `LOAD_AUTH=login` (each user logs in first instead of
using a pre-signed token), `LOAD_PROFILE=read|browse|data|predict|account|auth|mixed`, and so on.

## Pass/fail criteria

Proposed defaults, in `load_config.py`; override with environment variables and agree them with the team.

| Criterion | Default | Variable |
|---|---|---|
| Read endpoints p95 / p99 | 500 ms / 1500 ms | `LOAD_READ_P95_MS`, `LOAD_READ_P99_MS` |
| Login / signup / change-password p95 | 3000 ms | `LOAD_AUTH_P95_MS` |
| Prediction and forecast p95 | 10 000 ms | `LOAD_PREDICT_P95_MS` |
| Error rate, whole run | 1 % | `LOAD_MAX_ERROR_PCT` |

The runner relaxes these for `peaks` (2 %, 1 s) and `spike` (5 %, 2 s / 5 s), matching "no failure under
spikes, recovers afterwards". A failed request means a non-2xx status, a timeout (60 s) or a response body
that does not have the expected shape.

## Reading the results

| You see | It probably means |
|---|---|
| `db_active` near 10 and latency rising, backend CPU low | database pool saturation: raise `MAX_CONNECTIONS`, or look at the slow query in the `pg_stat_statements` table `monitor.py` prints |
| Backend CPU near 100 % of one core, `db_active` low | Python-bound: the GIL. bcrypt or the model. Try `--workers 4` (`uvicorn stubbed_app:app ... --workers 4`) and compare |
| Auth scenario makes `/dashboard/summary` slow too | login work is starving the shared threadpool |
| Memory drifts up steadily in `soak` | leak (`report.md` prints first-fifth vs last-fifth memory) |
| `503`/timeouts from `POST /predictions` at high users | inference queueing; the 30 s pool wait or the 60 s client timeout |
| `lock waits` above 0 | contention on hot rows (`users`, `notifications`) |

## Making the numbers trustworthy

- **Run the load generator on a different machine from the backend and database.** On one laptop they
  compete for CPU (the dry run showed 100 % system CPU), which measures the laptop, not the API.
- The plan asks for a dedicated machine or time slot and a database of realistic size: use `--scale large` and `docker-compose.load.yml` (or a dedicated Supabase project on the plan you deploy on).
- The model is **stubbed by default** (`LOAD_MODEL=stub`, 800 ms of CPU per forecast, `LOAD_STUB_MODEL_MS` to change it) so the API and database can be measured without torch. To load-test real inference, start the app with `$env:LOAD_MODEL="real"`: it needs `artifacts/` and ~25 s to start; only the YouTube calls stay faked.
- Compare like with like: repeat a scenario after each change (pool size, workers, indexes) and keep the `results/` folders.

## Files

| File | Purpose |
|---|---|
| `locustfile.py` | user journeys, request checks, pass/fail evaluation |
| `shapes.py` | load profiles (baseline, normal, ramp, peaks, spike, soak) |
| `load_config.py` | shared settings, thresholds, safety checks |
| `seed_load_data.py` | `seed` / `cleanup` / `stats` for the load-test database |
| `stubbed_app.py` | the backend with the YouTube API, model and uploads stubbed |
| `smoke_check.py` | one request per endpoint before a long run |
| `monitor.py` | CPU, memory and DB connection sampling during a run |
| `analyze_results.py` | Locust CSVs to a Markdown report with verdicts |
| `run_load_tests.ps1` | runs scenarios in sequence with monitoring and reports |
| `docker-compose.load.yml` | disposable Postgres for the runs |
