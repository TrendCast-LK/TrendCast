### 3.1.5 Load Testing

**Technique Objective:**
The objective of load testing is to subject TrendCast to varying workloads and to observe how it behaves: under a normal workload, under a worst-case workload, and with a considerable number of concurrent users. The test measures response times, transaction rates and error rates, and finds the point at which the system stops coping. The goal, as in the test plan, is to determine whether the system keeps functioning properly beyond its expected workload, and to learn its performance boundaries.

The system under test is the FastAPI backend and the PostgreSQL database behind it. The user interface and the ETL pipeline are outside the scope of this test. The tables that the ETL normally fills (`channel_stats`, `videos`, `view_timeseries`) were filled with static rows so that the public data endpoints could be read; the pipeline itself was never run.

**Technique:**
The load was generated with **Locust**, a Python load-testing tool. Each virtual user runs a realistic journey against the API and waits one to three seconds between actions, like a person using the application. The journeys and their share of the traffic were:

| Virtual user | Share | What it does |
|---|---:|---|
| Browse | 60 % | Dashboard, trends, notifications, list and open predictions, own channel, own profile, mark notifications read |
| Data | 25 % | `/channels`, `/channels/{id}/videos`, `/videos/{id}/timeseries`, `/health`, `/forecast/health` |
| Predict | 10 % | Run a prediction (uploads a thumbnail), save a draft, list, delete, `POST /forecast` |
| Account | 5 % | Sign up, update profile, change password, refresh channel, log in again |

Every response is checked: a non-2xx status, a timeout (60 s) or a body that does not have the expected shape counts as a failure. The database was seeded to a realistic size before the run (200 users, 200 channels, 6,000 videos, 90,000 time-series rows, 2,000 predictions and 2,000 notifications). Virtual users authenticate with a pre-signed token, so login cost is measured only through the Account users' logins and sign-ups.

Four scenarios were run, each shaped to match a workload named in the plan:

| Scenario | Workload shape | Purpose |
|---|---|---|
| Baseline | 1 user for 45 s | Single user, normal workload: reference response times |
| Normal load | Ramp to 20 concurrent users over 60 s, hold for 2 min | Normal expected workload with concurrent users |
| Ramp (worst case) | 20, 40, 60, 80 and 100 users, 30 s at each step | Beyond the expected maximum: find where latency or errors begin (at least 100 users, as the plan requires) |
| Spike | 10 users for 30 s, jump to 100 users in about 10 s, hold 60 s, back to 10 users for 45 s | Spiky peak load |

The ramp and spike scenarios were then repeated with the backend running four worker processes instead of one (`uvicorn --workers 4`), to test whether a single Python process was the limiting factor.

While each scenario ran, a monitor sampled once a second the backend's CPU, memory and threads, the machine's CPU and memory, and the database's connections, active queries and lock waits.

To keep the test safe, it ran against a separate, disposable PostgreSQL 16 database in Docker (never the live database), and the backend was started in a special mode in which the YouTube API and the forecast model are replaced by stand-ins. The test tools refuse to send load to any backend that is not that special instance.

**Oracles:**
The outcomes were judged from three sources:

- **Locust statistics:** requests, failures, requests per second and the median, 95th and 99th percentile response times for every endpoint, as CSV files and an HTML report with charts.
- **Server measurements:** backend CPU and memory, and database connections and active queries, from the monitor.
- **Pass/fail limits** agreed for this test (below). The limits are proposals and can be changed.

| Criterion | Limit |
|---|---|
| Read endpoints, 95th percentile | 500 ms |
| Read endpoints, 99th percentile | 1,500 ms |
| Login, sign-up, change password, 95th percentile | 3,000 ms |
| Prediction and forecast, 95th percentile | 10,000 ms |
| Error rate over the whole run | 1 % (2 % for peaks, 5 % for the spike) |

For the spike scenario the read limits were relaxed to 2,000 ms (95th) and 5,000 ms (99th).

**Required Tools:**
- Locust 2.46 (load generation), Python 3.12
- `psutil` (backend CPU and memory sampling) and `psycopg2` (database sampling)
- Docker 28.4 with the `pgvector/pgvector:pg16` image (disposable test database), `pg_stat_statements` enabled
- Windows 11 machine with 12 logical processor cores
- The load-test scripts in `backend/tests/load/`: `locustfile.py` (journeys), `shapes.py` (workload shapes), `seed_load_data.py` (test data), `stubbed_app.py` (test instance of the backend), `monitor.py`, `analyze_results.py` and `run_load_tests.ps1` (runs the scenarios and writes a report for each)

