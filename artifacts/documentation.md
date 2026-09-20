# TrendCast — Complete Methodology Record

**Purpose of this document:** a learning record, not a report. It explains _why_ every
decision was made, what alternatives were rejected, which attempts failed, and what the
numbers actually mean. Where something is an assumption rather than an established fact,
it says so.

---

## Part 0 — The Problem and Why It Is Framed This Way

### 0.1 The product question

Forecast how many views a YouTube video will get, for creators in the Sri Lankan
viewer base.

### 0.2 The framing decision that shaped everything

There are two versions of this problem:

|                  | Post-publish forecasting                       | Pre-publish forecasting                          |
| ---------------- | ---------------------------------------------- | ------------------------------------------------ |
| Input available  | Video is live, early views observable          | Only thumbnail, title, tags, channel history     |
| Difficulty       | Much easier                                    | Much harder                                      |
| Value to creator | Low — outcome already fixed, nothing to change | High — can still change thumbnail, title, timing |

We chose **pre-publish**. This is a deliberate choice to take the harder problem because
it is the one that produces a useful product. It is also, in machine-learning terms, a
**cold-start problem**: predicting for an item with no interaction history.

### 0.3 The second cold-start layer (discovered later, important)

Initially we thought of this as _video_ cold start — new video, known channel. Partway
through it became clear the real requirement is _channel_ cold start: a user submitting
a video will usually be from a channel that is **not in our training corpus at all**.

This changed several design decisions:

- No channel-ID embeddings anywhere (they only work for channels seen in training)
- Every channel feature must be computable at request time from one API call
- Train/validation/test splits must be **channel-disjoint**, not video-disjoint

### 0.4 What the literature said before we started

Key reference: **SMTPD (CVPR 2025)** — 282K YouTube videos, daily popularity for 30 days,
multimodal features. Their critical ablation:

- With day-1 popularity as input: MAE ≈ 0.72, Spearman ≈ 0.96
- Without it (true cold start): MAE ≈ 1.56–1.67, Spearman ≈ 0.85

**Interpretation:** error roughly doubles without early observation. This told us up front
that a modest cold-start result is the expected outcome, not a failure. It also told us
that the eventual Phase 2 (adding early views) would produce a large jump.

Other references that shaped the design:

- **Wu, Rizoiu & Xie (ICWSM 2018)** — channel-level context explains most of cold-start
  engagement (R²=0.77 in their setting). Motivated separating channel scale from content.
- **Rizoiu et al., HIP (WWW 2017)** — power-law memory kernels for attention decay.
  Motivated the parametric curve approach.
- **MMRA (SIGIR 2024)** — retrieval augmentation for popularity prediction. We tested
  this idea; it did not transfer (see Part 7).

---

## Part 1 — The Core Model Architecture

### 1.1 The decomposition

```
N(t) = S × m × F(t)
```

| Term     | Meaning                                                                       | How obtained                             |
| -------- | ----------------------------------------------------------------------------- | ---------------------------------------- |
| **N(t)** | Cumulative views at time t                                                    | The thing being predicted                |
| **S**    | Channel anchor — what a typical video from this channel gets                  | Computed arithmetically, **not learned** |
| **m**    | Magnitude multiplier — how this video performs relative to its channel's norm | **Predicted by a model**                 |
| **F(t)** | Normalised shape curve, rises 0 → 1                                           | **Predicted by a model**                 |

### 1.2 Why this decomposition

Three reasons, in order of importance:

1. **S is computable for any channel, including unseen ones.** One YouTube API call gets
   the channel's recent uploads and their view counts. No learning required. This is what
   makes channel cold start tractable.

2. **It removes the largest, easiest source of variance from the learning problem.**
   Absolute view counts vary by orders of magnitude across channels. Most of that variance
   is "which channel is this" — which we can compute directly. What remains (`m`) is the
   genuinely interesting question: does _this particular video_ over- or under-perform?

3. **It collapses the output space.** Predicting a full 7-day trajectory means predicting
   many correlated numbers. Predicting `m` plus 2 curve parameters means predicting 3
   numbers, with monotonicity and smoothness guaranteed by construction. Far more
   sample-efficient given a limited corpus.

### 1.3 The assumption this rests on — and how it was tested

The decomposition assumes **shape is independent of magnitude**: a video's curve
_shape_ shouldn't depend on how _big_ it gets.

