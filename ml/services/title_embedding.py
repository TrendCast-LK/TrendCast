"""Title embedding: load the LaBSE model and encode text into raw
(pre-PCA) embedding vectors.

Shared by ml/embed_titles.py (bulk, offline, building the training set) and
backend/inference.py (single title, online, serving /forecast) so both
compute embeddings exactly the same way.
"""

from __future__ import annotations

import numpy as np
from sentence_transformers import SentenceTransformer

MODEL_NAME = "sentence-transformers/LaBSE"
DEFAULT_BATCH_SIZE = 64


def load_model(device: str) -> SentenceTransformer:
    return SentenceTransformer(MODEL_NAME, device=device)


def embed_titles(
    model: SentenceTransformer,
    titles: list[str],
    batch_size: int = DEFAULT_BATCH_SIZE,
    show_progress_bar: bool = False,
) -> np.ndarray:
    """Raw (pre-PCA) LaBSE embeddings, one row per title, in the same order."""
    if not titles:
        embedding_dim = (
            model.get_embedding_dimension()
            if hasattr(model, "get_embedding_dimension")
            else model.get_sentence_embedding_dimension()
        )
        return np.empty((0, embedding_dim), dtype=np.float32)

    return model.encode(
        titles, batch_size=batch_size, show_progress_bar=show_progress_bar, convert_to_numpy=True
    )