**Success Criteria:**
- Average transactions for a single user and for multiple users work without errors.
- The system withstands spiky peak loads and regular peaks without failing.
- The system is exercised with at least 100 concurrent virtual users.
- The performance boundaries are found and recorded, so that the expected workload can be judged against them.
- Response times stay within the limits above for the normal workload.

**Special Considerations:**
- Load testing should be done on a dedicated machine or at a dedicated time, with a database of realistic size. The database was realistic in size, but **the load generator, the backend, the database and the monitor all ran on the same laptop**, which was already busy (system CPU was at or near 100 % during every run and 93 to 98 % of memory was in use, even in the idle baseline). The absolute times below are therefore worse than a dedicated setup would give. The shape of the results is reliable, not the exact figures.
- The forecast model was replaced by a stand-in that uses 0.8 s of CPU per forecast, and the YouTube API by an instant fake. Prediction timings show how the application and database behave around the model, not the real model's cost.
- The connection pool in `backend/db.py` allows 10 database connections per process. Waiting requests queue for up to 30 s before failing.
- Bcrypt password hashing (login, sign-up, change password) is deliberately slow and uses CPU.
- `GET /channels` returns every channel in one response (no paging), so its size grows with the data.

---

## Test Results

### Overview

| Scenario | Backend | Requests | Errors | Requests/s | Median | 95th pct. | 99th pct. | Result |
|---|---|---:|---:|---:|---:|---:|---:|---|
| Baseline (1 user, 45 s) | 1 process | 22 | 0 | 0.5 | 38 ms | 91 ms | 95 ms | **Pass** |
| Normal load (20 users, 3 min) | 1 process | 1,324 | 0 | 7.4 | 37 ms | 500 ms | 1,500 ms | Fail, marginally (3 endpoints) |
| Ramp (20 to 100 users) | 1 process | 1,796 | 0 | 11.9 | 2.1 s | 7.6 s | 10 s | Informational |
| Spike (10 to 100 users) | 1 process | 1,100 | 0 | 8.1 | 3.2 s | 9.7 s | 12 s | Fail (latency) |
| Normal load | 4 workers | 1,212 | 0 | 6.6 | 60 ms | 1.1 s | 4.7 s | Fail |
| Ramp | 4 workers | 2,160 | 2 | 14.0 | 0.88 s | 9.2 s | 15 s | Informational |
| Spike | 4 workers | 1,220 | 10 | 8.9 | 1.5 s | 8.9 s | 14 s | Fail |

No request failed in any of the single-process runs. In the ramp scenario the pass/fail limits do not apply; its result is the capacity table below.

### Baseline: one user

With one user the system is fast: the median response is 38 ms and the 95th percentile 91 ms, all within the limits. Backend CPU was almost idle (peak 2.7 % of one core), memory was 65 MB, and the backend used one database connection. This is the reference for the other runs.

### Normal load: 20 concurrent users

There were no errors. The median response was 37 ms, but 3 of 22 endpoints were just over the 500 ms limit at the 95th percentile:

| Endpoint | Requests | Median | 95th pct. | 99th pct. | Limit (95th) |
|---|---:|---:|---:|---:|---:|
| GET /dashboard/summary | 226 | 24 ms | 370 ms | 510 ms | 500 ms |
| GET /notifications | 164 | 47 ms | 380 ms | 710 ms | 500 ms |
| GET /predictions | 146 | 40 ms | 480 ms | 700 ms | 500 ms |
| GET /channels/[id]/videos | 122 | 23 ms | 330 ms | 470 ms | 500 ms |
| GET /videos/[id]/timeseries | 118 | 33 ms | 460 ms | 500 ms | 500 ms |
| GET /trends/summary | 109 | 40 ms | 450 ms | 650 ms | 500 ms |
| GET /channels | 72 | 59 ms | 420 ms | 500 ms | 500 ms |
| GET /predictions/[id] | 69 | 44 ms | **520 ms** | 720 ms | 500 ms |
| GET /auth/me | 55 | 28 ms | **530 ms** | 630 ms | 500 ms |
| PATCH /auth/me | 11 | 46 ms | **740 ms** | 740 ms | 500 ms |
| POST /predictions (run) | 17 | 1,500 ms | 1,700 ms | 1,700 ms | 10,000 ms |
| POST /predictions (draft) | 14 | 130 ms | 330 ms | 330 ms | 500 ms |
| POST /forecast (run) | 6 | 1,200 ms | 1,200 ms | 1,200 ms | 10,000 ms |
| POST /auth/login | 2 | 930 ms | 930 ms | 930 ms | 3,000 ms |
| POST /auth/signup | 1 | 1,350 ms | 1,400 ms | 1,350 ms | 3,000 ms |
| POST /auth/change-password | 5 | 1,700 ms | 1,900 ms | 1,900 ms | 3,000 ms |

