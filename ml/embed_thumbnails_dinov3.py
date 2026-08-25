"""Thumbnail embedding stage (DINOv3 variant): encodes each downloaded
thumbnail image into a DINOv3 embedding vector, as an alternative to the CLIP
embeddings from embed_thumbnails.py so the two can be compared.

Reads the video_id list from ml/data/features_simple.csv, skips any video_id
listed in ml/data/thumbnail_failures.csv (known-bad downloads), and reads the
corresponding image from ml/data/thumbnails/{video_id}.jpg - the same
already-downloaded images embed_thumbnails.py uses; nothing is re-downloaded
here. Writes:

  ml/data/thumbnail_embeddings_dinov3.npy      float32 array, one row per video
  ml/data/thumbnail_embedding_ids_dinov3.csv   video_id per row, in the same order

Purely embedding computation - no downloading, no PCA, no target computation.

Uses facebook/dinov3-vitb16-pretrain-lvd1689m via the transformers library
(downloaded from the Hugging Face Hub on first run and cached locally
thereafter). This is a gated model - accept its license on the model page
and set HF_TOKEN before running.

DINOv3 returns a class token, register tokens, and patch tokens in
last_hidden_state. We use the CLS token (index 0) as the image embedding,
never the mean-pooled patch tokens.

Usage:
    python ml/embed_thumbnails_dinov3.py
    python ml/embed_thumbnails_dinov3.py --limit 50 --batch-size 8
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from PIL import Image
from tqdm import tqdm
from transformers import AutoImageProcessor, AutoModel

DATA_DIR = Path(__file__).resolve().parent / "data"
FEATURES_CSV = DATA_DIR / "features_simple.csv"
FAILURES_CSV = DATA_DIR / "thumbnail_failures.csv"
THUMBNAILS_DIR = DATA_DIR / "thumbnails"
EMBEDDINGS_NPY = DATA_DIR / "thumbnail_embeddings_dinov3.npy"
EMBEDDING_IDS_CSV = DATA_DIR / "thumbnail_embedding_ids_dinov3.csv"

MODEL_NAME = "facebook/dinov3-vitb16-pretrain-lvd1689m"


def load_target_ids(limit: int | None) -> list[str]:
    """video_id list from features_simple.csv, with known-bad thumbnail
    downloads (thumbnail_failures.csv) excluded."""
    features_df = pd.read_csv(FEATURES_CSV, usecols=["video_id"])
    if limit is not None:
        features_df = features_df.head(limit)

    failed_ids: set[str] = set()
    if FAILURES_CSV.exists():
        failures_df = pd.read_csv(FAILURES_CSV)
        if "video_id" in failures_df.columns:
            failed_ids = set(failures_df["video_id"])

    return [vid for vid in features_df["video_id"].tolist() if vid not in failed_ids]


def load_existing_store() -> tuple[list[str], np.ndarray | None]:
    if EMBEDDINGS_NPY.exists() and EMBEDDING_IDS_CSV.exists():
        ids = pd.read_csv(EMBEDDING_IDS_CSV)["video_id"].tolist()
        embeddings = np.load(EMBEDDINGS_NPY)
        return ids, embeddings
    return [], None


def load_image(video_id: str) -> Image.Image | None:
    path = THUMBNAILS_DIR / f"{video_id}.jpg"
    try:
        with Image.open(path) as img:
            return img.convert("RGB")
    except Exception as exc:
        print(f"[embed_thumbnails_dinov3] FAILED {video_id}: {exc}")
        return None


def load_model_and_processor(device: str) -> tuple[AutoModel, AutoImageProcessor]:
    """Loads the gated DINOv3 model, printing a clear message (instead of a
    raw traceback) if the Hub rejects the request for lack of license
    acceptance / a missing HF_TOKEN."""
    print(f"[embed_thumbnails_dinov3] loading {MODEL_NAME} (downloads on first run, cached after)...")
    try:
        model = AutoModel.from_pretrained(MODEL_NAME).to(device).eval()
        processor = AutoImageProcessor.from_pretrained(MODEL_NAME)
    except Exception as exc:
        message = str(exc).lower()
        auth_markers = ("gated", "401", "403", "access", "authoriz", "authentic", "token")
        if any(marker in message for marker in auth_markers):
            print("=" * 78)
            print(f"[embed_thumbnails_dinov3] Could not load {MODEL_NAME} - this looks like")
            print("an authentication/access problem, not a bug. DINOv3 is a gated model:")
            print(f"  1. Visit https://huggingface.co/{MODEL_NAME} and accept the license.")
            print("  2. Create a Hugging Face access token and set it as HF_TOKEN before")
            print("     running this script, e.g.:")
            print("       set HF_TOKEN=hf_your_token_here   (Windows cmd)")
            print("       $env:HF_TOKEN='hf_your_token_here' (PowerShell)")
            print("=" * 78)
            sys.exit(1)
        raise
    return model, processor


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=None, help="Only process the first N videos (for testing)"
    )
    parser.add_argument(
        "--batch-size", type=int, default=32, help="Images per batch (lower this if you hit GPU memory limits)"
    )
    args = parser.parse_args()

    start_time = time.time()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[embed_thumbnails_dinov3] using device: {device}")

    target_ids = load_target_ids(args.limit)
    existing_ids, existing_embeddings = load_existing_store()
    existing_id_set = set(existing_ids)

    to_embed_ids = [vid for vid in target_ids if vid not in existing_id_set]
    already_present = len(target_ids) - len(to_embed_ids)

    model, processor = load_model_and_processor(device)
    embedding_dim = model.config.hidden_size

    n_failed = 0
    logged_token_source = False
    new_embeddings_rows: list[np.ndarray] = []

    batches = [
        to_embed_ids[i : i + args.batch_size]
        for i in range(0, len(to_embed_ids), args.batch_size)
    ]
    for batch_ids in tqdm(batches, desc="embedding thumbnails (dinov3)"):
        images = []
        valid_indices = []
        for i, video_id in enumerate(batch_ids):
            img = load_image(video_id)
            if img is None:
                n_failed += 1
            else:
                images.append(img)
                valid_indices.append(i)

        batch_embeddings = np.zeros((len(batch_ids), embedding_dim), dtype=np.float32)
        if images:
            inputs = processor(images=images, return_tensors="pt").to(device)
            with torch.no_grad():
                outputs = model(**inputs)
            # DINOv3's last_hidden_state is [CLS, register tokens..., patch
            # tokens...] - index 0 is always the CLS token regardless of how
            # many register tokens the checkpoint uses. Prefer pooler_output
            # when the installed transformers version exposes one (it's
            # derived from the same CLS token), otherwise fall back to
            # last_hidden_state[:, 0] directly.
            pooler_output = getattr(outputs, "pooler_output", None)
            if pooler_output is not None:
                if not logged_token_source:
                    print("[embed_thumbnails_dinov3] using outputs.pooler_output (CLS token) for embeddings")
                    logged_token_source = True
                cls_embeds = pooler_output
            else:
                if not logged_token_source:
                    print("[embed_thumbnails_dinov3] no pooler_output on this transformers version - "
                          "using outputs.last_hidden_state[:, 0] (CLS token) for embeddings")
                    logged_token_source = True
                cls_embeds = outputs.last_hidden_state[:, 0]
            batch_embeddings[valid_indices] = cls_embeds.cpu().numpy().astype(np.float32)

        new_embeddings_rows.append(batch_embeddings)

    new_embeddings = (
        np.vstack(new_embeddings_rows) if new_embeddings_rows else np.empty((0, embedding_dim), dtype=np.float32)
    )

    if existing_embeddings is not None:
        all_embeddings = np.vstack([existing_embeddings, new_embeddings])
    else:
        all_embeddings = new_embeddings
    all_ids = existing_ids + to_embed_ids

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    np.save(EMBEDDINGS_NPY, all_embeddings)
    pd.DataFrame({"video_id": all_ids}).to_csv(EMBEDDING_IDS_CSV, index=False)

    elapsed = time.time() - start_time

    print("=" * 78)
    print(f"Embedded this run:         {len(to_embed_ids) - n_failed}")
    print(f"Already present (skipped): {already_present}")
    print(f"Failed (zero vector):      {n_failed}")
    print(f"Embedding dimension:       {embedding_dim}")
    print(f"Device used:               {device}")
    print(f"Total time:                {elapsed:.1f}s")
    print(f"Total stored embeddings:   {len(all_ids)}")
    print(f"Saved to {EMBEDDINGS_NPY}")
    print(f"     and {EMBEDDING_IDS_CSV}")
    print("=" * 78)


if __name__ == "__main__":
    main()
