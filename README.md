# TrendCast

TrendCast forecasts the first seven days of views for a YouTube video
**before it is published**, built for Sri Lankan creators.

A creator signs up with their channel URL, enters a planned video's title,
tags, duration, publish time and thumbnail, and gets back a 7-day view
forecast: a daily accumulation curve, a total, and an uncertainty range.

## How the forecast works

The forecast is decomposed as **N(t) = S × m × F(t)**:

- **S, the channel anchor:** the median 7-day-equivalent views of the
  channel's recent uploads, fetched live from the YouTube Data API.
- **m, the multiplier:** how far this video is expected to beat or miss the
  channel's norm. It is a blend of two models in log space:
  - a CatBoost model over pre-publish features (video metadata,
    multilingual CLIP title and thumbnail embeddings, channel statistics);
  - HistAttnV2, an attention network that compares the new video with the
    channel's last 20 uploads.

  If the channel's history isn't cached yet, the forecast uses CatBoost alone.
- **F(t), the curve shape:** a logistic or power-law accumulation curve whose
  family and parameters are predicted by CatBoost models.

The trained models are in [ensemble_artifacts/](ensemble_artifacts/), along
with the scripts that exported them.

## Architecture

```
 React frontend ──/api──► FastAPI backend ──────► Supabase / PostgreSQL
 (creator pages +         auth, predictions,      users · predictions · notifications
  /admin dashboard)       admin, forecast engine   channel history cache · admins
                                │
                                ├──► YouTube Data API v3 (channel data, recent uploads)
                                └──► ensemble_artifacts/ (CatBoost + HistAttnV2, loaded at start-up)
```

## Repo layout

| Folder | What it is |
| --- | --- |
| [backend/](backend/README.md) | FastAPI service: the forecast engine, auth, predictions, notifications, admin API, database schema and tests |
| [frontend/](frontend/README.md) | React app: creator pages and the admin dashboard |
| [ensemble_artifacts/](ensemble_artifacts/) | Trained models, PCA transforms, configuration and the export scripts that produced them |

## Tech stack

| Layer | Tech |
| --- | --- |
| Frontend | React 19, Vite, Tailwind CSS, Chart.js, React Router |
| Backend | FastAPI, psycopg2, bcrypt, PyJWT |
| Model serving | CatBoost, PyTorch (HistAttnV2), sentence-transformers (CLIP and multilingual CLIP), scikit-learn PCA |
| Database | PostgreSQL (Supabase) |
| Deployment | Docker Compose (nginx frontend + backend) |

## Running it

### Prerequisites

- A PostgreSQL database: a free [Supabase](https://supabase.com) project, or local Postgres
- A [YouTube Data API v3 key](https://console.cloud.google.com/apis/library/youtube.googleapis.com)
- Either Docker Desktop (option A), or Python 3.12 and Node.js 22 (option B)
- ~3 GB free disk and internet on first run (PyTorch, and the CLIP models
  downloaded from Hugging Face, cached afterwards)

### 1. Set up the database

Apply the SQL files in [backend/schema/init/](backend/schema/init/) in order:

```bash
for f in backend/schema/init/0*.sql; do psql "$SUPABASE_DB_URL" -f "$f"; done
```

(Without `psql`, paste each file into the Supabase SQL editor.) The tables
are described in [backend/schema/README.md](backend/schema/README.md).

### 2. Configure the backend

```bash
cp backend/.env.example backend/.env      # Windows: copy backend\.env.example backend\.env
```

| Variable | Value |
| --- | --- |
| `SUPABASE_DB_URL` | Postgres connection URL |
| `YOUTUBE_API_KEY` | your YouTube Data API key |
| `JWT_SECRET_KEY` | run `python -c "import secrets; print(secrets.token_hex(32))"` |

### Option A: Docker

With Docker Desktop running, from the repo root:

```bash
docker compose up --build       # first build ~10-20 min (PyTorch + CLIP models)
```

Open `http://localhost:8080`. The backend needs ~30 s after starting to load
the models. `docker compose down` stops it.

### Option B: run the backend and frontend directly

Backend (first terminal):

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload       # http://127.0.0.1:8000, ~30 s to load the models
```

Frontend (second terminal):

```bash
cd frontend
npm install
cp .env.example .env            # points at http://localhost:8000
npm run dev                     # http://localhost:5173
```

### 3. Use it

Open the app, sign up with a YouTube channel URL, and run a prediction. The
channel needs at least five public uploads for a forecast.

To use the admin dashboard (`/admin/login`), create an admin account from
`backend/` (with its dependencies installed):

```bash
python -m tools.create_admin --email you@example.com --name "Your Name"
```

### Troubleshooting

| Symptom | Fix |
| --- | --- |
| `... environment variable is not set` on backend start | Fill in all three values in `backend/.env` |
| `/forecast/health` reports a missing artifact | `ensemble_artifacts/` must contain the `.cbm`, `.pkl`, `.pt` and `.json` files from the repo |
| First start is very slow | It's downloading the CLIP models; later starts use the cache |
| Frontend can't reach the API | Check `VITE_API_URL` in `frontend/.env` and that the backend is running |
| Prediction fails with "YouTube API limit reached" | The API key's daily quota is used up; try again the next day or use another key |

## Tests

The backend tests need Docker (they start a throwaway Postgres container):

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest --ignore=tests/load
```

See [backend/tests/DB_TESTING.md](backend/tests/DB_TESTING.md) for what each
test file covers, and the test reports in [backend/tests/](backend/tests/).

GitHub Actions runs the backend tests and the frontend lint and build on
every pull request and push to `main` ([ci.yml](.github/workflows/ci.yml)),
and checks the model artifacts when they change ([model.yml](.github/workflows/model.yml)).

## Known limitations

- A prediction's `confidence` is a heuristic derived from the width of the
  uncertainty range, not a calibrated model output.
- The uncertainty range uses CatBoost's held-out residual spread and covers
  the 7-day total only.
- The first forecast after sign-up may be CatBoost-only while the channel
  history cache warms in the background.
- The training data (~1 GB) is not in the repo; the export scripts in
  `ensemble_artifacts/` need it to retrain.