(Some endpoints with very few requests are omitted; the rest passed.) The three endpoints over the limit exceeded it by 4 % to 48 %. Login, sign-up and change-password each took about 1 to 2 seconds per call, which is the cost of bcrypt on this machine. Prediction runs took about 1.5 s, most of which is the stand-in model and the faked YouTube calls. Backend memory stayed flat at 66 MB for the whole run, and the database never had more than one query running at once.

### Ramp: 20 to 100 concurrent users

Throughput stopped growing at about 12 to 13 requests per second from 40 users onward, and adding users only made responses slower. There were no errors.

| Users | 95th pct. (all requests) | Requests/s | Errors |
|---:|---:|---:|---:|
| 20 | 2.6 s | 8.4 | 0 |
| 40 | 3.2 s | 11.8 | 0 |
| 60 | 5.3 s | 13.3 | 0 |
| 80 | 10 s | 12.5 | 0 |
| 100 | 9.9 s | 12.5 | 0 |

At 100 users even the trivial `GET /health` took 4.7 s at the 95th percentile, and `GET /auth/me` 6.2 s. Backend CPU peaked at 188 % (across processes), machine CPU at 100 %, and the database connections reached the pool limit of 10. The database itself was not busy: at most 2 queries ran at the same time and there were no lock waits. No user count in this scenario met the read-endpoint limits.

### Spike: 10 users jumping to 100

The system did not fail: every one of the 1,100 requests succeeded. But while the 100 users were active, responses were very slow: median 3.2 s, 95th percentile 9.7 s, 99th percentile 12 s. The slowest were `GET /notifications` (95th 11 s), `GET /trends/summary` (11 s), `GET /dashboard/summary` (7.3 s), `POST /channel/refresh` (14 s), `POST /auth/change-password` (11 s) and `POST /predictions (run)` (15 s). Throughput was about 13 requests per second at 100 users. How quickly response times returned to normal after the spike was not analysed in detail.

### Does one process limit the system?

The ramp and spike were repeated with four worker processes. The comparison:

| Scenario | Setup | Requests/s | Median | 95th pct. | 99th pct. | Errors |
|---|---|---:|---:|---:|---:|---:|
| Normal load | 1 process | 7.4 | 37 ms | 500 ms | 1.5 s | 0 |
| | 4 workers | 6.6 | 60 ms | 1.1 s | 4.7 s | 0 |
| Ramp | 1 process | 11.9 | 2.1 s | 7.6 s | 10 s | 0 |
| | 4 workers | 14.0 | 0.88 s | 9.2 s | 15 s | 2 |
| Spike | 1 process | 8.1 | 3.2 s | 9.7 s | 12 s | 0 |
| | 4 workers | 8.9 | 1.5 s | 8.9 s | 14 s | 10 |

Requests per second during the ramp:

| Users | 1 process | 4 workers |
|---:|---:|---:|
| 40 | 11.8 | 12.5 |
| 60 | 13.3 | 18.4 |
| 80 | 12.5 | 16.3 |
| 100 | 12.5 | 16.8 |

Four workers raised the throughput plateau by roughly 35 % (12.5 to about 17 requests per second) and halved the median response time in the ramp and spike scenarios, but the 95th and 99th percentile times were not better and 12 requests were dropped without a response (an HTTP 0: the connection failed or timed out). Machine CPU averaged 97 to 100 % in these runs, so the four workers, the load generator and the database were competing for the same processor cores. **This shows that on this machine a single process is not the main limit. It does not show whether more processes would help on a dedicated server.** With four workers the database saw up to 31 connections (10 per worker) but still no more than 4 active queries.

### Resource use

| Scenario | Backend CPU, peak | Backend memory | DB connections, peak | Active DB queries, peak | Lock waits |
|---|---:|---:|---:|---:|---:|
| Baseline | 2.7 % | 65 MB | 1 | 0 | 0 |
| Normal | 109 % | 66 MB, no growth | 4 | 1 | 0 |
| Ramp (1 process) | 188 % | 67 to 76 MB | 10 | 2 | 0 |
| Spike (1 process) | 152 % | 71 to 75 MB | 10 | 1 | 0 |
| Ramp (4 workers) | 302 % | n/a | 31 | 3 | 0 |
| Spike (4 workers) | 234 % | n/a | 28 | 4 | 0 |

(CPU: 100 % is one full core.)

### Findings

