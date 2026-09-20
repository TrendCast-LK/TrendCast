# TrendCast Model — Integration Guide

**Audience:** an engineer or coding agent wiring this model into the web app.
**Goal:** serve a pre-publish view forecast for a YouTube video, given its
thumbnail, title, tags, duration, and channel ID.

Read Part 6 (Pitfalls) before writing code. Several of the failure modes are
silent — they produce plausible-looking numbers that are wrong.

---

## 1. What the model does

Given a video that has **not been published yet**, forecast its cumulative view
count for each of the next 7 days.

The channel may be one the model has never seen. Nothing in the inference path
depends on the channel having been in training data.

### The decomposition

```
N(t) = S × m × F(t)
```

| Term | Meaning | How it is obtained |
|---|---|---|
| `N(t)` | Cumulative views at day t | The output |
| `S` | Channel anchor — what a typical video from this channel gets in 7 days | **Arithmetic.** Computed live from the YouTube API. Not learned. |
| `m` | Magnitude multiplier — how this video performs relative to the channel's norm | **Predicted** by a CatBoost regressor |
| `F(t)` | Normalised shape curve, 0 → 1 | **Predicted** by CatBoost classifier + regressors |

`F(7) = 1` by construction, so `N(7) = S × m`. The shape only affects days 1–6.

### Honest capability statement

Do not present this as an exact view-count predictor. Its measured behaviour:

- **Absolute forecast R² = 0.665** — but the channel anchor alone gives 0.646.
  Content features contribute roughly +0.02.
- **Within-channel ranking:** Spearman 0.254; top-20% precision 0.310 versus
  0.200 chance.

The product claim that the numbers support is: *"here is a likely range, and
here is which of your videos will probably overperform"* — not a point estimate.

**The UI must show an interval.** See Part 4.5.

---

## 2. Artifacts

Everything below lives in `artifacts/`. All of it was produced during training
and must be loaded as-is — none of it may be recomputed at inference.

| File | What it is | Required |
|---|---|---|
| `catboost_magnitude.cbm` | Regressor → `log(m)` | Yes |
| `catboost_shape_form.cbm` | Classifier → curve family (1 = logistic, 0 = power) | Yes |
| `catboost_shape_c.cbm` | Regressor → power-law `c` | Yes |
| `catboost_shape_theta.cbm` | Regressor → power-law `theta` | Yes |
| `catboost_shape_k.cbm` | Regressor → logistic `k` | Yes |
| `catboost_shape_t0.cbm` | Regressor → logistic `t0` | Yes |
| `pca_text.pkl` | Fitted PCA, 512 → 32, for text embeddings | Yes |
| `pca_image.pkl` | Fitted PCA, 512 → 32, for image embeddings | Yes |
| `feature_columns.json` | Exact ordered list of the 94 feature names | Yes |
| `maturation_curve.json` | `g(d)` for d = 1..7 | Yes |
| `config.json` | Clip bounds, residual std, category list, misc constants | Yes |

If any of these are missing, **stop and ask** rather than substituting a
recomputed version. A refitted PCA will silently produce wrong predictions.

### Models downloaded at runtime (not in `artifacts/`)

Both from `sentence-transformers`, cached on first use:

- `clip-ViT-B-32` — thumbnail encoder → 512-dim
- `clip-ViT-B-32-multilingual-v1` — text encoder → 512-dim

These two are trained to share an embedding space, which is what makes the
thumbnail–title alignment feature meaningful. **Do not substitute one without
the other.**

The multilingual text encoder is not optional: ~58% of the corpus titles are
non-Latin script (mostly Sinhala).

---

## 3. Suggested architecture

The model needs Python (CatBoost, PyTorch, sentence-transformers, scikit-learn).
If the web app is Node/Next.js, it cannot run in-process.

```
Web app  ──HTTP──►  FastAPI service (Python)
                     ├─ CLIP encoders (loaded once at startup)
                     ├─ CatBoost models (loaded once at startup)
                     ├─ PCA transforms (loaded once at startup)
                     └─ YouTube Data API (called per request)
```

**Load everything once at process start.** CLIP takes 10–15 s to load. Loading
per request makes every forecast unusable. Use FastAPI's lifespan/startup hook
and keep the models in module-level state.

Expect **200–400 ms of CPU time per thumbnail** for CLIP inference. That is the
dominant cost in the request. Acceptable for a click-to-forecast flow; not
acceptable if you intended sub-100 ms.

---

## 4. The inference pipeline

### 4.1 Fetch channel history

One call to the channel's uploads playlist; take the **last 30 uploads**.
For each: `video_id`, `published_at`, `view_count`.

Efficient approach: `channels.list(part=contentDetails)` to get the uploads
playlist ID, then `playlistItems.list`, then one batched `videos.list`
(up to 50 IDs per call) for statistics.