This is an assumption we made, not something taken from a paper. It was tested
empirically (Part 5.4): correlations between fitted shape parameters and `log(m)` came
out **under 0.05 across all parameters**. The assumption holds on this data.

**Important honesty note:** the `S × m × F(t)` formulation as a whole is not a published
formula. The ingredients are standard (shape/scale separation, per-series normalisation,
parametric growth curves), but this specific arrangement is our own synthesis. It should
be presented as a design choice validated on our data, not as a method borrowed from
literature.

---

## Part 2 — Data Collection (Our Own Pipeline)

### 2.1 What we built

An automated scraper (GitHub Actions) with two distinct jobs:

- **Discovery job** — checks tracked channels for newly published videos
- **Tracking job** — polls view/like/comment counts for videos already known

Polling schedule for tracking:

- Every **5 minutes** for videos under 2 hours old
- Every **hour** thereafter

### 2.2 The bug we found

The `created_at` column showed videos being discovered up to **14 hours after publish**,
and timestamps clustered at identical values across many videos (e.g. `03:51:57.327570`).

**Diagnosis:** the discovery job ran **once daily**, not continuously. A video published
at 14:00 waited until the next day's run. So despite the 5-minute polling tier existing,
most videos never received it — they entered tracking already hours old.

**Additional finding:** some `created_at` values were _before_ `published_at` (negative
lag). Explanation: `created_at` comes from the uploads playlist, which can list a video
before it is publicly published. So `created_at` is not a reliable proxy for discovery
time at all.

**Correct measure:** age of the video at its **first actual row in the timeseries table**.
This is ground truth for "do we have the launch burst."

### 2.3 Data quality outcome

After filtering to videos with a true first scrape within 2h of publish, plus horizon
coverage:

| Metric                                   | Value                                 |
| ---------------------------------------- | ------------------------------------- |
| Total videos in our corpus               | 10,747                                |
| Passing day-0 + horizon + status filters | ~1,250                                |
| **Distinct channels**                    | **37**                                |
| Most dominant channel                    | 409 videos (33% of corpus)            |
| Scrape density                           | median max-gap 1.25h — genuinely good |

**The killing constraint: 37 channels.** For a system that must generalise to unseen
channels, the effective sample size is the number of channels, not videos. 37 is far
too few, and one channel contributing a third of the data makes it worse.

This is why the corpus was supplemented (Part 3).

### 2.4 Recommended fixes for future collection

- Increase discovery frequency to every 15–30 minutes (worst-case lag 24h → 30min)
- Or switch to **WebSub/PubSubHubbub** push notifications — near-zero lag, near-zero quota
- Quota maths with 3 API keys (30,000 units/day):
  `discovery_units_per_day = n_channels × (1440 / interval_minutes)`
  At 100 channels / 15 min = 9,600 units/day (fine). At 500 channels / 15 min =
  48,000 units/day (over budget). Channel count and discovery frequency must be sized together.
- Tail polling (2h → 120h) at hourly resolution is wasted precision; every 4–6h is
  sufficient for the tail and frees large amounts of quota.

---

## Part 3 — The External Dataset

### 3.1 What it is

A second dataset from another group: **206,620 videos, 1,084 channels**, daily view
counts (`day_1_views` … `day_30_views`), plus full metadata.

Overlap with our channels: only 13. So it added **1,071 new channels** — exactly the
diversity our own corpus lacked.

### 3.2 Cleaning performed

| Issue                                        | Finding                                                                                                                       | Action                                                                                                      |
| -------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Day-to-day view decreases                    | 32,589 videos had at least one; median drop only 0.19% of prior day                                                           | Small drops = rounding noise → fixed with `cummax`                                                          |
| Large drops (>20%)                           | 561 videos                                                                                                                    | Excluded from label training (kept usable for other purposes)                                               |
| Dominant channel                             | One news broadcaster (Hiru News), 46,485 videos = 22.5% of corpus, ~124 clips/day, all category 25, mostly 48s news bulletins | **Capped every channel at 50 videos** → top channel share fell to 0.2%                                      |
| Zero views at day 7                          | 274 videos                                                                                                                    | Verified genuine (current `view_count` also ~0, not a scraping bug) → excluded, since `log(0)` is undefined |
| Duplicates, invalid categories, future dates | None found                                                                                                                    | —                                                                                                           |

