"""Feature-name sanitization and single-row feature assembly.

sanitize_feature_name() must produce byte-identical output whether called at
training time (ml/train_model.py, sanitizing the whole training table's
columns) or inference time (backend/inference.py, sanitizing one request's
feature row) - the trained booster's feature names were sanitized this way
when it was trained, so any drift here silently breaks every prediction.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

CATEGORICAL_COLUMNS = ["category_id", "size_tier"]

# Same "simple" (non-embedding, non-one-hot) feature columns and order used
# by ml/build_features.py to assemble the training table and by
# backend/inference.py to assemble a single request's feature row.
SIMPLE_FEATURE_COLUMNS = [
    "title_char_length",
    "title_word_count",
    "duration_seconds",
    "tag_count",
    "upload_hour",
    "upload_dayofweek",
    "is_weekend",
    "subscriber_count",
    "total_views",
    "video_count",
    "avg_views_per_video",
    "views_per_subscriber",
    "engagement_ratio",
    "tier_category",
]


def sanitize_feature_name(name: str) -> str:
    """XGBoost rejects feature names containing '[', ']', or '<' (used by its
    own split-condition serialization) - one-hot column names like
    'size_tier_Micro (<1K)' hit this, so swap the offending characters for
    plain text rather than touching the source data."""
    return name.replace("<", "lt").replace(">", "gt").replace("[", "(").replace("]", ")")


def assemble_feature_row(
    simple_values: dict,
    encoder,
    category_id,
    size_tier: str,
    title_pcs: np.ndarray,
    thumb_pcs: np.ndarray,
    feature_names: list[str],
) -> pd.DataFrame:
    """Builds one feature row, named and ordered to match `feature_names`
    (the trained booster's expected columns) - used by
    backend/inference.py for a single (title, thumbnail, upload time,
    channel) request."""
    raw: dict = dict(simple_values)

    encoded = encoder.transform(pd.DataFrame({"category_id": [category_id], "size_tier": [size_tier]}))[0]
    for name, value in zip(encoder.get_feature_names_out(CATEGORICAL_COLUMNS), encoded):
        raw[name] = value

    for i, value in enumerate(title_pcs):
        raw[f"title_pc_{i}"] = float(value)
    for i, value in enumerate(thumb_pcs):
        raw[f"thumb_pc_{i}"] = float(value)

    sanitized = {sanitize_feature_name(k): v for k, v in raw.items()}
    row = [sanitized.get(name, 0.0) for name in feature_names]
    return pd.DataFrame([row], columns=feature_names).astype(float)
