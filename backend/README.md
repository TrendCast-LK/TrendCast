# Backend

FastAPI service for TrendCast. It serves the trained forecast model, plus
all the app logic (accounts, saved predictions, notifications) for the
TrendCast frontend.

See the [root README](../README.md) for how this fits into the whole system.

## Setup

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate      # Windows. On Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env` with:

| Variable | What it's for |
| --- | --- |
| `SUPABASE_DB_URL` | Postgres connection string. Same database the ETL jobs write to. |
| `YOUTUBE_API_KEY` | Used to fetch a user's channel data at signup and on refresh, and to fetch channel history for the forecast baseline (S). |
| `JWT_SECRET_KEY` | Signs login tokens. Generate one with `python -c "import secrets; print(secrets.token_hex(32))"`. |

The app won't start without all three — `config.py` checks them at import time.

## Run it

```bash
uvicorn main:app --reload
```

Serves on `http://127.0.0.1:8000`.

**Startup is slow — about 30 seconds.** On boot, the app loads six CatBoost
models, the HistAttnV2 network and its scaler, two CLIP embedding models (text
and image), two PCA reducers, and the maturation curve from
`../ensemble_artifacts/` (see `inference.py`'s
`load_artifacts()`, called once at startup). `/forecast` and `/predictions`
will 503 until that finishes. Check `GET /forecast/health` to see when
it's ready.

## Key files

| File | What it does |
| --- | --- |
| `main.py` | App setup, CORS, `/health`, `/channels`, `/videos/{id}/timeseries`, `/forecast` |
| `inference.py` | Loads the CatBoost + HistAttnV2 ensemble from `../ensemble_artifacts/`, encodes the target video once (raw CLIP-512 for HistAttnV2, PCA-32 for CatBoost), blends the two `log(m)` predictions with `ensemble_weight`, and logs per-stage timings (`forecast_timing`). |
| `histattn.py` | The HistAttnV2 model class (matches `histattn_v2.pt` key for key) and its history-feature builders |
| `channel_cache.py` | The channel history cache HistAttnV2 reads: background warming at signup / channel refresh, cache-first reads, CatBoost-only fallback on a miss |
| `db.py` | Postgres connection pool (`psycopg2`) |
| `config.py` | Reads and validates env vars |
| `security.py` | Password hashing, JWT issue/verify, `get_current_user` dependency |
| `youtube.py` | Resolves a channel URL to channel data via the YouTube Data API |
| `storage.py` | Saves uploaded thumbnails/datasets to `uploads/`, served at `/uploads` |
| `models.py` | Pydantic request/response schemas (includes `ForecastRange` with low/high bounds) |
| `routers/auth.py` | Signup, login, profile, change password |
| `routers/channel.py` | Fetch/refresh the signed-in user's YouTube channel data |
| `routers/predictions.py` | Create/list/get/delete predictions — this is what calls `inference.py` |
| `routers/dashboard.py`, `routers/trends.py` | Summary stats for the dashboard and trends pages |
| `routers/notifications.py` | List/read notifications |
| `routers/admin.py` | Admin dashboard: separate admin login, overview stats, user management, audit log |
| `tools/create_admin.py` | CLI that creates/resets/disables admin accounts (the only way to make one) |

## Endpoints

**ETL-backed data (no auth):**

| Method & path | What it returns |
| --- | --- |
| `GET /health` | DB connectivity check |
| `GET /channels` | All tracked channels, with computed KPIs |
| `GET /channels/{channel_id}/videos` | Videos for one channel |
| `GET /videos/{video_id}/timeseries` | Raw view/like/comment history for one video |
| `GET /forecast/health` | Whether the ML models finished loading |
| `POST /forecast` | Run a forecast for a title + thumbnail URL + upload time; returns a point estimate and an uncertainty range (low/high) |

**App layer (needs `Authorization: Bearer <token>`, except signup/login):**

| Method & path | What it does |
| --- | --- |
| `POST /auth/signup`, `POST /auth/login` | Create account / log in |
| `GET /auth/me`, `PATCH /auth/me`, `POST /auth/change-password` | Profile |
| `GET /channel/me`, `POST /channel/refresh` | The user's own YouTube channel data |
| `GET /dashboard/summary`, `GET /trends/summary` | Stats for those two frontend pages |
| `GET/POST/DELETE /predictions` | Saved predictions (this is what runs the model) |
| `GET /notifications`, `POST /notifications/{id}/read`, `POST /notifications/read-all` | In-app notifications |

**Admin dashboard (needs an admin token from `POST /admin/auth/login`; app-user tokens are rejected):**

| Method & path | What it does |
| --- | --- |
| `POST /admin/auth/login`, `GET /admin/auth/me` | Admin sign-in (5 failed tries per email per 15 min, then 429) |
| `GET /admin/overview` | User/prediction counts, 30-day daily series, model split since `ensemble_since`, model status |
| `GET /admin/users`, `GET /admin/users/{id}` | Search/filter/paginate users; one user's profile, channel, predictions, admin activity |
| `POST /admin/users/{id}/disable`, `/enable` | Lock a user out (login and every request get 403) or let them back in |
| `POST /admin/users/{id}/refresh-channel`, `/clear-fetch-error` | Channel maintenance for a user |
| `DELETE /admin/users/{id}` | Delete a user and their data; body `{"confirm_email": "<their email>"}` |
| `GET /admin/audit-log` | Every admin login and change, newest first |

Create the first admin after applying migration 006: `python -m tools.create_admin --email you@example.com --name "Your Name"`.

## Test it's working

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/forecast/health
```

There's no automated test suite for the backend yet — testing today means
running it and hitting endpoints by hand (or through the frontend).

## Forecast accuracy

`tests/test_ensemble_artifacts.py` loads the exported artifacts and replays the
export scripts' reference cases through the serving code: CatBoost matches
`reference_predictions.json` to 1e-6, HistAttnV2 matches
`histattn_reference_predictions.json` to 1e-4, and the blend matches the
reference ensemble values. It needs the training corpus in `../artifacts/`.
The response includes a `range_7d` with low/high bounds (computed from
`residual_std` in `config.json`) for uncertainty visualization.

## Known gaps

- **Confidence is a heuristic, not a model output.** `predictions.py` sets it
  to a fixed 0.85 or 0.55 depending on whether a real channel was matched
  — the models don't produce a calibrated uncertainty estimate.
- **Admin dashboard is Phase 1 only.** Overview, users and the activity log exist; a predictions explorer, cache and model pages do not yet. The admin login throttle and the 30s admin-row cache are in-process (per worker).
- **YouTube API quota.** Fetching channel history on each forecast costs quota.
  The service caches per channel for 6 hours to mitigate this.
- **First forecast after signup is CatBoost-only.** The channel history cache
  warms in the background; until it lands, forecasts skip HistAttnV2 and store
  `used_channel_context = false`.
- **The uncertainty band uses CatBoost's residual spread.** No residual_std was
  exported for the ensemble, so `range_7d` is CatBoost's, even when blended.