### 3.3 Why capping mattered

Without the cap, gradient updates would be dominated by one high-frequency news channel
whose upload dynamics (124 short clips/day, breaking-news driven) are nothing like a
typical creator's. The model would learn that channel's patterns, not transferable ones.

### 3.4 Decision: keep the two corpora separate

Our corpus is hourly-resolution to 5 days; the external is daily to 30 days. Rather than
force a merge, we used the external dataset alone for model development. Rationale:

- Daily resolution is sufficient for magnitude (`m` only needs the horizon endpoint)
- Daily resolution is _insufficient_ for the launch-burst parameter τ (day 1 aggregates
  the whole burst into one number)
- Our hourly corpus remains the only source able to resolve sub-day dynamics — reserved
  for future work

**Consequence:** the two-component burst+tail curve originally designed (`w, τ, c, θ`)
was reduced to a **two-parameter** form, because τ is not identifiable from daily data.

---

## Part 4 — Label Construction

### 4.1 Horizon choice

**H = 7 days.** Chosen because it is a common industry benchmark and gives 7 daily points
to fit a curve. (Earlier candidates: 5 days, 6 days — switched to 7 once the 30-day
external dataset made it affordable.)

### 4.2 The maturation curve g(d)

Needed because a channel's past videos are at different ages and aren't directly comparable.

```
g(d) = median over corpus of [ views_at_day_d / views_at_day_7 ]
```

Fitted on the training corpus. Result:

| d    | 1     | 2     | 3     | 4     | 5     | 6     | 7     |
| ---- | ----- | ----- | ----- | ----- | ----- | ----- | ----- |
| g(d) | 0.810 | 0.933 | 0.972 | 0.987 | 0.994 | 0.998 | 1.000 |

**Key empirical finding: 81% of week-one views arrive in the first 24 hours.** Extremely
front-loaded. This shaped later decisions about curve family (Part 5.3).

### 4.3 Computing S (the channel anchor)

For each video, using **only that channel's videos published strictly before it**:

```python
def compute_S(channel_videos, publish_time, H=7, N=30, min_prior=5):
    past = [v for v in channel_videos if v.publish_time < publish_time][-N:]
    equiv = []
    for v in past:
        age_days = (publish_time - v.publish_time).days
        if age_days < 1:          # too immature to extrapolate
            continue
        equiv.append(v.views_so_far / g(min(age_days, H)))
    if len(equiv) < min_prior:
        return None               # drop this row
    return median(equiv)
```

**Design points:**

- **Median, not mean** — view distributions are heavy-tailed; one viral video would
  drag a mean far off the channel's typical output
- **Point-in-time** — using later videos would leak future information into the label
  and make validation scores fiction
- **min_prior = 5** — fewer priors gives an unreliable anchor. This requirement dropped
  the corpus from 1,072 usable channels to **786**.

Also computed: `channel_log_volatility = MAD(log(equiv))` — how erratic the channel is.

### 4.4 The magnitude label

```
m = N(7) / S
label = log(m)
```

Then clipped at the 0.5th and 99.5th percentiles to stop extreme outliers dominating
the loss.

**Why log:** forecast errors are multiplicative. Missing a 1,000-view video by 1,000 and
a 1,000,000-view video by 1,000 are not equally bad. Log space penalises a 2× miss
identically everywhere.

**Validation of S:** `log(m)` came out with mean −0.063, median −0.037, **skew −0.033**.
Near-perfect symmetry around zero is strong evidence the anchor is well calibrated —
a biased S would show visible skew.

### 4.5 Final corpus

|                                     |                            |
| ----------------------------------- | -------------------------- |
| Videos with day_1…day_7 present     | 200,562                    |
| After capping at 50/channel         | 26,059                     |
| After requiring valid S (≥5 priors) | 22,902                     |
| After removing zero-view videos     | **22,628**                 |
| Distinct channels                   | **786**                    |
| Non-Latin script titles             | **57.7%** (mostly Sinhala) |

---

## Part 5 — The Shape Curve F(t)

### 5.1 What F(t) is

The fraction of the video's 7-day views that have arrived by time t. `F(0)=0`, `F(7)=1`,
monotone increasing.

**Note:** because F(7)=1 by construction, at exactly the horizon `N(7) = S × m`. Shape
only matters for intermediate days.

