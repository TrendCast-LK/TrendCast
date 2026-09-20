### 3.1.8 Configuration Testing
**Technique Objective:**
The objective of configuration testing is to verify that TrendCast behaves correctly across the required operating environments and service configurations used by the system. This project is built as a distributed application spanning a Python FastAPI backend, a React + Vite frontend, a PostgreSQL/Supabase data store, and a Dockerized ETL pipeline that uses Airflow, Postgres, and external YouTube APIs. Configuration testing therefore targets environment variables, dependency versions, database connectivity, model artifact availability, external API access, and runtime resource behavior. The purpose is to confirm that the application can start successfully, remain stable under realistic minimum-resource conditions, and maintain acceptable responsiveness when running with only the required software stack and constrained memory or disk usage.

This is especially important because the backend depends on multiple critical configuration items: the `SUPABASE_DB_URL`, `JWT_SECRET_KEY`, and `YOUTUBE_API_KEY` environment variables must be valid before the service starts, and the ML runtime must load several CatBoost models and embedding artifacts from the repo at startup. The application should also be tested under both developer-friendly and minimal viable hardware profiles, with attention to cold-start latency, memory consumption, and graceful failure when required services are absent or misconfigured.

**Technique:**
Configuration testing should be performed in stages using the actual runtime paths used by the application:

- Validate the base environment:
  - Confirm Python version compatibility for backend and ML components, with the repo’s recommended Python 3.10+ baseline.
  - Confirm Node.js 18+ for the frontend build and Vite development workflow.
  - Confirm Docker and Docker Compose availability for the ETL/data-pipeline services.
  - Verify that required OS networking is available for local development and for access to Supabase, YouTube API, and GitHub-hosted automation.
- Validate environment-driven configuration:
  - Run the backend with a clean `.env` or equivalent environment variables and confirm that missing values fail fast with clear errors.
  - Test both valid and invalid database URLs, invalid API keys, and missing JWT secrets to ensure startup behavior is deterministic.
  - Verify that the frontend can resolve its backend endpoint through the configured environment variables and that Vite dev and production builds complete successfully.
- Exercise startup and initialization behavior:
  - Start the FastAPI service and confirm the app completes artifact loading for CatBoost models, PCA transforms, and CLIP encoders before serving requests.
  - Check the startup path where the application loads the model stack once, which is expected to be slow on first run and should remain within a reasonable threshold for the deployed environment.
  - Call `GET /health` and `GET /forecast/health` to verify application readiness and service health after configuration changes.
- Validate pipeline and database configuration:
  - Use Docker Compose to bring up the PostgreSQL and Airflow services and confirm that container health checks pass.
  - Verify database initialization scripts run in the correct order and that the schema supports the ETL, app, and analytics features described by the project.
  - Test the connection string path used by both the backend and ETL jobs to ensure a single configuration model is used consistently.
- Test configuration under constrained resources:
  - Run the backend and frontend in minimal-memory conditions to observe whether model loading, database access, and API requests remain functional.
  - Test the project in a low-usage environment with reduced browser and system memory, while monitoring CPU, memory, and startup logs.
  - Simulate partial service failure conditions, such as missing Supabase connectivity, failed YouTube quota, or unavailable model artifacts, to verify that configuration errors are surfaced clearly and that the app fails safely.
- Test user-facing integration across runtime layers:
  - Validate frontend login/signup flow against the backend, especially where JWT authentication and user-channel binding depend on valid configuration.
  - Verify prediction generation works when the channel context is present or absent, to ensure minimal configuration still allows graceful fallback behavior.
  - Confirm that the app remains usable when the database is reachable but external data sources are limited or temporarily unavailable.

**Oracles:**
The outcomes will be observed through both framework-native indicators and application-level health checks. For the FastAPI service, the primary oracles are startup logs, successful boot of the application, and the availability of health endpoints. A successful backend configuration will show that required environment variables are present, the Postgres connection is valid, and the model artifacts load without missing-file or dependency errors. The backend should respond to `GET /health` and `GET /forecast/health` without failing when the environment is correctly configured.

For the React frontend, successful configuration is indicated by a clean `npm run build` and a working local `npm run dev` or preview flow, with no build-time dependency or path-resolution failures. Vite will generate production artifacts only when the app is configured correctly, while the browser should be able to reach the backend without CORS or endpoint mismatches.

