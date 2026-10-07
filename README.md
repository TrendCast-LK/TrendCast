# TrendCast

TrendCast helps Sri Lankan YouTube creators guess how a video will
perform **before they upload it**.

You give it a title, a thumbnail, and a planned upload time. It gives
you back a 7-day view forecast, based on a model trained on real view
histories from tracked channels.

## How it works, in short

1. A model was trained offline on view histories collected from tracked
   channels (title + thumbnail + channel history → predicted view growth).
2. A backend loads the trained model and serves forecasts.
3. A web dashboard lets creators sign up, connect their channel, and run
   predictions.

## Architecture

```
YouTube Data API v3 ──► backend/ (FastAPI) ──serves──► frontend/ (React)
  (user's channel +       loads the trained         sign up, connect a
   recent uploads)        ensemble, serves           channel, run
                          /forecast + app API        predictions, see trends
                                │
                                ▼
                      Supabase / PostgreSQL
                      users · predictions · notifications
                      channel_history_cache · admins

ensemble_artifacts/  ◄── trained offline, exported by hand
```

The backend reaches the database through the `SUPABASE_DB_URL` connection string.

## Tech stack

| Layer | Tech |
| --- | --- |
| Database | Supabase (PostgreSQL) |
| Model training | Python, CatBoost, sentence-transformers (CLIP + multilingual CLIP for embeddings), scikit-learn PCA |
| Model serving | FastAPI, CatBoost, CLIP embeddings, cached PCA transforms, 6h channel history cache |
| Frontend | React 19 + Vite, Tailwind CSS, Chart.js |

## Repo layout

| Folder | What it is |
| --- | --- |
| [backend/](backend/README.md) | FastAPI service — serves forecasts, auth, predictions, notifications |
| [ml/](ml/README.md) | Offline pipeline that trains the forecast model |
| [frontend/](frontend/README.md) | React dashboard ("TrendCast") |
| [CLAUDE.md](CLAUDE.md) | Full database table reference |

## Quickstart (run it yourself)