### 5.2 First attempt: single power-law

```
F(t) = 1 - (1 + t/c)^(-θ)
```

Fitted per video by least squares. Result: **median R² = 0.94**, only 44% of videos
above 0.95. Below our threshold.

### 5.3 Diagnosis and fix: hybrid form

The problem: with 81% of views in day 1, the curve is extremely front-loaded, and a
single power-law cannot fit both the steep rise and the slow tail for every video.

Tested a logistic alternative:

```
F(t) = sigmoid(k(t - t₀)) / sigmoid(k(7 - t₀))
```

Head-to-head on a 2,000-video sample:

|                 | Power-law | Logistic  |
| --------------- | --------- | --------- |
| Median R²       | 0.947     | **0.978** |
| 25th percentile | 0.879     | **0.942** |
| Worst case      | −1.38     | −3.06     |

Logistic wins on average, especially at the lower tail, but is less stable on
degenerate cases.

**Solution: fit both per video, keep whichever fits better.**

Final result across all 22,628 videos:

- **Median R² = 0.981**
- **93.7% of videos above R² = 0.8**
- Split: **68% logistic, 32% power-law**

That split is a genuine empirical finding — videos fall into two distinct arrival patterns.

### 5.4 Testing the factorization assumption

Correlation of each fitted shape parameter against `log(m)`:

| Family   | Parameter | Correlation with log(m) |
| -------- | --------- | ----------------------- |
| Power    | θ         | −0.006                  |
| Power    | c         | +0.041                  |
| Logistic | k         | −0.036                  |
| Logistic | t₀        | +0.002                  |

All negligible. **Shape and magnitude are independent — the decomposition is valid.**

(Minor secondary effect: logistic-form videos skew slightly higher magnitude, median
log(m)=0.021 vs −0.116 for power-form. Too weak to change the design.)

### 5.5 Bugs encountered during fitting

- **`log(0) = -inf`** from 274 zero-view videos → traced, verified genuine, excluded
- **Division-by-zero in R²** for videos with constant daily ratios (e.g. all views on
  day 1, zero growth after). Correct R² for a perfectly matched flat series is 1.0, not
  undefined → guarded with a zero-variance check

---

## Part 6 — Features

### 6.1 Thumbnail embeddings

**Model:** `clip-ViT-B-32` (sentence-transformers) → 512-dim vector per image.

### 6.2 Text embeddings

**Model:** `clip-ViT-B-32-multilingual-v1` → 512-dim vector.

**Why multilingual specifically:** 57.7% of titles use non-Latin script (mostly Sinhala).
An English-only encoder would badly underserve most of the corpus. SMTPD measured this
exact substitution and found English-only BERT cost ~0.19 MAE versus multilingual.

**Why paired with the CLIP image model:** these two are trained so their embeddings share
the same vector space. That makes step 6.3 possible for free.

**Input construction:** `title + title + top-10 tags`. The title is repeated to weight it
above tags in the pooled embedding.

> **Honesty note:** the title-repetition trick is an engineering hack of ours, not a
> published technique. A cleaner alternative is to encode title and tags separately and
> combine with an explicit weighted average (e.g. 0.7 × title + 0.3 × tags), because then
> the weighting is a stated, defensible number rather than a side effect of string
> duplication. Not yet changed, because re-encoding costs ~26 min.

**Description was deliberately excluded** — for most videos it is boilerplate (sponsor
text, channel links, hashtag dumps), so it would add noise rather than signal.
32.9% of descriptions are missing anyway.

### 6.3 Thumbnail–title alignment

Cosine similarity between a video's own image and text embeddings. One scalar measuring
"does the thumbnail visually match what the title promises." Free, because both encoders
share a space.

### 6.4 Tabular features

- `duration_s` (parsed from ISO 8601, e.g. `PT2M46S`)
- `title_length`, `description_length`, `tag_count`
- Publish time as **sin/cos of hour-of-day and day-of-week**

**Why sin/cos:** so the model understands hour 23 and hour 0 are adjacent, and Sunday
and Monday are adjacent, rather than treating them as maximally distant integers.

### 6.5 Channel features

Computed from the channel's videos: `channel_video_count`, `channel_median_views`,
`channel_view_std`, `channel_log_volatility`, `channel_category_diversity`.

