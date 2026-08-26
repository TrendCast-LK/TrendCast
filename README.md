# TrendCast

TrendCast helps Sri Lankan YouTube creators guess how a video will
perform **before they upload it**.

You give it a title, a thumbnail, and a planned upload time. It gives
you back a 7-day view forecast, based on a model trained on real view
histories from tracked channels.

## How it works, in short

1. A pipeline pulls channel and video stats from YouTube into a database.
2. That data trains a model offline (title + thumbnail + channel stats →
   predicted view growth).
3. A backend loads the trained model and serves forecasts.
4. A web dashboard lets creators sign up, connect their channel, and run
   predictions.

## Architecture

```
YouTube Data API v3
        │
        ▼
GitHub Actions (scheduled jobs, plain Python + Postgres — no Kafka/Spark)
  • Job 1: channel + new-video ingestion, every 12h
  • Job 2: view-count polling, every 5 min (faster for brand-new videos)
  • Job 3: caches title/thumbnail embeddings, every 12h
        │
        ▼
Supabase / PostgreSQL
  channel_stats · videos · view_timeseries · video_features
  users · predictions · notifications
        │
        ├──────────────► ml/  (offline, run by hand)
        │                 extract → fit curves → build features
        │                 → train XGBoost models
        │                         │
        │                         ▼ (model files, copied by hand)
        ▼
backend/ (FastAPI)  ──serves──►  frontend/ (React, "ViewCast")
  loads the trained model,           sign up, connect a channel,
  serves /forecast + full            run predictions, see trends
  app API (auth, predictions,
  notifications)
```

All parts share one database, via the `SUPABASE_DB_URL` connection string.

## Tech stack

| Layer | Tech |
| --- | --- |
| Data collection | GitHub Actions (cron) + Python, YouTube Data API v3 |
| Database | Supabase (PostgreSQL + pgvector) |
| Model training | Python, XGBoost, LaBSE (title embeddings), CLIP (thumbnail embeddings), scikit-learn PCA |
| Backend API | FastAPI, psycopg2, JWT auth |
| Frontend | React 19 + Vite, Tailwind CSS, Chart.js |

## Repo layout

| Folder | What it is |
| --- | --- |
| [youtube-etl-pipeline/](youtube-etl-pipeline/README.md) | Data collection jobs (GitHub Actions), database schema |
| [backend/](backend/README.md) | FastAPI service — serves forecasts, auth, predictions, notifications |
| [ml/](ml/README.md) | Offline pipeline that trains the forecast model |
| [frontend/](frontend/README.md) | React dashboard ("ViewCast") |
| [CLAUDE.md](CLAUDE.md) | Full database table reference |

## Quickstart

Get the backend and frontend running locally. (You need a Supabase/Postgres
database already set up and reachable — see
[youtube-etl-pipeline/README.md](youtube-etl-pipeline/README.md) for the
schema.)

```bash
# Backend
cd backend
python -m venv .venv
.venv\Scripts\activate          # Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # fill in SUPABASE_DB_URL, YOUTUBE_API_KEY, JWT_SECRET_KEY
uvicorn main:app --reload       # http://127.0.0.1:8000 — takes ~20s to load ML models
```

```bash
# Frontend, in a second terminal
cd frontend
npm install
cp .env.example .env            # points at http://localhost:8000 by default
npm run dev                     # http://localhost:5173
```

Open `http://localhost:5173`, sign up with a real YouTube channel URL, and
run a prediction.

Each part's README has the full details — env vars, endpoints, key files,
known gaps.

## Status

**Working today:**

- All three data-collection jobs run live on GitHub Actions.
- `/forecast` and `/predictions` call the real trained model (not a stub)
  — see `backend/inference.py`.
- Full account system: signup/login, per-user channel binding, saved
  predictions, notifications.
- Feature-computation logic (embeddings, PCA, feature assembly) is shared
  between training (`ml/`) and serving (`backend/`) through `ml/services/`
  — this was a refactor to stop the two from drifting apart, and it's
  merged to `main`.
- A `video_features` table caches title/thumbnail embeddings so future
  retraining doesn't have to re-embed every video — also merged to `main`.

**Manual / not automated:**

- Retraining the model (`ml/train_model.py` and the steps before it) is
  run by hand locally. Nothing schedules it.
- After retraining, the new model files are copied by hand from
  `ml/models/` into `backend/models/`.
- The `ml/` training scripts don't yet read from the new `video_features`
  cache — they still compute embeddings from scratch each time. Wiring
  that up is planned but not done.

**Not built:**

- No admin or monitoring dashboard. The frontend only has creator-facing
  pages.
- No automated test suite for the backend or frontend. The ETL pipeline
  has one small test file (`youtube-etl-pipeline/tests/test_key_pool.py`).

**A prediction's `confidence` number is a heuristic** (fixed at 0.85 or
0.55 depending on whether a real channel match was found), not a model
uncertainty estimate. See `backend/routers/predictions.py`.

**Note on the ETL folder:** `youtube-etl-pipeline/` also contains an older
Kafka + Spark + Airflow setup (via `docker-compose.yml`). That is **not**
what runs in production — see that folder's README for details.
