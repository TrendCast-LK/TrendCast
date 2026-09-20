# Performance Profiling Test Report

**Project:** TrendCast
**Assessment date:** 2026-09-20
**Assessment type:** Static performance analysis with simulated planning estimates
**Reference basis:** RUP performance test-plan principles and IEEE 829/1012-style test documentation

> **Evidence statement:** This report is based on repository inspection of the FastAPI backend, PostgreSQL access layer, inference pipeline, frontend source, schema migrations, and project documentation. No load generator, production database trace, CPU profiler, memory profiler, or browser performance capture was present in the repository. Metrics labelled **simulated estimate** are planning values and must be replaced with measured values before release sign-off.

## 1. Overview & Objective

### Purpose

Verify response times, transaction rates, resource utilization, and time-sensitive requirements under normal and worst-case workloads. The profiling objective is to establish whether TrendCast can serve authenticated dashboard traffic, channel data, notifications, forecast requests, uploads, and trend views without unacceptable latency, memory growth, database contention, timeouts, or unhandled exceptions.

The assessment focuses on:

- API response time and execution latency, including p50, p95, p99, and timeout rate.
- Transaction throughput for read and write endpoints.
- CPU and memory usage during model startup, warm inference, cold-cache inference, and uploads.
- PostgreSQL query count, execution plans, pool wait time, rows returned, and connection utilization.
- External YouTube API latency and quota-related failure behavior.
- Frontend bundle, network waterfall, chart rendering, and large-result rendering costs.

### Target components

- **FastAPI application:** `backend/main.py`, including `/health`, `/channels`, `/channels/{channel_id}/videos`, `/videos/{video_id}/timeseries`, `/forecast`, and `/forecast/health`.
- **Authentication and user loading:** `backend/routers/auth.py` and `backend/security.py`, including bcrypt verification and the `get_current_user` dependency.
- **Prediction workflow:** `backend/routers/predictions.py`, especially `create_prediction` and `list_predictions`.
- **Forecast computation:** `backend/inference.py`, including `load_artifacts`, `_fetch_channel_history`, `_channel_features`, `run_forecast`, and `run_forecast_on_image`.
- **Database layer:** `backend/db.py`, which uses a `psycopg2.pool.SimpleConnectionPool` with `maxconn=10`.
- **Notifications and trends:** `backend/routers/notifications.py` and `backend/routers/trends.py`.
- **File and image processing:** `backend/storage.py`, `UploadFile` reads, and Pillow image decoding.
- **Frontend rendering:** `frontend/src/pages/Dashboard.jsx`, `PredictionResult.jsx`, and `Trends.jsx`; Chart.js setup; the shared fetch wrapper in `frontend/src/lib/api.js`; and Vite build output.

## 2. Performance Profiling Strategy & Specification Table

| Attribute | Specification Details |
| :--- | :--- |
| **Technique Objective** | Exercise key functional transactions under normal and worst-case workloads to measure response time, execution latency, throughput, CPU, memory consumption, database load, external API latency, and frontend rendering cost. |
| **Technique / Methodology** | Run API calls individually and sequentially for baseline timings, then repeat with controlled concurrency. Instrument each request with a correlation ID and timings for authentication, upload read/write, image decode, inference stages, external YouTube calls, SQL execution, connection-pool acquisition, and response serialization. Capture p50/p95/p99, error rate, throughput, peak RSS, CPU percentage, pool wait, query count, query duration, rows returned, and PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)` for representative queries. Compare cold and warm model/cache states. Use browser DevTools Performance and Network panels for Dashboard, Trends, and Prediction Result, and run `npm run build` to inspect production bundle output. |
| **Oracles & Baselines** | Release targets: simple health/authenticated reads p95 < 500 ms; paginated list reads p95 < 1,000 ms; warm forecast p95 < 3,000 ms excluding unavoidable external delays; cold-cache forecast p95 < 15,000 ms with explicit timeout handling; upload processing p95 < 5,000 ms before external calls; memory increase < 50 MB per ordinary read and no unbounded growth across 100 repeated requests; error rate < 1% for valid requests; zero unhandled exceptions. These are acceptance thresholds, not measurements from this repository. |
| **Required Tools** | Python `pytest` plus a FastAPI test client; `pytest-benchmark` or `locust`/JMeter for load; `py-spy` or `scalene` for CPU profiling; `tracemalloc` and process RSS monitoring for memory; PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)`, `pg_stat_activity`, and query logging; `psutil` or Task Manager for host resources; Postman or `curl` for repeatable API calls; Chrome/Firefox DevTools for frontend timing; Vite production build output; and YouTube API mocks to isolate network latency and quota behavior. |
| **Success Criteria** | Single and concurrent valid transactions complete within the applicable threshold, without memory leaks, pool exhaustion, request timeouts, unhandled exceptions, corrupted uploads, stale response data, or unacceptable external API failure amplification. Database connections are returned to the pool after success and failure. |
| **Special Considerations** | Use a staging Supabase/PostgreSQL database with production-like row counts and indexes. Keep ETL jobs separate or record their concurrent load because they share core tables and database capacity. Test CPU-only inference because `load_artifacts` sets the inference device to CPU. Test cold startup separately because artifact loading includes six CatBoost models, PCA artifacts, JSON configuration, and two SentenceTransformer models. Mask API keys and user data in traces. Test YouTube quota exhaustion, slow responses, invalid thumbnails, 10 MB thumbnail boundaries, and 50 MB dataset boundaries. |