> **Known limitation:** these are computed over the whole corpus rather than point-in-time
> (unlike S, which is correctly point-in-time). This introduces a small amount of leakage.
> It was accepted as a diagnostic shortcut and should be fixed before final reporting.

### 6.6 PCA compression

512+512 dims compressed to **32+32** via PCA for training speed. This is a speed shortcut
for the baseline, not a permanent design choice — full-dimension embeddings are saved
and available.

### 6.7 Engineering problems solved

- Naive sequential thumbnail download projected to ~3 hours → fixed with a **32-worker
  thread pool + persistent HTTP session**, reducing to ~90 min
- Remaining time was CPU-bound CLIP inference, not fixable without a GPU
- 2,818 downloads (12.5%) failed on first pass; verified transient (URLs return 200 on
  retry), not dead links. Remaining failures filled with the **mean embedding** (neutral)
  rather than zeros (which would read as a specific, unusual point in embedding space)

---

## Part 7 — Modelling: What Worked and What Did Not

### 7.1 Evaluation protocol

**Channel-disjoint split** (70/15/15) via `GroupShuffleSplit` on `channel_id`.

This is non-negotiable: a random video-level split would place videos from the same
channel in both train and validation, letting the model memorise channel scale and
producing badly inflated scores. With channel cold start as the requirement, only a
channel-disjoint split measures what we care about.

Result: Train 19,620 / Val 1,395 (59 channels) / Test held out.

### 7.2 Attempt 1 — CatBoost on flat features

| Model                             | MAE       | R²        |
| --------------------------------- | --------- | --------- |
| Baseline (predict m=1 always)     | 1.130     | 0.000     |
| CatBoost, content only            | 1.111     | 0.034     |
| CatBoost, content + channel stats | **1.105** | **0.045** |

Feature importances put `duration_s` (14.1) and channel stats (8.7, 5.6, 4.4) far above
any embedding dimension (top image PC: 3.1).

### 7.3 Attempt 2 — Deep neural network

Motivation: dense embeddings have geometric structure a tree cannot exploit.

Architecture: MLP, 128→64→32→1, BatchNorm + Dropout, Adam lr=1e-3, L1 loss.

**Result: MAE 1.134, R² 0.001** — worse than CatBoost, worse than baseline. Best
validation score was at _epoch 0_, meaning it started overfitting immediately.

Second attempt with fixes: lr → 3e-4, removed BatchNorm (known to interact badly with
Dropout), added `ReduceLROnPlateau`, gradient clipping, smaller net, more patience.

**Result: MAE 1.130, R² 0.002.** Train loss fell steadily (1.158 → 1.113) while
validation didn't move — textbook memorisation without generalisation.

**Conclusion:** gradient-boosted trees outperform plain neural networks on flat tabular
data. This is a well-documented pattern, and it is a legitimate reportable finding rather
than a failed experiment.

### 7.4 The crisis point and the diagnosis

At this stage R² ≈ 0.045 looked like "no signal at all," and the project felt stuck.

**The actual problem was the metric, not the model.**

`log(m)` is a _residual_ — the variance left after already removing channel scale via S.
Reporting R² on it in isolation measures only the hardest sub-component, while the
system's actual output `N̂(7) = S × m̂ × F(t)` had never been evaluated.

### 7.5 The end-to-end evaluation (the turning point)

Scored in log space (views are heavy-tailed, so raw-space R² would be dominated by a
few huge channels). Three-rung ablation ladder:

| Rung | What it is                        | R²        | MAE       |
| ---- | --------------------------------- | --------- | --------- |
| 0    | Global constant                   | −0.000    | 2.174     |
| 1    | **Channel anchor S alone (m=1)**  | 0.646     | 1.112     |
| 2    | **S × predicted m (full system)** | **0.662** | **1.087** |

Plus ranking metrics:

| Metric                                                   | Value                      |
| -------------------------------------------------------- | -------------------------- |
| Spearman, absolute views                                 | 0.822                      |
| **Spearman, residual log(m)** — the real cold-start test | **0.217** (p=2.9e-16)      |
| **Top-20% precision within channel**                     | **0.296** (chance = 0.200) |

**Why ranking metrics matter:** the popularity-prediction literature reports Spearman
and ranking metrics, not R², precisely because exact counts aren't predictable but
_ordering_ is both achievable and useful. `R²=0.045` and `ρ=0.217` describe the same
underlying signal — R² punishes magnitude error, Spearman rewards correct ordering.

