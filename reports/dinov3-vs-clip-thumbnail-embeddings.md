# DINOv3 vs CLIP Thumbnail Embeddings

**Project:** TrendCast  
**Date:** 2026-09-17  
**Purpose:** Compare DINOv3 and CLIP ViT thumbnail embeddings for the TrendCast view-forecasting model.

## Executive Summary

CLIP ViT-B/32 performed better than DINOv3 on the current TrendCast dataset and held-out evaluation split.

| Metric | CLIP ViT-B/32 | DINOv3 | DINOv3 - CLIP |
|---|---:|---:|---:|
| Overall RMSLE | **1.4073** | 1.4494 | +0.0420 |
| Day 1 RMSLE | **1.4151** | 1.4570 | +0.0420 |
| Day 3 RMSLE | **1.3786** | 1.4250 | +0.0464 |
| Day 7 RMSLE | **1.3565** | 1.4031 | +0.0465 |

Lower RMSLE is better. DINOv3 was worse at every evaluation checkpoint, so CLIP should remain the selected thumbnail encoder for the current deployment. No backend model artifacts were replaced as part of this comparison.

## Models Compared

### CLIP

- Checkpoint: `openai/clip-vit-base-patch32`
- Loaded with Hugging Face Transformers using `CLIPModel` and `CLIPProcessor`
- Image features were extracted with `get_image_features`
- Embeddings were stored in `ml/data/thumbnail_embeddings.npy`

### DINOv3

- Checkpoint: `facebook/dinov3-vitb16-pretrain-lvd1689m`
- Loaded with Hugging Face Transformers using `AutoModel`
- The gated Hugging Face license was accepted and the model was downloaded using `HF_TOKEN`
- The CLS token was used as the image embedding
- Embeddings were stored separately in `ml/data/thumbnail_embeddings_dinov3.npy`
- DINOv3 generated 3,317 embeddings successfully, with zero image failures
- The DINOv3 embeddings were 768-dimensional before PCA

## Data Pipeline

Both encoders used exactly the same source images and video IDs:

1. Existing YouTube thumbnail images were read from `ml/data/thumbnails/`.
2. IDs listed in `ml/data/thumbnail_failures.csv` were excluded.
3. The thumbnail embedding scripts generated encoder-specific `.npy` arrays and ID CSV files.
4. `ml/build_features.py` joined thumbnail embeddings with:
   - `features_simple.csv`
   - `curve_params.csv`
   - title embeddings
   - channel and video metadata
5. Videos with unreliable curve fits were removed:
   - `tau > 10,000` hours
   - missing or `R^2 < 0.5`
6. The final feature table contained 3,268 videos for each encoder.
7. Each thumbnail embedding set was independently reduced to 40 PCA components.
8. Title embeddings, metadata, categorical features, and thumbnail PCA features were used by the forecasting model.

Failed image handling was also corrected during this work. An unreadable image is now excluded from the embedding store instead of receiving a misleading zero vector, keeping the ID list and embedding rows aligned.

## Training Method

The same training configuration was used for both encoders:

- Model: two XGBoost regressors
- Targets:
  - `target_log_vinf_rel`
  - `target_log_tau`
- Estimators: 2,000 maximum
- Maximum depth: 6
- Learning rate: 0.03
- Subsample: 0.8
- Column subsample: 0.8
- Early stopping: 50 rounds
- Random seed: 42
- Feature set: metadata, categorical features, title PCA features, and thumbnail PCA features

The fitted curve model represents view growth as:

```text
V(t) = V_inf * (1 - exp(-t / tau))
```

The predicted `V_inf` and `tau` values were converted back into view curves before evaluation.

## Evaluation Method

The comparison used a channel-level split to prevent videos from the same channel appearing in both training and testing data.

- Training channels: 48
- Held-out test channels: 4
- Training videos: 2,614
- Test videos: 654
- Test video fraction: 20.0%

The split was pinned from the original evaluation reference and reused for both encoder runs. This makes the RMSLE comparison directly comparable.

Evaluation was performed over the forecast curve from 1 hour through 168 hours. Results were also reported at:

- Day 1: 24 hours
- Day 3: 72 hours
- Day 7: 168 hours

RMSLE was calculated on the reconstructed view counts. Lower values indicate better forecast accuracy.

## Results

### Full Model RMSLE

| Forecast horizon | CLIP | DINOv3 | Better model |
|---|---:|---:|---|
| Overall curve | **1.4073** | 1.4494 | CLIP |
| Day 1 | **1.4151** | 1.4570 | CLIP |
| Day 3 | **1.3786** | 1.4250 | CLIP |
| Day 7 | **1.3565** | 1.4031 | CLIP |

DINOv3's overall RMSLE was approximately 3.0% higher than CLIP's relative to the CLIP score:

```text
(1.4494 - 1.4073) / 1.4073 = 0.0299
```

### Metadata-Only Ablation

The metadata-only ablation removed title and thumbnail embedding features while retaining metadata and categorical features.

| Model | Overall RMSLE |
|---|---:|
| CLIP full model | **1.4073** |
| DINOv3 full model | 1.4494 |
| CLIP metadata-only | 1.5595 |
| DINOv3 metadata-only | 1.5595 |

Both embedding-based models improved over metadata-only features. The improvement was larger for CLIP:

- CLIP improvement: `1.5595 - 1.4073 = 0.1522`
- DINOv3 improvement: `1.5595 - 1.4494 = 0.1101`

This indicates that both thumbnail/title embedding feature sets provide useful signal, but the CLIP-based feature set was more useful for this forecasting task and dataset.

## Generated Artifacts

The comparison artifacts were kept separate:

- `ml/data/thumbnail_embeddings_dinov3.npy`
- `ml/data/thumbnail_embedding_ids_dinov3.csv`
- `ml/data/features_dinov3.csv`
- `ml/models/thumbnail_pca_dinov3.joblib`
- `ml/models/channel_medians_dinov3.json`
- `ml/models/vinf_model_dinov3.joblib`
- `ml/models/tau_model_dinov3.joblib`
- `ml/models/evaluation_dinov3.json`

The equivalent CLIP comparison artifacts use the `_clip` suffix. The existing unsuffixed artifacts in `backend/models/` were not copied or replaced.

## Reproduction Commands

Run these commands from the repository root with the `DSEP` conda environment:

```powershell
conda run -n DSEP python ml/embed_thumbnails.py --batch-size 32
conda run -n DSEP python ml/embed_thumbnails_dinov3.py --batch-size 8
conda run -n DSEP python ml/build_features.py --thumbnail-encoder clip
conda run -n DSEP python ml/train_model.py --thumbnail-encoder clip
conda run -n DSEP python ml/build_features.py --thumbnail-encoder dinov3
conda run -n DSEP python ml/train_model.py --thumbnail-encoder dinov3
```

DINOv3 requires access to the gated Hugging Face checkpoint and a valid `HF_TOKEN`. The token should be provided through the local ignored `backend/.env` file or the process environment and should never be committed.

## Recommendation

Keep CLIP ViT-B/32 as the thumbnail encoder for the current TrendCast model. It achieved lower RMSLE overall and at every forecast horizon tested. DINOv3 artifacts remain available for future experiments, but replacing the deployed CLIP artifacts is not justified by this evaluation.

A future comparison could test additional DINOv3 pooling strategies, larger training data, alternative PCA dimensions, or a hyperparameter search. Those would be new experiments and should use the same pinned channel split or repeated channel-level cross-validation.