## 3. Scope of Profiling & Evaluated Functions

### Backend routes and transactions

| Area | Files and functions/routes evaluated | Performance relevance |
| :--- | :--- | :--- |
| Health and readiness | `backend/main.py`: `health`, `forecast_health` | Establishes database round-trip and model-readiness baselines. `health` executes `SELECT 1`; `forecast_health` exposes startup load time and device state. |
| Dataset reads | `GET /channels`, `GET /channels/{channel_id}/videos`, `GET /videos/{video_id}/timeseries` | Results are fetched completely into memory. The channel endpoint reads from `channel_stats_enriched`; video and time-series routes have no visible pagination or limit. |
| Direct forecast | `POST /forecast`: `forecast` and `inference.run_forecast` | Synchronous CPU inference and possible thumbnail download. It can include channel-history retrieval and model prediction work in the request thread. |
| Signup and login | `POST /auth/signup`: `signup`; `POST /auth/login`: `login` | Signup combines bcrypt hashing, user insert, welcome notification, YouTube channel refresh, optional subscriber update, and final user lookup. Login performs an indexed email lookup and bcrypt verification. |
| Authenticated user access | `security.get_current_user`, `get_user_by_id`, `GET/PATCH /auth/me`, `POST /auth/change-password` | Every protected request performs a fresh `users` query. Password hashing and verification are intentionally CPU-expensive operations. |
| Channel refresh | `POST /channel/refresh`, `refresh_user_channel` | External YouTube resolution, user update, and notification insert occur in a user-facing request. |
| Prediction creation | `POST /predictions`: `create_prediction` | Highest-cost transaction. Reads uploads into memory, writes files synchronously, decodes with Pillow, runs inference, inserts the prediction, inserts a notification, then performs a final select on a new connection. |
| Prediction reads | `GET /predictions`, `GET /predictions/{prediction_id}`, `DELETE /predictions/{prediction_id}` | Listing has no pagination. Each request also pays the authentication user lookup. |
| Notification operations | `GET /notifications`, notification read endpoints, `create_notification` | Listing executes a list query and a separate unread-count query; results are unbounded. |
| Trends aggregation | `GET /trends/summary`: `get_trends_summary` | Loads all predictions for a user and computes counts, averages, categories, and timeline in Python. Time and response size grow with prediction history. |

### Inference and storage functions

- `inference.load_artifacts`: startup critical path; loads six CatBoost models, two PCA reducers, configuration files, and two SentenceTransformer models.
- `inference._fetch_channel_history`: up to three sequential YouTube API calls on a cache miss, each with a 15-second timeout; caches at most the latest 30 usable videos for six hours.
- `inference._channel_features`: calculates baseline, standard deviation, log volatility, category diversity, and video count from channel history.
- `inference.run_forecast_on_image`: image embedding, text embedding, PCA transforms, feature-vector assembly, and CatBoost predictions.
- `backend/routers/predictions.py::_read_limited`: reads up to 10 MB for thumbnails and 50 MB for datasets into memory.
- `backend/storage.py::save_upload`: synchronous local file writes under `backend/uploads/`.