### 7.6 Bootstrap validation

Resampled **by channel** (2,000 iterations), not by video.

**Why by channel:** videos from the same channel share an anchor, audience and style.
Resampling videos independently would treat 1,395 correlated observations as independent
and produce confidence intervals ~4–5× too narrow.

| Metric             | Estimate | 95% CI         | P(beats null) |
| ------------------ | -------- | -------------- | ------------- |
| End-to-end R²      | 0.649    | [0.534, 0.745] | —             |
| Content uplift ΔR² | +0.017   | [0.006, 0.033] | 1.000         |
| Spearman on log(m) | 0.216    | [0.135, 0.296] | 1.000         |
| Top-20% precision  | 0.295    | [0.239, 0.353] | 1.000         |

All three effects survive. Lower bound on precision (0.239) still clears chance (0.200).

### 7.7 Failed attempt — category shrinkage on S (instructive)

**Idea:** S is noisy for channels with few prior videos, and that noise enters the label
directly. Shrink S toward a category-level prior, weighted by evidence:
`log S* = w·log S_raw + (1−w)·log S_prior`, `w = n/(n+k)`.

**Apparent result:**

| k   | ρ         | R2_anchor | label_sd |
| --- | --------- | --------- | -------- |
| 0   | 0.219     | **0.646** | 1.591    |
| 40  | **0.751** | **0.291** | 2.259    |

ρ appeared to triple. **This was an artifact.**

**The mechanism:** shrinking S toward a category prior means S stops tracking the
channel's actual scale. That information doesn't vanish — it moves _into the label_.
`log(m)` stops meaning "how does this video do relative to its channel" and starts
meaning "how big is this channel relative to its category." The model then reads channel
scale straight off `channel_median_views`, which is already a feature.

**The tells:**

- `R2_anchor` **fell** (0.646 → 0.291) — the baseline was being sabotaged, and ΔR²
  inflated purely because of that
- `label_sd` **rose** (1.59 → 2.26) — the opposite of noise removal
- ρ climbed **monotonically with no optimum** — a real effect peaks and turns over
- `R2_e2e` barely moved (0.662 → 0.696) — the system wasn't actually better

**Lesson:** always check that the baseline is unchanged when comparing. A metric that
improves because its comparison point got worse is not an improvement.

### 7.8 Correct fixes for anchor noise

30% of videos rest on fewer than 10 prior videos, so anchor noise is real. Two approaches
that leave the label untouched:

**A) Filter** — restrict to well-anchored videos (changes the validation set, so compare
these rows only against each other):

| Subset           | ρ         | R2_e2e | R2_anchor | top20 |
| ---------------- | --------- | ------ | --------- | ----- |
| all              | 0.217     | 0.662  | 0.646     | 0.296 |
| n_prior ≥ 10     | 0.291     | 0.681  | 0.658     | 0.331 |
| **n_prior ≥ 15** | **0.335** | 0.706  | 0.674     | 0.289 |
| n_prior ≥ 20     | 0.295     | 0.732  | 0.704     | 0.261 |

ρ peaks at n≥15 and **turns over** at n≥20 — the signature of a real effect, unlike the
shrinkage sweep. R2_anchor _rises_ alongside, confirming this isn't the same artifact.

**Finding:** on channels with adequate history, cold-start ranking is meaningfully
better (ρ=0.335 vs 0.217).

**B) Sample weighting** — keep all rows, weight training examples by anchor reliability
`w = n/(n+k)`. Same validation set as baseline, so directly comparable:

| Config         | ρ         | top20     |
| -------------- | --------- | --------- |
| baseline       | 0.217     | 0.296     |
| **weight k=5** | **0.251** | **0.310** |
| weight k=10    | 0.205     | 0.299     |
| weight k=20    | 0.216     | 0.296     |

**Adopted: weight k=5.** Improves both metrics, keeps all data, leaves labels untouched.
Note the effect is narrow (k=10 and k=20 give nothing), so it should be bootstrapped
before being leaned on heavily.

### 7.9 Failed attempt — retrieval features

**Idea (from MMRA, SIGIR 2024):** for each video, find content-similar videos on _other_
channels and use their average `log(m)` as a feature.

**Three leakage rules enforced:**

