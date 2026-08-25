"""Title embedding stage: encodes each video's title into a LaBSE vector.

Reads ml/data/features_simple.csv for the video_id list and ml/data/videos.csv
for the corresponding title text (features_simple.csv only carries derived
title stats, not the raw string). Writes:

  ml/data/title_embeddings.npy      float32 array, one row per video
  ml/data/title_embedding_ids.csv   video_id per row, in the same order

Purely embedding computation - no thumbnails, no PCA, no target computation.

Uses sentence-transformers/LaBSE (~1.8GB, downloaded from the Hugging Face
Hub on first run and cached locally thereafter) because titles in this
corpus mix Sinhala, Tamil, English, and romanized Sinhala - an English-only
model would produce noise for most of the corpus.

Usage:
    python ml/embed_titles.py
    python ml/embed_titles.py --limit 50
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sentence_transformers import SentenceTransformer

DATA_DIR = Path(__file__).resolve().parent / "data"
FEATURES_CSV = DATA_DIR / "features_simple.csv"
VIDEOS_CSV = DATA_DIR / "videos.csv"
EMBEDDINGS_NPY = DATA_DIR / "title_embeddings.npy"
EMBEDDING_IDS_CSV = DATA_DIR / "title_embedding_ids.csv"

MODEL_NAME = "sentence-transformers/LaBSE"
BATCH_SIZE = 64


def load_titles(limit: int | None) -> pd.DataFrame:
    """video_id order comes from features_simple.csv; title text comes from
    videos.csv, joined on video_id."""
    features_df = pd.read_csv(FEATURES_CSV, usecols=["video_id"])
    if limit is not None:
        features_df = features_df.head(limit)

    videos_df = pd.read_csv(VIDEOS_CSV, usecols=["video_id", "title"])
    merged = features_df.merge(videos_df, on="video_id", how="left")
    return merged


def load_existing_store() -> tuple[list[str], np.ndarray | None]:
    if EMBEDDINGS_NPY.exists() and EMBEDDING_IDS_CSV.exists():
        ids = pd.read_csv(EMBEDDING_IDS_CSV)["video_id"].tolist()
        embeddings = np.load(EMBEDDINGS_NPY)
        return ids, embeddings
    return [], None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=None, help="Only process the first N videos (for testing)"
    )
    args = parser.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[embed_titles] using device: {device}")

    titles_df = load_titles(args.limit)
    existing_ids, existing_embeddings = load_existing_store()
    existing_id_set = set(existing_ids)

    to_encode = titles_df[~titles_df["video_id"].isin(existing_id_set)]
    already_present = len(titles_df) - len(to_encode)

    print(f"[embed_titles] loading {MODEL_NAME} (~1.8GB, cached after first download)...")
    model = SentenceTransformer(MODEL_NAME, device=device)
    embedding_dim = (
        model.get_embedding_dimension()
        if hasattr(model, "get_embedding_dimension")
        else model.get_sentence_embedding_dimension()
    )

    titles_raw = to_encode["title"].fillna("")
    is_empty = titles_raw.str.strip() == ""
    n_empty = int(is_empty.sum())
    if n_empty:
        print(f"[embed_titles] {n_empty} titles are null/empty; recording zero vectors for them")

    non_empty_titles = titles_raw[~is_empty].tolist()
    if non_empty_titles:
        encoded = model.encode(
            non_empty_titles,
            batch_size=BATCH_SIZE,
            show_progress_bar=True,
            convert_to_numpy=True,
        )
    else:
        encoded = np.empty((0, embedding_dim), dtype=np.float32)

    new_embeddings = np.zeros((len(to_encode), embedding_dim), dtype=np.float32)
    new_embeddings[~is_empty.to_numpy()] = encoded
    new_ids = to_encode["video_id"].tolist()

    if existing_embeddings is not None:
        all_embeddings = np.vstack([existing_embeddings, new_embeddings])
    else:
        all_embeddings = new_embeddings
    all_ids = existing_ids + new_ids

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    np.save(EMBEDDINGS_NPY, all_embeddings)
    pd.DataFrame({"video_id": all_ids}).to_csv(EMBEDDING_IDS_CSV, index=False)

    print("=" * 78)
    print(f"Titles encoded this run:   {len(to_encode)}")
    print(f"Already present (skipped): {already_present}")
    print(f"Null/empty (zero vector):  {n_empty}")
    print(f"Embedding dimension:       {embedding_dim}")
    print(f"Total stored embeddings:   {len(all_ids)}")
    print(f"Saved to {EMBEDDINGS_NPY}")
    print(f"     and {EMBEDDING_IDS_CSV}")
    print("=" * 78)


if __name__ == "__main__":
    main()