**Guard:** if fewer than 5 prior uploads exist, return a structured refusal.
`S` cannot be estimated reliably and the forecast would be meaningless. Do not
fall back to a global average — say the channel has insufficient history.

**Cache this per channel** with roughly a 6-hour TTL. It changes slowly, and
the YouTube quota is 10,000 units/day per key.

### 4.2 Compute S and channel features

```python
# g comes from artifacts/maturation_curve.json -- ALWAYS read it from the file,
# never hardcode. Current exported values:
# g = {1: 0.560, 2: 0.762, 3: 0.872, 4: 0.933, 5: 0.965, 6: 0.988, 7: 1.000}

equivalents = []
for v in past_videos:                     # the 30 fetched uploads
    age_days = (now - v.published_at).days
    if age_days < 1:
        continue                          # too immature to rescale
    frac = g[min(max(age_days, 1), 7)]
    equivalents.append(v.view_count / frac)

if len(equivalents) < 5:
    raise InsufficientHistory()

S = median(equivalents)                                  # NOT mean
log_equiv = [log(max(e, 1)) for e in equivalents]
channel_log_volatility = median_absolute_deviation(log_equiv)
channel_median_views   = S
channel_view_std       = std(equivalents)
channel_video_count    = len(past_videos)
channel_category_diversity = count_distinct_categories(past_videos)
```

**Median, not mean.** View distributions are heavy-tailed; a single viral video
would drag a mean far away from the channel's typical output.

### 4.3 Encode the content

```python
img_512  = image_model.encode([thumbnail_pil])[0]        # clip-ViT-B-32
text_str = f"{title}. {title}. {' '.join(tags[:10])}"    # title repeated on purpose
txt_512  = text_model.encode([text_str])[0]              # multilingual

alignment = cosine_similarity(img_512, txt_512)          # scalar feature

img_32 = pca_image.transform(img_512.reshape(1, -1))[0]  # loaded, never refitted
txt_32 = pca_text.transform(txt_512.reshape(1, -1))[0]
```

The title is duplicated in `text_str` to weight it above the tags in the pooled
embedding. This is an engineering choice, not a published technique, but it must
be reproduced exactly because the model was trained on features built this way.

**If the thumbnail fails to download or decode:** use the mean image embedding
stored in `config.json` and set `has_thumbnail = 0`, `alignment = 0.0`. Do not
use a zero vector — zero is a specific, unusual point in embedding space and the
model will interpret it as a real (weird) thumbnail.

### 4.4 Assemble features and predict

Build a single row in **exactly** the order given by `feature_columns.json`.
CatBoost matches features positionally; a reordered vector produces confident
nonsense with no error raised.

```
duration_s, title_length, description_length, tag_count,
publish_hour_sin, publish_hour_cos, publish_dow_sin, publish_dow_cos,
thumbnail_title_alignment, has_thumbnail,
text_pc0..text_pc31, img_pc0..img_pc31,
cat_1, cat_2, ... (one-hot over the category list in config.json),
channel_video_count, channel_median_views, channel_view_std,
channel_log_volatility, channel_category_diversity
```

Notes on individual features:

- `duration_s` — parse ISO 8601 (`PT8M32S` → 512). This is the single most
  important feature in the model (importance 14.4), so parsing bugs here matter
  more than anywhere else.
- Publish time is encoded as `sin/cos` of hour-of-day and day-of-week so that
  hour 23 and hour 0 are adjacent rather than maximally distant. Use the
  **intended** publish time supplied by the user.
- Category one-hot must cover exactly the categories listed in `config.json`.
  An unseen category means all zeros — acceptable, do not invent a column.

Then:

```python
log_m   = magnitude_model.predict(row)[0]
log_m   = clip(log_m, config["log_m_min"], config["log_m_max"])
m       = exp(log_m)

is_logistic = shape_form_model.predict(row)[0] == 1
if is_logistic:
    k  = clip(shape_k_model.predict(row)[0], 0.05, 10)
    t0 = clip(shape_t0_model.predict(row)[0], -5, 7)
    F = lambda t: sigmoid(k * (t - t0)) / sigmoid(k * (7 - t0))
else:
    c  = clip(shape_c_model.predict(row)[0], 0.05, 100)
    th = clip(shape_theta_model.predict(row)[0], 0.05, 20)
    F = lambda t: 1 - (1 + t / c) ** (-th)

curve = [S * m * F(t) for t in range(1, 8)]
curve = enforce_monotonic(curve)     # cumulative views cannot decrease
curve[6] = S * m                     # F(7) = 1 exactly
```

The clip bounds are not cosmetic — unconstrained regressors occasionally emit
degenerate parameters that produce nonsensical curves.

### 4.5 Uncertainty band — required