1. Never retrieve same-channel neighbours — at inference on an unseen channel there are
   none, so allowing them teaches a shortcut that vanishes at deployment
2. Memory bank contains training rows only — otherwise validation labels leak into
   validation features
3. Neighbours must predate the query — otherwise the feature uses future information

**Result before modelling:** raw retrieval signal (similarity-weighted neighbour mean vs
true log m) = **Spearman 0.082** (p=0.002). Statistically non-zero but very weak.

**Result after modelling:**

| Config                | ρ         | top20     |
| --------------------- | --------- | --------- |
| **without retrieval** | **0.254** | **0.310** |
| with retrieval        | 0.212     | 0.307     |

Retrieval features ranked near the bottom of importance (0.57–1.26 vs duration at 14.4).

**Conclusion — a genuine negative result:** a published approach did not transfer to
this domain. Two candidate explanations, not separable with current evidence:

1. CLIP encodes _semantic content_, not _engagement appeal_ — it knows two thumbnails
   both show a kitchen; it doesn't know which gets clicked
2. Relative performance may be genuinely channel-specific — what overperforms for one
   audience may not transfer

### 7.10 Shape prediction

Approach: classify curve family, then regress that family's parameters, then
**evaluate on the reconstructed curve**, not on parameters.

**Why curve-space evaluation:** parameters interact nonlinearly, so small parameter
errors can mean large curve errors and vice versa. Parameter MAE would be misleading.

**Baseline:** predict the population-average curve for every video. This is a strong
baseline because arrival patterns are highly consistent (g(1)=0.81).

**Results:**

|                                     | MAE on F(t) |
| ----------------------------------- | ----------- |
| Baseline (population-average curve) | 0.1015      |
| Model                               | **0.0916**  |
| **Improvement**                     | **+9.7%**   |

Per-day:

| Day | Baseline | Model  | Improvement             |
| --- | -------- | ------ | ----------------------- |
| 1   | 0.2173   | 0.2056 | 5.4%                    |
| 2   | 0.1787   | 0.1656 | 7.3%                    |
| 3   | 0.1330   | 0.1145 | 14.0%                   |
| 4   | 0.0925   | 0.0781 | 15.6%                   |
| 5   | 0.0588   | 0.0506 | 14.0%                   |
| 6   | 0.0299   | 0.0267 | 10.7%                   |
| 7   | 0        | 0      | — (1.0 by construction) |

**Important caveat:** the curve-family classifier achieved **accuracy 0.694 vs majority
baseline 0.694, AUC 0.579** — it learned essentially nothing and predicts the majority
class. **The 9.7% gain comes from the parameter regressors, not family classification.**
Do not claim curve-family prediction works.

Also note: days 3–5 improve most in _relative_ terms, but day 1 has by far the largest
_absolute_ error (0.206 vs 0.027 at day 6) because it carries the most genuine variance.
Report both framings.

---

## Part 8 — Final State

### 8.1 Best configuration

- Corpus: 22,628 videos, 786 channels, external dataset only
- Labels: `log(m)` from point-in-time S, hybrid-fit shape parameters
- Features: CLIP thumbnail + multilingual CLIP text (PCA-32 each), alignment score,
  tabular, channel stats
- Model: CatBoost, MAE loss, **sample weighting k=5**, **no retrieval**
- Split: channel-disjoint

### 8.2 Results

| Metric                           | Value      | Note                                |
| -------------------------------- | ---------- | ----------------------------------- |
| End-to-end 7-day forecast R²     | 0.665      | Mostly S-driven                     |
| Content uplift over anchor       | +0.020 ΔR² | Small but bootstrap-confirmed       |
| Spearman on residual log(m)      | 0.254      | The genuine cold-start signal       |
| Top-20% precision within channel | 0.310      | vs 0.200 chance = +55% relative     |
| Shape prediction vs mean curve   | +9.7%      | Parameter regressors only           |
| Conditional on n_prior ≥ 15      | ρ = 0.335  | Better on well-established channels |

### 8.3 How to state this honestly

Two separate claims, both true, easily confused:

- **Absolute view forecasting is strong (R²=0.665)** — but the channel anchor does most
  of the work; content adds ~+0.02
- **Within-channel ranking is modest but real (ρ=0.254, precision 0.310)** — this is the
  genuine cold-start contribution and where the interesting claim lives