For the data layer, PostgreSQL and Docker health checks provide the observable success criteria. If the database connection string is wrong, startup should fail early; if the services are healthy, the app should be able to read and write configuration-critical data and should not silently continue in a partially initialized state. Resource usage will be measured via process memory consumption, container health output, and runtime logs; the application should remain within reasonable memory and disk expectations for a Python ML + web stack and should not consume excessive memory during startup or routine prediction calls.

The project’s configuration model is currently explicit and mostly manual, with environment variables handled directly by Python and the frontend environment. This means the primary oracles are environment validation, runtime health signals, and system resource monitoring rather than framework-level abstraction features such as a full dependency injection system or cloud-managed config service.

**Required Tools:**
- Python environment manager: `python -m venv`, `pip install -r requirements.txt`
- Backend runtime: `uvicorn main:app --reload`
- Frontend toolchain: `npm install`, `npm run dev`, `npm run build`, `npm run preview`
- Database validation: `psql` for Postgres connectivity and migration checks, plus Supabase SQL editor or equivalent
- Container orchestration: Docker Desktop / Docker Engine and `docker compose` for the ETL stack
- Service validation: `curl` or equivalent HTTP client for `/health`, `/forecast/health`, `/docs`
- Monitoring/inspection:
  - Windows Task Manager / Resource Monitor or Linux `top` / `htop`
  - Docker resource view (`docker stats`) for container memory and CPU behavior
  - backend logs and startup output for artifact-load timing and errors
- Config and secrets management:
  - `.env` files and environment variables for backend and frontend
  - Python `dotenv` loading behavior in backend configuration, plus local secret generation methods such as `python -c "import secrets; print(secrets.token_hex(32))"`

**Success Criteria:**
A configuration test is successful when all critical components can start and operate with a valid, documented configuration without manual patching or ad hoc code changes. The successful configuration for this project includes:

- The backend starts cleanly with all required environment variables set.
- The frontend builds and serves successfully with the configured API base URL.
- The PostgreSQL database is reachable through the configured Supabase URL and the project schema initializes correctly.
- The ETL pipeline can run under Docker Compose with healthy container states and proper service dependencies.
- The application loads the required CatBoost and embedding artifacts without missing dependency or file errors.
- Health endpoints are responsive, and the app remains operational under expected user load.
- Memory and disk usage remain within reasonable bounds for a Python ML service using embedding models and historical channel data.
- The system fails predictably and with clear diagnostics when a required configuration item is missing or invalid.

For this project, a reasonable operating target is the documented “minimum viable” configuration: Python 3.10+, Node.js 18+, a working Postgres/Supabase connection, internet access for the YouTube API, and sufficient disk space for model artifacts and first-run CLIP model downloads. The app should not require excessive resources beyond a typical local development or small deployment footprint, and cold-start delays should be acceptable for an ML-backed backend that loads several large model assets.

**Special Considerations:**
- The architecture depends on several third-party services and libraries:
  - Supabase/PostgreSQL for the application database and ETL data store
  - YouTube Data API v3 for channel metadata and forecast baselines
  - Docker-based ETL services for Airflow and Postgres
  - CatBoost, scikit-learn, and CLIP embedding models for ML inference
- Configuration is only partly framework-managed. The backend is intentionally strict: it raises runtime errors if required environment variables are absent, which is a desirable validation pattern for system-critical settings. The frontend is also dependent on explicit environment configuration for the API URL. These settings are mostly manual and should be documented clearly for operators.
- This project is not a fully cloud-native or containerized full-stack system in production; it is a local/dev-friendly stack with a deployed data layer and a Python API. Therefore, configuration testing should include both local developer setups and a realistic hosted configuration using Supabase and the backend’s environment variables.
- Hardware and OS constraints should be considered:
  - Development should be validated on at least one Windows-based environment and one Unix-like environment if the team supports both.
  - The app requires external network access for API calls and model downloads on first run.
  - Long-term configuration testing should account for quota limits from the YouTube API and ensure fallback behavior is graceful when external calls are throttled or unavailable.
- The ETL pipeline and frontend build process are comparatively more configuration-sensitive than the application logic itself. In particular, a small misconfiguration in the database URL, API key, or Node environment can prevent the system from starting even though the code itself is correct.

This approach ensures that configuration testing validates the real deployment characteristics of TrendCast: a Python ML service, a React dashboard, a shared Postgres database, and a Dockerized pipeline that all depend on correct environment setup, external service access, and resource-aware runtime behavior.
