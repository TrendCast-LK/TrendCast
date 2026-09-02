# Backend

FastAPI service for TrendCast. It serves the trained forecast model, plus
all the app logic (accounts, saved predictions, notifications) for the
ViewCast frontend.

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

**Startup is slow — about 25 seconds.** On boot, the app loads six CatBoost
models, two CLIP embedding models (text and image), two PCA reducers, and
the maturation curve from `artifacts/` (see `inference.py`'s
`load_artifacts()`, called once at startup). `/forecast` and `/predictions`
will 503 until that finishes. Check `GET /forecast/health` to see when
it's ready.

## Key files

| File | What it does |
| --- | --- |
| `main.py` | App setup, CORS, `/health`, `/channels`, `/videos/{id}/timeseries`, `/forecast` |
| `inference.py` | Loads CatBoost models and CLIP embeddings from `../artifacts/`, caches channel history per channel (6h TTL), builds feature vectors in exact order, and runs forecasts with uncertainty ranges. |
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

## Test it's working

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/forecast/health
```

There's no automated test suite for the backend yet — testing today means
running it and hitting endpoints by hand (or through the frontend).

## Forecast accuracy

Validation against the exported reference set (20 held-out training rows)
shows a mean relative error of **0.24%** on the 7-day magnitude estimate.
The response includes a `range_7d` with low/high bounds (computed from
`residual_std` in `config.json`) for uncertainty visualization.

## Known gaps

- **Confidence is a heuristic, not a model output.** `predictions.py` sets it
  to a fixed 0.85 or 0.55 depending on whether a real channel was matched
  — the models don't produce a calibrated uncertainty estimate.
- **No admin/monitoring page.** There's no backend support for one either.
- **YouTube API quota.** Fetching channel history on each forecast costs quota.
  The service caches per channel for 6 hours to mitigate this.