Do not let the 0.665 imply the content model carries it. The ablation says otherwise.

### 8.4 Negative results worth reporting

1. Plain neural networks underperform gradient-boosted trees on this flat tabular data
2. Retrieval augmentation (MMRA-style) does not transfer — content similarity carries
   only ρ=0.082 of engagement signal
3. Curve family is not predictable from pre-publish features (AUC 0.579)
4. Category shrinkage on S produces a metric artifact, not an improvement

### 8.5 Known limitations and open items

- **Channel features are not point-in-time** (unlike S) — small leakage, should be fixed
- **Title-repetition trick** in text embedding should be replaced with explicit weighting
- **PCA-32** compression not yet tested against full 512-dim
- **Weight k=5 gain not yet bootstrapped** — sits inside the earlier CI width
- **Test set never touched** — all reported numbers are validation
- Videos dormant then discovered later (viral after week 1) are structurally outside a
  7-day horizon — a stated limitation, not a bug

---

## Part 9 — What Comes Next

### 9.1 Remaining Phase 1 items

1. Bootstrap the weight-k=5 improvement
2. Fix channel features to be point-in-time
3. Test full 512-dim embeddings vs PCA-32
4. **Thumbnail encoder comparison** — the hypothesis is specific and testable: _CLIP
   encodes semantics, not engagement appeal_. Best single test is **DINOv2-small**
   (fastest, ~20–30 min CPU, and self-supervised so it never saw captions — a genuinely
   different objective). If it moves ρ, try SigLIP and DINOv2-base. If nothing moves,
   the ceiling is in the problem, not the representation.
5. Final evaluation on the held-out test set (once, at the end)

### 9.2 Phase 2 — early popularity

Deliberately deferred so the cold-start baseline is established first (which is what
makes the comparison meaningful).

Add `views_at_1h`, `views_at_6h`, `day_1_views` as features and re-run the identical
pipeline. The literature predicts a large jump (SMTPD: MAE roughly halves). The
with/without comparison, on the same split and model, is a strong result **because**
Phase 1 established the baseline properly.

**Design note:** keep the feature pipeline able to accept early-view columns — adding
them should be a column append and retrain, not a rebuild.

### 9.3 Architecture pieces designed but never built

From the original design, still open:

- Learned **channel-history set-encoder** (currently 5 flat aggregate stats) — expected
  to be the largest remaining lever
- **Quantile regression head** (pinball loss) for calibrated ranges rather than point
  estimates — the product promises a band, and nothing built so far delivers one
- **Privileged-feature distillation** (teacher sees trajectories, student sees snapshots)
- **Conformal calibration** for valid coverage guarantees per channel-size band

---

## Appendix A — Key Numbers Reference

| Quantity                       | Value                            |
| ------------------------------ | -------------------------------- |
| Final corpus                   | 22,628 videos, 786 channels      |
| Forecast horizon               | 7 days                           |
| Views arriving in first 24h    | 81%                              |
| Curve fit quality (hybrid)     | median R² 0.981; 93.7% above 0.8 |
| Curve family split             | 68% logistic, 32% power-law      |
| Shape ⊥ magnitude correlations | all \|r\| < 0.05                 |
| log(m) skew                    | −0.033 (well-calibrated S)       |
| Non-Latin titles               | 57.7%                            |
| Train / Val                    | 19,620 / 1,395 (59 channels)     |
| Best ρ (residual)              | 0.254                            |
| Best top-20% precision         | 0.310                            |
| End-to-end R²                  | 0.665                            |

## Appendix B — Methodological Lessons

1. **Evaluate the system you ship, not just its hardest component.** R²=0.045 on a
   residual and R²=0.665 end-to-end describe the same system.
2. **Choose metrics the domain uses.** Ranking metrics, not R², for popularity prediction.
3. **Always check the baseline is unchanged** when comparing. The shrinkage artifact was
   caught only because R2_anchor was tracked alongside ρ.
4. **A real effect has an optimum.** Monotonic improvement with no turnover is suspicious.
5. **Bootstrap at the level of correlation** (channels), not the level of rows.
6. **Negative results are results** — three of them here are genuinely informative.
7. **Leakage rules must be enforced explicitly**, especially in retrieval: no same-group
   neighbours, training-only memory bank, no future information.
8. **Point-in-time computation matters** for anything derived from history.