| # | Finding | Evidence | Assessment |
|---|---|---|---|
| L1 | Throughput reaches a ceiling of about 12 to 13 requests per second on this machine; beyond about 40 concurrent users, extra users only add latency. | Ramp table | The main capacity limit found |
| L2 | The database is not the bottleneck. | At most 2 to 4 queries active at once, no lock waits, at 100 users | The system is limited by CPU in the Python process, not by database queries |
| L3 | The 10-connection pool fills up under 100 users, but this does not cause errors. | Pool reached 10 in ramp and spike; no failures | Requests wait in line; the effect on latency was not separated from the CPU effect |
| L4 | At the normal workload of 20 users, 3 of 22 endpoints slightly exceed the 500 ms limit. | `GET /auth/me`, `GET /predictions/[id]`, `PATCH /auth/me` | Borderline; could pass on a dedicated machine. Not a functional problem |
| L5 | Bcrypt operations (login, sign-up, change password) take 1 to 2 s each even at normal load, and up to 11 s at 100 users. | Normal and spike tables | Expected for bcrypt; sign-up bursts will be slow |
| L6 | The system stays correct under a spike: no failed requests with one process. | 1,100 of 1,100 succeeded | Meets "without failing", but 95th percentile response is about 10 s |
| L7 | Four workers give about 35 % more throughput but do not improve the slowest responses, and 12 requests were dropped on this machine. | Comparison tables | Cannot be concluded on shared hardware; needs a dedicated test |
| L8 | No memory growth was seen in runs of 3 minutes. | 66 MB flat in Normal | Too short to rule out a leak; needs a soak run |

### Assessment against the success criteria

| Criterion | Result |
|---|---|
| Average transactions work perfectly for a single user and for multiple users | **Met.** No errors in the baseline (1 user), normal (20 users), ramp or spike runs with one process |
| Withstands spiky and regular peaks without failing | **Partly met.** The spike caused no failures, but responses degraded to a 95th percentile of about 10 s. Regular peaks were not run (see below) |
| At least 100 concurrent virtual users | **Met.** The ramp and spike scenarios reached 100 users |
| Performance boundaries determined | **Met, with a caveat.** About 12 to 13 requests per second and useful response times up to roughly 20 to 40 users on this machine. Exact limits need a dedicated setup |
| Response times within the limits at normal load | **Nearly met.** 3 endpoints were over by 4 to 48 % |

## Limitations and Remaining Work

- **Shared hardware.** Everything ran on one busy laptop. The load generator should run on a separate machine from the backend and the database. Repeat the ramp and spike scenarios on that setup before quoting the numbers as capacity.
- **Not run in this round:** the soak test (30 users for 30 minutes, for memory growth and connection leaks), the regular-peaks scenario, and separate login-only and prediction-only scenarios that would show how much of the CPU load comes from bcrypt and how much from the model. The kit contains all four; a short dry run of them worked but produced no meaningful figures.
- **The real forecast model** was replaced by a stand-in (0.8 s of CPU per forecast). Run with the real model (`LOAD_MODEL=real`) to measure real inference cost.
- **Recovery after the spike** was recorded but not analysed.
- **Actions the results point to (for the team to decide):** run the four-worker comparison on a dedicated machine; consider paging `GET /channels`; consider whether sign-up and change-password need to cope with bursts.

## Appendix: How to reproduce

From `backend/tests/load`, with Docker running. The full instructions are in `backend/tests/load/README.md`.

```powershell
pip install -r ../../requirements.txt -r requirements-load.txt
docker compose -f docker-compose.load.yml up -d
$env:LOAD_TEST_DB_URL = "postgresql://postgres:loadtest@127.0.0.1:55432/postgres"
python seed_load_data.py seed --scale medium --apply-schema

# second terminal, same LOAD_TEST_DB_URL (add --workers 4 for the four-worker runs)
uvicorn stubbed_app:app --app-dir . --port 8100

python smoke_check.py                       # 17 of 17 endpoints must respond
./run_load_tests.ps1                        # the standard suite (about 45 min; add soak for 30 more)
```

The runs in this report used shortened durations, set through environment variables before running:
`LOAD_BASELINE_SECONDS=45`, `LOAD_NORMAL_MINUTES=2`, `LOAD_RAMP_STEP=20`, `LOAD_RAMP_STEP_SECONDS=30`, `LOAD_RAMP_MAX=100`, `LOAD_SPIKE_WARMUP=30`, `LOAD_SPIKE_HOLD=60`, `LOAD_SPIKE_RECOVERY=45`, and scenarios `baseline,normal,ramp,spike`.

Clean up afterwards with `python seed_load_data.py cleanup` and `docker compose -f docker-compose.load.yml down`. The raw results of each run (Locust CSV files, HTML charts, monitor data and `report.md` per scenario) are written to `backend/tests/load/results/` (not committed to git).