The model does not yet produce calibrated quantiles. Until it does, derive a
band from the validation residual spread (`residual_std` in `config.json`):

```python
lower = S * exp(log_m - 0.8 * residual_std)
upper = S * exp(log_m + 0.8 * residual_std)
```

> **Check `residual_std` before shipping.** The export script computes it
> in-sample (the model scoring its own training rows), which gives ~1.42. The
> correct value, measured on the channel-disjoint validation split, is ~1.09.
> If `config.json` says 1.42, overwrite it with 1.09 — otherwise every band
> will be about 30% wider than the evidence supports.

This band is wide — roughly 0.4× to 2.4× the point estimate. That width is
honest. Do not narrow it for presentational reasons.

**The UI must display the range as the primary number**, with the point estimate
secondary. Presenting a single figure would overstate what the model can do.

---

## 5. API contract

### Request

```
POST /api/forecast
Content-Type: multipart/form-data

thumbnail:      file (jpg/png)
title:          string
tags:           string[]
duration:       string   (ISO 8601, e.g. "PT8M32S")
channel_id:     string   (UC...)
publish_time:   string   (ISO 8601 datetime, intended publish time)
```

### Response — success

```json
{
  "status": "ok",
  "channel_baseline": 6800,
  "multiplier": 1.40,
  "point_estimate_7d": 9520,
  "range_7d": { "low": 4000, "high": 22700 },
  "curve": [
    { "day": 1, "views": 5236 },
    { "day": 2, "views": 7045 },
    { "day": 3, "views": 8187 },
    { "day": 4, "views": 8854 },
    { "day": 5, "views": 9139 },
    { "day": 6, "views": 9425 },
    { "day": 7, "views": 9520 }
  ],
  "shape_family": "logistic",
  "day1_fraction": 0.55,
  "based_on_videos": 30
}
```

`day1_fraction` is worth surfacing in the UI — it tells the creator whether the
video is front-loaded (promote immediately) or slow-building.

### Response — insufficient history

```json
{
  "status": "insufficient_history",
  "message": "This channel has fewer than 5 prior uploads, so we cannot estimate a reliable baseline.",
  "videos_found": 2
}
```

### Other failure modes

| Condition | Status |
|---|---|
| Channel not found / private | `channel_not_found` |
| YouTube quota exhausted | `quota_exceeded` (503) |
| Thumbnail unreadable | proceed with mean embedding, add `"warnings": ["thumbnail_unavailable"]` |

---

## 6. Pitfalls

These are ordered by how much damage they cause and how hard they are to notice.

1. **Refitting PCA at inference.** Must load `pca_text.pkl` and `pca_image.pkl`.
   A PCA fitted on a single row, or on new data, produces a completely different
   projection. No error is raised; predictions are simply wrong.

2. **Feature order.** CatBoost is positional. Build the row from
   `feature_columns.json` and assert the length matches before predicting.

3. **Loading CLIP per request.** 10–15 s each time. Load once at startup.

4. **Using mean instead of median for S.** Heavy-tailed distribution; a mean is
   dragged badly by one viral video.

5. **Zero vector for missing thumbnails.** Use the stored mean embedding.

6. **Forgetting to clip.** Both `log_m` and the shape parameters. Unclipped
   values occasionally produce absurd curves.

7. **Not caching channel history.** Burns YouTube quota fast; the data changes
   slowly.

8. **Presenting a point estimate alone.** The model's precision does not support
   it. Show the range.

9. **Using `created_at`-style timestamps for age.** Video age must be computed
   from `published_at`. (In the original data pipeline, an uploads-playlist
   timestamp could predate publication, which broke age calculations.)

10. **Timezones.** All timestamps UTC. A local-time publish hour shifts the
    `sin/cos` features and changes the prediction.

---

## 7. Sanity test before shipping

Take any video already in `training_corpus_h7_final.csv`, feed its metadata
through the live inference path, and compare `point_estimate_7d` against the
value the notebook produced for the same row.

They should match to within a few percent. If they do not, the most likely
causes in order are: PCA not loaded, feature order wrong, duration parsed
incorrectly, or timezone handling.

This check catches almost every integration bug in Part 6 and takes ten minutes.

---

## 8. Known limitations to keep in mind

- **Channel features are not point-in-time.** During training they were computed
  over the full corpus rather than only prior videos. Minor leakage; the served
  model still works, but reported metrics are slightly optimistic.
- **The curve-family classifier is weak** (AUC 0.579). It mostly predicts the
  majority class. The shape gain comes from the parameter regressors, not the
  family choice.
- **All quoted metrics are validation, not test.** The test split has not been
  used.
- **Horizon is fixed at 7 days.** Videos that go viral after week one are
  structurally outside the model's scope.
- **Quantile outputs are not implemented.** The current band is a fixed-width
  approximation, not a calibrated interval.