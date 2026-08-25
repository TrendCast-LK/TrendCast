"""Applies a saved PCA transform (fitted in ml/build_features.py, loaded
from ml/models/*.joblib at inference time) to raw embeddings. A thin, shared
wrapper so training-time and inference-time dimensionality reduction go
through the exact same call.
"""

from __future__ import annotations

import numpy as np
from sklearn.decomposition import PCA


def apply_pca(pca: PCA, raw_embeddings: np.ndarray) -> np.ndarray:
    return pca.transform(raw_embeddings)
