# youtube-etl-pipeline/

TrendCast's data collection pipeline. It pulls YouTube channel and video
stats and writes them into Supabase/Postgres.

See the [root README](../README.md) for how this fits into the whole system.

## What actually runs in production

Three scheduled jobs, run by GitHub Actions
([.github/workflows/youtube_etl.yml](../.github/workflows/youtube_etl.yml)
at the repo root — see "About the old Airflow/Kafka setup" below for why
that matters). Each job is a plain Python script in `youtube_extractor/`
that connects straight to Postgres with `psycopg2`. No Kafka, Spark, or
Airflow involved.

| Job | Script | Schedule | What it does |
| --- | --- | --- | --- |
| 1 — Channel Ingestion | `youtube_extractor/job1_channel_ingestion.py` | every 12h | Refreshes channel stats, discovers new video uploads, adds them to `videos`. |
| 2 — Timeseries Collector | `youtube_extractor/job2_timeseries_collector.py` | every 5 min | Polls videos that are due, records view/like/comment counts. Polls as often as every 5 min for a video's first hour, then backs off to hourly. |
| 3 — Embed New Videos | `youtube_extractor/embed_new_videos.py` | every 12h, right after Job 1 | Caches title/thumbnail embeddings for new videos in `video_features`, so `ml/`'s retraining doesn't have to re-embed everything from scratch each time. |

All three read `SUPABASE_DB_URL` from a GitHub Actions secret. Trigger any
of them by hand from the repo's Actions tab ("Run workflow"), or see
`trigger_collector.py` below.

## Setup — running a job locally

There's no `.env.example` in this folder — these scripts read env vars
directly (no `.env` file loading built in). Export them yourself, or reuse
`backend/.env`, which has the same `SUPABASE_DB_URL`:

```bash
cd youtube-etl-pipeline/youtube_extractor
pip install -r requirements.txt

export SUPABASE_DB_URL="postgresql://..."
export YOUTUBE_API_KEYS="key1,key2"   # or YOUTUBE_API_KEY for a single key

python job1_channel_ingestion.py
python job2_timeseries_collector.py
```

`embed_new_videos.py` also needs the heavier ML packages (`torch`,
`sentence-transformers`, `transformers`) — not in `requirements.txt` to
keep it lightweight. Install those separately, or reuse `ml/requirements.txt`.

## Key files

- `youtube_extractor/job1_channel_ingestion.py` — Job 1
- `youtube_extractor/job2_timeseries_collector.py` — Job 2
- `youtube_extractor/embed_new_videos.py` — Job 3
- `youtube_extractor/key_pool.py` — rotates across multiple YouTube API
  keys when one hits its daily quota
- `youtube_extractor/switch_channels.py` — archives current data and swaps
  in a new set of channels (see below)
- `youtube_extractor/backfill_video_metadata.py` — a one-off script (not
  run in CI) for videos ingested before metadata columns existed
- `youtube_extractor/trigger_collector.py` — triggers Job 2 on GitHub
  Actions through the API, without waiting for the schedule
- `postgres/init/*.sql` — the database schema, applied automatically on a
  fresh local Postgres container. On the real Supabase database, apply new
  migration files by hand: `psql "$SUPABASE_DB_URL" -f postgres/init/0X_....sql`.
  Full table reference: [../CLAUDE.md](../CLAUDE.md).

## Test it's working

```bash
pip install -r youtube_extractor/requirements.txt
python -m unittest discover tests
```

That covers `key_pool.py`'s API-key rotation logic — the only automated
test in this pipeline today. There's no test coverage for the jobs
themselves; verify those by running them against a scratch database and
checking the rows they write.

## Rotating to a new set of channels

To swap in a new set of channels while keeping the old data:

1. Prepare a CSV with columns: `channel_id, channel_title, country,
   subscriber_count, total_views, uploads_playlist_id`. See
   `youtube_extractor/channels_template.csv` for the format.
2. Preview it, then run it for real:
   ```bash
   python youtube_extractor/switch_channels.py --csv <file>.csv --dry-run
   python youtube_extractor/switch_channels.py --csv <file>.csv
   ```
   This copies `channel_stats`, `videos`, and `view_timeseries` into
   `*_archive` tables, clears the active tables, and seeds the new channels.
3. Re-trigger Job 1 to start ingesting the new channels.

Archived data stays queryable, e.g.
`SELECT * FROM channel_stats_archive ORDER BY archived_at DESC;`.

## API quota

The YouTube Data API v3 gives 10,000 units/day. A `channels.list` or
`videos.list` call costs 1 unit and covers up to 50 IDs, so quota use
stays low even at these schedules. Set several keys in `YOUTUBE_API_KEYS`
(comma-separated) to multiply the effective daily quota —
`key_pool.py` rotates to the next key automatically once one hits its limit.

## About the old Airflow/Kafka/Spark setup

This folder also has a `docker-compose.yml` that spins up Postgres +
Kafka + Spark + Airflow + Jupyter — an earlier, heavier design for this
pipeline. **It is not what runs in production.** Production is the three
GitHub Actions jobs above, talking straight to Postgres.

A few things worth knowing if you use it anyway:

- `airflow/dags/job1_channel_ingestion.py` and
  `job2_timeseries_collector.py` are **stale copies** of the real scripts
  in `youtube_extractor/` — they've drifted out of sync with recent fixes
  to the live scripts. Don't treat them as current.
- `youtube_extractor/extractor.py` (Kafka producer) and
  `spark/scripts/process_youtube_data.py` (Spark streaming job) belong to
  this same older design and aren't used by the GitHub Actions jobs.
- To run it locally: copy `.env`, fill in `YOUTUBE_API_KEYS`,
  `YOUTUBE_CHANNEL_IDS`, and `AIRFLOW__CORE__FERNET_KEY` (generate one with
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`),
  then `docker compose up --build -d`.

| Service | URL | Notes |
| --- | --- | --- |
| Airflow Webserver | http://localhost:8084 | admin / admin |
| Jupyter Lab | http://localhost:8888 | token from `.env` |
| Spark Master UI | http://localhost:8081 | — |
| PostgreSQL | localhost:5433 | local container — separate from the real Supabase database |
| Kafka | localhost:29092 | — |

Treat `youtube_extractor/job1_channel_ingestion.py`,
`job2_timeseries_collector.py`, `embed_new_videos.py`, and the GitHub
Actions workflow as the source of truth for how data collection actually
works today.