### Frontend components

- `frontend/src/main.jsx`: application bootstrap under `StrictMode`, `BrowserRouter`, `ThemeProvider`, and `AuthProvider`.
- `frontend/src/App.jsx`: route and protected-route composition.
- `frontend/src/pages/Dashboard.jsx`: concurrent dashboard and prediction requests followed by rendering all returned prediction cards.
- `frontend/src/pages/PredictionResult.jsx`: Chart.js construction when prediction or theme changes.
- `frontend/src/pages/Trends.jsx`: Chart.js timeline and category rendering from the complete API response.
- `frontend/src/lib/api.js`: shared `fetch` wrapper without visible retries, cancellation, request timing, caching, deduplication, or pagination.

## 4. Performance Test Execution & Results

### Execution status

No instrumented execution was available in the repository. The following table is a **simulated workload model and static-analysis result**, not an observed benchmark. It identifies expected risk and the measurement to collect. The status is therefore a release-readiness status, not proof that the endpoint currently meets the numeric target.

Assumptions for the simulated ranges: warm application process, local/staging network, indexed PostgreSQL tables, 100 to 10,000 user-owned rows where relevant, one thumbnail under 2 MB, and no concurrent ETL spike. External API ranges are intentionally wider because network behavior is outside the repository.

| Transaction / Feature | Execution / Response Time | Memory Consumption | Database Query Count / Load | Status |
| :--- | :--- | :--- | :--- | :--- |
| `GET /health` / `health` | Simulated 5-50 ms; one database round trip | Simulated < 5 MB request overhead | 1 query: `SELECT 1`; minimal load | Pass for baseline; verify pool wait under concurrency |
| `GET /forecast/health` / `forecast_health` | Simulated < 5 ms; no database query | < 1 MB | 0 queries | Pass |
| `POST /auth/login` / `login` | Simulated 100-500 ms, dominated by bcrypt; p95 must remain < 1,000 ms | < 10 MB | 1 indexed `users.email` query | Pass conditionally; benchmark bcrypt cost and concurrent CPU saturation |
| Protected read such as `GET /auth/me` | Simulated 10-150 ms plus pool wait | < 5 MB | 1 `users` lookup per request | Needs Optimization at scale due to repeated user lookup on every protected request |
| `GET /channels` / `get_channels` | Simulated 100-2,000+ ms depending on channel count and view computation | Proportional to all rows returned; potentially tens of MB | 1 full result query over `channel_stats_enriched`; inspect view plan | Needs Optimization because response is unbounded |
| `GET /channels/{channel_id}/videos` | Simulated 20-500+ ms | Proportional to all channel videos | 1 filtered query ordered by `published_at DESC`; composite index is not visible | Needs Optimization for large channels and pagination |
| `GET /videos/{video_id}/timeseries` | Simulated 20-500+ ms | Proportional to all snapshots for a video | 1 query; existing `(video_id, scraped_at DESC)` index supports lookup | Pass for moderate histories; Needs Optimization without a result limit |
| `POST /forecast` warm cache | Simulated 300-3,000 ms; model and embedding work are synchronous | Simulated 20-100+ MB transient model/tensor allocation | 0 PostgreSQL queries in the direct route; possible up to 3 external YouTube calls on cache miss | Needs Optimization until measured and protected by a timeout/budget |
| `POST /forecast` cold channel cache | Simulated 3,000-45,000 ms; three external calls can each reach a 15-second timeout | Simulated 20-100+ MB plus response buffers | 0 PostgreSQL queries; up to 3 sequential YouTube API calls | Fail against normal interactive threshold when upstream is slow; add isolation, caching, and explicit deadline |
| `POST /predictions` draft | Simulated 50-500 ms | Up to 50 MB dataset plus upload buffers; thumbnail may add 10 MB | At least 2 database operations: insert and final select; authentication adds 1 user query | Needs Optimization for upload memory and transaction count |
| `POST /predictions` complete, warm cache | Simulated 500-4,000 ms before notification overhead | Thumbnail bytes, decoded image, embedding tensors, and model allocations; likely > 50 MB peak in process | Authentication query, prediction insert, notification insert, final select: typically 4 DB operations | Needs Optimization; highest-value profiling target |
| `POST /predictions` complete, cold cache | Simulated 3,000-45,000+ ms | Same as warm path plus retained request buffers | Same DB operations plus up to 3 sequential YouTube calls | Fail against interactive target under upstream timeout conditions |
| `GET /predictions` / `list_predictions` | Simulated 20-1,500+ ms as user history grows | Proportional to all prediction rows, trajectory JSON, and response serialization | Authentication query plus 1 unbounded predictions query | Needs Optimization; paginate and select only required fields |
| `GET /notifications` | Simulated 20-500+ ms | Proportional to all notifications returned | Authentication query plus list query and unread-count query | Needs Optimization; paginate and combine/count efficiently |
| `GET /trends/summary` / `get_trends_summary` | Simulated 20 ms for small histories to 1,000+ ms for large histories | O(number of predictions) Python lists and response timeline | Authentication query plus 1 full user prediction query | Needs Optimization; aggregate in SQL and cap timeline size |
| Frontend Dashboard | Simulated first meaningful view 1-3 seconds on local production build; grows with prediction cards | Browser heap grows with returned cards and chart data | Concurrent `/dashboard/summary` and `/predictions`, plus auth bootstrap | Needs Optimization until production waterfall and render traces are captured |
| Frontend Trends / Prediction Result | Simulated 50-500 ms chart creation after data arrives | Chart.js data and canvas allocations grow with timeline size | Backend response is unbounded | Needs Optimization for large histories; cap/downsample data and lazy-load chart code |

