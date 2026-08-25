"""Thumbnail embedding: load the CLIP model and encode images into raw
(pre-PCA) embedding vectors.

Shared by ml/embed_thumbnails.py (bulk, offline, building the training set)
and backend/inference.py (single image, online, serving /forecast) so both
compute embeddings exactly the same way.
"""

from __future__ import annotations

import numpy as np
import torch
from PIL import Image
from transformers import CLIPModel, CLIPProcessor

MODEL_NAME = "openai/clip-vit-base-patch32"


def load_model(device: str) -> tuple[CLIPModel, CLIPProcessor]:
    model = CLIPModel.from_pretrained(MODEL_NAME).to(device).eval()
    processor = CLIPProcessor.from_pretrained(MODEL_NAME)
    return model, processor


def embed_images(
    model: CLIPModel, processor: CLIPProcessor, images: list[Image.Image], device: str
) -> np.ndarray:
    """Raw (pre-PCA) CLIP image embeddings, one row per image, in the same order."""
    embedding_dim = model.config.projection_dim
    if not images:
        return np.empty((0, embedding_dim), dtype=np.float32)

    inputs = processor(images=images, return_tensors="pt").to(device)
    with torch.no_grad():
        features = model.get_image_features(**inputs)
    # Newer transformers versions return a BaseModelOutputWithPooling
    # (embeddings in .pooler_output) instead of a bare tensor.
    image_embeds = getattr(features, "pooler_output", features)
    return image_embeds.cpu().numpy().astype(np.float32)
