# ml/

The offline training pipeline for TrendCast's forecast model. Turns raw
data from Postgres into trained model artifacts that `backend/` loads to
serve `/forecast`.

See the [root README](../README.md) for how this fits into the whole system.

Everything here runs **locally, by hand**. There is no scheduled retrain —
someone runs these scripts in order when they want a new model.

## Setup

```bash
pip install -r ml/requirements.txt
```

Reads `SUPABASE_DB_URL` from `backend/.env` (same variable and file the
backend and ETL jobs use — nothing extra to configure here).

## The full pipeline, in order

Run these one after another. Each step writes files that the next step
reads (all in `ml/data/`, which is gitignored — regenerate it locally,
don't commit it).

| # | Script | What it does |
| --- | --- | --- |
| 1 | `audit_data.py` | Prints a data-quality summary. Run this first to see how much usable training data exists. |
| 2 | `extract_dataset.py` | Pulls the usable video set from Postgres into `ml/data/videos.csv` and `ml/data/view_timeseries.csv`. |
| 3 | `fit_curves.py` | Fits a saturating-growth curve (`V(t) = V_inf * (1 - exp(-t/tau))`) to each video's view history. Writes `ml/data/curve_params.csv`. |
| 4 | `features_simple.py` | Builds simple numeric features (title length, upload hour, channel stats, ...) into `ml/data/features_simple.csv`. |
| 5 | `download_thumbnails.py` | Downloads each video's thumbnail image to `ml/data/thumbnails/`. Resumable — skips files already on disk. |
| 6 | `embed_titles.py` | Encodes each title into a LaBSE vector (768-dim). Writes `ml/data/title_embeddings.npy`. |
| 7 | `embed_thumbnails.py` | Encodes each thumbnail into a CLIP vector (512-dim). Writes `ml/data/thumbnail_embeddings.npy`. |
| 8 | `build_features.py` | Joins everything, runs PCA on both embedding sets (768→40, 512→40), one-hot encodes categories, and writes the final training table `ml/data/features.csv` plus the PCA/encoder artifacts. |
| 9 | `train_model.py` | Trains two XGBoost models (one for `V_inf`, one for `tau`), evaluates against baselines, and writes the final model artifacts. |

Extra diagnostic scripts, not part of the required order:

- `plot_fits.py` — samples 12 videos and saves a chart of curve fits vs.
  real data, `ml/data/fit_examples.png`.
- `sweep_first_obs.py` — the diagnostic that justified the
  `FIRST_OBS_MAX_HOURS` default in `common.py`. Only needs re-running if
  that default changes.

## Where the model artifacts end up

`build_features.py` and `train_model.py` write to `ml/models/`:

| File | What it is |
| --- | --- |
| `title_pca.joblib`, `thumbnail_pca.joblib` | Fitted PCA reducers |
| `categorical_encoder.joblib` | Fitted one-hot encoder |
| `channel_medians.json` | Per-channel median V_inf, for the inference fallback |
| `vinf_model.joblib`, `tau_model.joblib` | The trained XGBoost models |
| `evaluation.json` | Test-set metrics vs. baselines |

**After training, these files must be copied by hand into
`backend/models/`** for the API to pick up the new model. Nothing
automates this today.

## Shared code (`ml/services/`)

Feature-computation logic used by *both* this training pipeline and
`backend/inference.py` at request time, so training and serving can never
drift apart:

- `title_embedding.py` — loads LaBSE, encodes titles
- `thumbnail_embedding.py` — loads CLIP, encodes images
- `pca.py` — applies a fitted PCA transform
- `tabular_features.py` — upload-time conversion, title/tag/duration parsing
- `feature_row.py` — assembles one full feature row, name-sanitized to
  match what the trained model expects

If you're changing how a feature is computed, change it here, not in
`ml/embed_titles.py` or `backend/inference.py` directly.

## Other scripts

- **`common.py`** — shared read-only DB access and the "usable video"
  definition (see below), used by `audit_data.py` and `extract_dataset.py`
  so their numbers always agree.

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

## Known gaps

- **Retraining is fully manual.** No scheduled job runs this pipeline —
  someone runs the 9 steps above by hand, then copies artifacts into
  `backend/models/` by hand too.
- **The `video_features` embedding cache isn't used here yet.** The ETL
  side now caches title/thumbnail embeddings in a `video_features` Supabase
  table (see
  [../youtube-etl-pipeline/README.md](../youtube-etl-pipeline/README.md))
  so future retrains don't need to re-embed every video. `embed_titles.py`
  and `embed_thumbnails.py` still compute embeddings from scratch locally
  and don't read from that table — wiring them together is planned but not
  done.