Everything needed to *run* the app is in this repo — the trained forecast
models (`artifacts/*.cbm`, `*.pkl`, `*.json`, ~2 MB) are committed. Only the
large *training* data is left out (see [Training data](#training-data-not-in-the-repo)).

### Prerequisites

- Python 3.10+ and Node.js 18+
- A PostgreSQL database (a free [Supabase](https://supabase.com) project, or local Postgres)
- A [YouTube Data API v3 key](https://console.cloud.google.com/apis/library/youtube.googleapis.com)
- ~3 GB free disk and internet on first run (installs PyTorch and downloads the
  CLIP models from Hugging Face, cached afterwards)

### 1. Set up the database

Apply the SQL files in [backend/schema/init/](backend/schema/init/)
in order (`01` → `06`). `03_app_backend.sql` creates the `users`, `predictions`
and `notifications` tables the app needs; `06_admin.sql` adds the admin
dashboard's accounts and audit log.

```bash
for f in backend/schema/init/0*.sql; do psql "$SUPABASE_DB_URL" -f "$f"; done
```

(Without `psql`, paste each file into the Supabase SQL editor.)

To use the admin dashboard (`/admin/login`), create an admin account from
`backend/` once the backend's dependencies are installed (step 2):
`python -m tools.create_admin --email you@example.com --name "Your Name"`.

### Option A: run everything with Docker

With [Docker Desktop](https://www.docker.com/products/docker-desktop/) running
and `backend/.env` filled in (see the table in step 2), from the repo root:

```bash
docker compose up --build       # first build ~10-20 min (PyTorch + CLIP models); later builds reuse layers
```

Open `http://localhost:8080`. The backend needs ~30s after start to load the
models. `docker compose down` stops it; after changing code, run
`docker compose up --build` again. nginx in the frontend container forwards
`/api/*` to the backend, so no `frontend/.env` or CORS setup is needed, and
uploads are kept in `backend/uploads/` (shared with a local run).

For day-to-day coding the two-terminal setup below (steps 2 and 3) is
quicker, since it reloads on every save.

### Option B: run the backend and frontend directly

### 2. Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # Windows: copy .env.example .env
```

Edit `backend/.env`:

| Variable | Value |
| --- | --- |
| `SUPABASE_DB_URL` | Postgres connection URL |
| `YOUTUBE_API_KEY` | your YouTube Data API key |
| `JWT_SECRET_KEY` | run `python -c "import secrets; print(secrets.token_hex(32))"` |

```bash
uvicorn main:app --reload       # http://127.0.0.1:8000 — ~25s to load models on start
```

Check it: `http://127.0.0.1:8000/docs` should load.

### 3. Frontend (second terminal)

```bash
cd frontend
npm install
cp .env.example .env            # points at http://localhost:8000 by default
npm run dev                     # http://localhost:5173
```

Open `http://localhost:5173`, sign up with a YouTube channel URL, and run a prediction.

### Troubleshooting

| Symptom | Fix |
| --- | --- |
| `... environment variable is not set` on backend start | Fill in all three values in `backend/.env` |
| `missing artifact: .../artifacts/...` | Make sure you cloned the full repo; `artifacts/` must contain the `.cbm`/`.pkl`/`.json` files |
| First start is very slow | It's downloading the CLIP models; later starts use the cache |
| Frontend can't reach the API | Check `VITE_API_URL` in `frontend/.env` and that the backend is running |

Each part's README has the full details — env vars, endpoints, key files,
known gaps.

## CI

GitHub Actions runs on every pull request and every push to `main`:

- [ci.yml](.github/workflows/ci.yml): backend tests (a throwaway Postgres in
  Docker, the model stubbed) and the frontend lint and build.
- [model.yml](.github/workflows/model.yml): loads the real ensemble from
  `ensemble_artifacts/` and checks it; runs only when the model files or the
  inference code change.

Backend dependencies are pinned in `backend/requirements.txt`, so CI, Docker
and a local install get the same versions.

## Training data (not in the repo)

The datasets used to train the model (`artifacts/*.csv`, `artifacts/*.npy` —
~350 MB) are excluded from git via `.gitignore`. You **don't need them to run
the app**. To retrain, regenerate them with the pipeline in
[ml/](ml/README.md) (extract from the database → build features → train →
`artifacts/export_artifacts.py`), or ask the maintainers for the data bundle.

## Status

**Working today:**

- The forecast model is trained offline with CatBoost (`artifacts/export_artifacts.py`):
  - Six models: magnitude (V_inf multiplier), shape family (logistic vs power), and four shape parameters (k, t0, c, theta).
  - Reference validation: **0.24% mean relative error** on 20 held-out rows.
- `/forecast` and `/predictions` call the real trained models (not a stub) — see `backend/inference.py`.
  - Loads artifacts once at startup (~25s).
  - Returns point estimate + uncertainty range (low/high bounds).
  - Fetches channel history from YouTube API to compute the baseline (S), cached 6h per channel.
- Full account system: signup/login, per-user channel binding, saved predictions, notifications.
- Feature-computation logic (embeddings, PCA, feature assembly) is shared between training (`ml/`) and serving (`backend/`) through `ml/services/` — prevents feature drift.

**Manual / not automated:**

- Retraining the model (the full ML pipeline up to `export_artifacts.py`) is run by hand locally. Nothing schedules it.
- The `artifacts/` folder contains the trained CatBoost models and PCA transforms (committed, small); they're loaded directly by the backend at startup. Large training CSV/NPY files are git-ignored.
- The `ml/` training scripts don't yet read from the new `video_features` embedding cache — they still compute embeddings from scratch each time. Wiring that up is planned but not done.

**Not built:**

- No admin or monitoring dashboard. The frontend only has creator-facing pages.

**Notes:**

- A prediction's `confidence` number is a heuristic (fixed at 0.85 or 0.55 depending on whether a real channel match was found), not a model uncertainty estimate. See `backend/routers/predictions.py`.
- The uncertainty range in the forecast response is a fixed-width approximation based on residual_std from training, not a calibrated prediction interval.
