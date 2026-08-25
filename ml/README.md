# ml/

Data audit and extraction for the 7-day view-trajectory model. Everything
here is **read-only** against the Supabase/Postgres database — no writes.

## Setup

```
pip install -r ml/requirements.txt
```

Connects using `SUPABASE_DB_URL`, loaded from `backend/.env` (same variable
and `.env` file the backend and ETL jobs use — nothing to configure here).

## Scripts

- **`audit_data.py`** — prints a coverage / data-quality / usable-set summary
  to stdout. Run this first to see how much training data currently exists.

  ```
  python ml/audit_data.py
  ```

- **`extract_dataset.py`** — writes the usable video set to
  `ml/data/videos.csv` (one row per video, joined with `channel_stats_enriched`
  channel features) and `ml/data/view_timeseries.csv` (long-format
  observations for those videos).

  ```
  python ml/extract_dataset.py
  ```

- **`common.py`** — shared read-only DB access and the "usable video"
  definition, used by both scripts so the audit numbers and the extracted
  dataset always agree.

- **`fit_curves.py`** — reads `ml/data/view_timeseries.csv` and
  `ml/data/videos.csv`, resamples each video onto a regular 0-168h grid
  (hourly for 48h, then every 6h), fits a saturating exponential
  `V(t) = V_inf * (1 - exp(-t/tau))` per video, and writes
  `ml/data/curve_params.csv` plus a diagnostics report to stdout (fit
  success rate, R² distribution, and an identifiability check for videos
  that haven't visibly saturated within 7 days). Local file I/O only, no DB.

  ```
  python ml/fit_curves.py
  ```

- **`plot_fits.py`** — samples 12 videos across the R² range (worst /
  median / best) and saves `ml/data/fit_examples.png`, observed points
  against the fitted curve.

  ```
  python ml/plot_fits.py
  ```

- **`sweep_first_obs.py`** — standalone diagnostic (touches the DB, unlike
  `fit_curves.py`/`plot_fits.py`) that sweeps the first-observation-lag
  threshold across `common.FIRST_OBS_SWEEP_HOURS` and reports, at each
  candidate value, survivor count, fit quality (median R², median tau of
  converged fits), and channel diversity. This is what the
  `FIRST_OBS_MAX_HOURS` default in `common.py` is justified against; rerun it
  if that default ever changes.

  ```
  python ml/sweep_first_obs.py
  ```

## "Usable" video definition

A video is included in the training set if it meets all of:

- observation span (published_at → latest scraped_at) >= 7 days
- no `view_timeseries` row with `scraped_at < published_at` (old broken
  `published_at` data)
- non-null `title` and `thumbnail_url`
- largest single downward view-count dip <= 5% of the pre-dip value (YouTube
  legitimately revises counts down when filtering spam; retained videos have
  their `view_count` repaired to a running maximum rather than excluded)
- largest observation gap within the first 7 days <= 24h
- first real observation <= 12h after `published_at` (a video can pass the
  span filter while having zero coverage near publish - e.g. a pre-existing
  video only picked up once its channel was added to tracking; without this,
  curve_fit converges against a flat tail and reports a fitted `tau` with no
  real information behind it - 12h rather than a naive 6h because the sweep
  in `sweep_first_obs.py` shows 6h is unnecessarily costly for negligible
  fit-quality gain, see the comment above `FIRST_OBS_MAX_HOURS` in `common.py`)

See `common.py`'s `compute_audit()` for the exact stepwise filter pipeline
and `audit_data.py` for the full diagnostics (including threshold
sensitivity and the channel distribution of the usable set).

`ml/data/` (the CSV/PNG output) is gitignored — regenerate it locally with
`extract_dataset.py`, `fit_curves.py`, and `plot_fits.py` rather than
committing it.