### Measurement gaps

The following values must be captured in the execution phase before a formal Pass can be assigned:

- Actual p50/p95/p99 latency under defined concurrency.
- Peak and retained process RSS during 100 repeated forecast and upload requests.
- CPU utilization and model inference stage timings.
- PostgreSQL query execution time, buffer hits, rows returned, and connection-pool wait.
- YouTube call latency, timeout rate, quota errors, and cache-hit ratio.
- Browser LCP, long tasks, JavaScript transfer size, chart render time, and heap growth.

## 5. Identified Bottlenecks & Optimization Recommendations

### Priority 1: Synchronous forecast and prediction inference

**Finding:** `run_forecast_on_image` performs text/image embedding, PCA transforms, and multiple CatBoost predictions synchronously. On a channel-history cache miss, `_fetch_channel_history` makes up to three sequential YouTube requests with 15-second timeouts. `MODEL_INTEGRATION.md` documents approximately 200-400 ms thumbnail inference cost, excluding network calls.

**Recommendations:**

1. Add stage-level timers and request deadlines for upload, image decode, embedding, PCA, CatBoost, each YouTube call, database work, and serialization.
2. Use a background job or bounded inference worker queue for complete predictions, returning a job status when interactive latency cannot be guaranteed.
3. Enforce an end-to-end deadline shorter than the client timeout and return structured 503 responses for upstream failures.
4. Preserve and instrument the six-hour channel cache, add bounded size/eviction behavior, and use locking or a shared cache when multiple workers are deployed.
5. Consider batching or reusing embedding work where product behavior permits; do not reload artifacts per request.

### Priority 2: Unbounded reads and Python-side aggregation

**Finding:** `/channels`, channel videos, time series, `/predictions`, `/notifications`, and `/trends/summary` fetch complete result sets. `get_trends_summary` performs filtering, averages, category grouping, and timeline construction in Python.

**Recommendations:**

1. Add validated `limit` and cursor/offset pagination to every collection endpoint.
2. Cap or downsample time-series and trends timeline responses for chart display.
3. Move trend counts and averages into SQL aggregates and fetch only the bounded timeline needed by the UI.
4. Select only columns needed by each response instead of `SELECT *` in `SELECT_ONE_SQL`.
5. Add response-size and row-count metrics so growth is visible before it becomes a latency incident.

### Priority 3: Database indexes and query plans

**Finding:** Existing indexes support primary access patterns, including predictions by user/time, notifications by user/time, videos by channel, and time series by video/time. Potential gaps remain for ordering and view computation.

**Recommendations:**

1. Run `EXPLAIN (ANALYZE, BUFFERS)` for `channel_stats_enriched`, `/channels/{channel_id}/videos`, trends aggregation, and all collection queries at production-like cardinality.
2. Consider a composite `videos (channel_id, published_at DESC)` index for the channel video filter/order pattern if the plan shows a sort or expensive scan.
3. Consider `videos (channel_id, category_id)` if the correlated category calculation in `channel_stats_enriched` is confirmed as a hotspot.
4. Review the explicit `idx_users_email`; the `UNIQUE` constraint already creates a unique index, so the additional index may be redundant.
5. Add query timeouts and connection-pool acquisition monitoring. The current `SimpleConnectionPool(maxconn=10)` has no visible pool wait or statement-timeout instrumentation.

### Priority 4: Upload and storage memory pressure

**Finding:** `_read_limited` reads the entire upload into memory, with limits of 10 MB for thumbnails and 50 MB for datasets. `save_upload` writes synchronously to local disk, and thumbnail bytes remain available while Pillow and inference process the image. There is no visible cleanup, quota, streaming, or object-storage policy.

**Recommendations:**

1. Stream uploads to controlled temporary files while enforcing byte limits.
2. Resize and validate images before model inference, then release raw buffers as soon as possible.
3. Add per-user quotas, cleanup of abandoned files, and storage utilization metrics.
4. Use object storage or a dedicated upload service for multi-worker or production deployments.
5. Test concurrent 50 MB uploads separately from forecast concurrency.

### Priority 5: Multiple sequential transactions and repeated authentication work

**Finding:** Complete prediction creation commits the prediction, separately inserts a notification, and then opens another connection for the final select. Signup also performs several sequential writes and external work. `get_current_user` queries `users` for every protected request, while the frontend dashboard separately requests authentication and page data.

**Recommendations:**

1. Return the inserted prediction using `RETURNING` with the complete response columns where practical.
2. Combine prediction and notification writes in one transaction when notification failure semantics permit, or use an outbox pattern.
3. Avoid holding a database transaction open during YouTube calls or model inference.
4. Add a short-lived per-request user cache only if authorization semantics remain correct; otherwise optimize the selected user columns and measure the lookup.
5. Add frontend request timing, cancellation, and response caching/deduplication for auth/bootstrap calls.

### Priority 6: Frontend transfer and rendering cost

**Finding:** Chart.js and React are in the main production dependency graph, route-level code splitting is not visible, and external Google Fonts and Material Symbols introduce network dependencies. Dashboard and Trends render returned collections without a visible display limit.

**Recommendations:**

1. Measure Vite production bundle size and add route-level lazy loading for chart-heavy pages.
2. Paginate or virtualize prediction cards and cap chart points.
3. Capture browser long tasks and heap snapshots for Dashboard, Trends, and Prediction Result.
4. Self-host or otherwise budget external font/icon requests where deployment policy requires predictable first load.
5. Ensure chart instances are destroyed on dependency changes; existing cleanup should be verified under repeated navigation.

## 6. Conclusion & Verification Summary

TrendCast has a coherent performance architecture for a prototype: forecast artifacts are loaded once during FastAPI lifespan startup, the database uses a bounded PostgreSQL connection pool, core tables have several useful indexes, and channel history is cached for six hours. The primary risk is the complete prediction path, where synchronous CPU inference and uncached external YouTube calls share the request lifecycle with file I/O and multiple database transactions.

The system should not be declared performance-ready for production until the simulated values in Section 4 are replaced by controlled measurements. The minimum release verification set is:

1. Warm and cold `POST /forecast` and `POST /predictions` tests with mocked and real staging YouTube behavior.
2. Concurrent authenticated read tests that measure pool wait, database CPU, and response-size growth.
3. Large-history tests for `/predictions`, `/notifications`, `/channels`, time series, and `/trends/summary`.
4. Upload boundary and concurrent-upload memory tests at 10 MB and 50 MB limits.
5. PostgreSQL query-plan review for the enriched channel view, channel video ordering, and trend aggregation.
6. Production frontend build, browser waterfall, chart render, long-task, and heap-growth measurements.
7. Failure tests for model-not-ready state, invalid images, YouTube timeouts, quota exhaustion, missing channels, and database errors.

**Overall assessment:** **Needs Optimization / Verification Pending.** The application has no evidence of an inherent correctness blocker in the reviewed paths, but forecast latency, unbounded result growth, memory pressure from uploads, and missing performance instrumentation prevent a deployment-readiness Pass based on the current repository evidence.
