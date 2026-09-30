"""HistAttnV2: the channel-history attention half of the CatBoost + HistAttnV2
forecast ensemble.

The classes below are a serving copy of the ones in
ensemble_artifacts/export_histattn_v2.py, the script that trained and saved
histattn_v2.pt. load_state_dict(strict=True) checks every key and shape against
the saved file, so any drift from the training architecture fails at startup
instead of producing quiet nonsense. MODEL_INTEGRATION_V2.md section 3 describes
a different layout (a LayerNorm+GELU tabular tower, no post-projection
LayerNorm, separate text/image arguments) that does not load this state_dict.

The feature helpers reproduce that script's build_hist() one value at a time:
the joint L2 normalisation of [text_512, image_512], the history-feature scaling
(/5, /8), the [-6, 6] clip and the maturation-curve lookup. A mismatch in any of
them would change predictions without raising an error.
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn

HORIZON_DAYS = 7
N_HIST_FEATURES = 3  # [rel_perf, log1p(age)/5, log1p(duration)/8]
# Not stored in histattn_config.json and not recoverable from the state_dict
# (in_proj_weight is 3*d_model x d_model for any head count); fixed at 4 in
# export_histattn_v2.py.
N_HEADS = 4


class ModalityProjector(nn.Module):
    """Separate nonlinear projections for the text and image halves of one
    joint [text_512 | image_512] embedding. Text first, image second."""

    def __init__(self, emb_txt: int, emb_img: int, txt_out: int, img_out: int):
        super().__init__()
        self.emb_txt = emb_txt
        self.text_mlp = nn.Sequential(
            nn.Linear(emb_txt, 128), nn.LayerNorm(128), nn.GELU(), nn.Dropout(0.3),
            nn.Linear(128, txt_out), nn.GELU())
        self.img_mlp = nn.Sequential(
            nn.Linear(emb_img, 64), nn.LayerNorm(64), nn.GELU(), nn.Dropout(0.3),
            nn.Linear(64, img_out), nn.GELU())

    def forward(self, emb: torch.Tensor) -> torch.Tensor:
        return torch.cat(
            [self.text_mlp(emb[..., :self.emb_txt]), self.img_mlp(emb[..., self.emb_txt:])], dim=-1
        )


class HistAttnV2(nn.Module):
    """Predicts log(m) from the target video's content, its 30 tabular
    features, and cross-attention over up to max_hist prior channel videos.

    Training also randomly masked whole channel histories (chan_drop=0.15),
    gated on self.training. That branch is left out here: it is a training-only
    regulariser, and the model is only ever served in eval mode."""

    def __init__(self, n_tab: int, emb_txt: int, emb_img: int, txt_out: int, img_out: int, d_model: int):
        super().__init__()
        proj = txt_out + img_out
        self.projector = ModalityProjector(emb_txt, emb_img, txt_out, img_out)
        self.hist_proj = nn.Linear(proj + N_HIST_FEATURES, d_model)
        self.q_proj = nn.Linear(proj, d_model)
        self.attn = nn.MultiheadAttention(d_model, num_heads=N_HEADS, batch_first=True, dropout=0.1)
        self.ln = nn.LayerNorm(d_model)
        self.tab = nn.Sequential(nn.Linear(n_tab, 96), nn.ReLU(), nn.Dropout(0.3))
        self.tgt = nn.Sequential(nn.Linear(proj, 96), nn.ReLU(), nn.Dropout(0.3))
        self.head = nn.Sequential(
            nn.Linear(d_model + 96 + 96, 128), nn.ReLU(), nn.Dropout(0.35),
            nn.Linear(128, 48), nn.ReLU(), nn.Dropout(0.25),
            nn.Linear(48, 1))

    def forward(self, tab, tgt_emb, hist_emb, hist_feat, mask):
        tp = self.projector(tgt_emb)
        hp = self.projector(hist_emb)
        h = self.ln(self.hist_proj(torch.cat([hp, hist_feat], -1)))
        # A row with no history attends over nothing: unmask it so softmax
        # stays finite, then zero its context vector.
        empty = (mask.sum(1) == 0)
        kp = (mask == 0)
        kp[empty] = False
        q = self.q_proj(tp).unsqueeze(1)
        ctx, _ = self.attn(q, h, h, key_padding_mask=kp)
        ctx = ctx.squeeze(1) * (~empty).float().unsqueeze(1)
        z = torch.cat([ctx, self.tab(tab), self.tgt(tp)], -1)
        return self.head(z).squeeze(-1)


def load_model(path: Path, config: dict[str, Any]) -> HistAttnV2:
    model = HistAttnV2(
        n_tab=int(config["n_tab"]),
        emb_txt=int(config["emb_txt"]),
        emb_img=int(config["emb_img"]),
        txt_out=int(config["txt_out"]),
        img_out=int(config["img_out"]),
        d_model=int(config["d_model"]),
    )
    state_dict = torch.load(path, map_location="cpu", weights_only=True)
    model.load_state_dict(state_dict, strict=True)
    model.eval()  # disables every Dropout; nothing here is ever trained
    return model


# ---------------------------------------------------------------------------
# Features, exactly as build_hist() in export_histattn_v2.py
# ---------------------------------------------------------------------------

def joint_embedding(text_512: np.ndarray, image_512: np.ndarray | None) -> np.ndarray:
    """[text | image] L2-normalised as ONE 1024-dim vector (not per modality),
    as training's emb_all. A missing thumbnail is a zero image half, as in the
    training corpus (has_thumbnail=0 rows); the config's mean image embedding
    is a CatBoost-side fallback and is not what this model saw."""
    text = np.asarray(text_512, dtype=np.float32).reshape(-1)
    image = (np.zeros_like(text) if image_512 is None
             else np.asarray(image_512, dtype=np.float32).reshape(-1))
    emb = np.concatenate([text, image])
    return emb / (np.linalg.norm(emb) + 1e-8)


def maturation_fraction(age_days: float, maturation_curve: dict[int, float]) -> float:
    return maturation_curve[int(min(max(round(age_days), 1), HORIZON_DAYS))]


def day7_estimate(view_count: float, age_now_days: float, maturation_curve: dict[int, float]) -> float:
    """Training read each history video's true day-7 count; serving only has
    today's count, so a video younger than 7 days is scaled up to its expected
    day-7 value. Older videos use the current count as-is, like the channel
    anchor S in inference._channel_features."""
    if age_now_days < HORIZON_DAYS:
        return float(view_count) / max(maturation_fraction(age_now_days, maturation_curve), 1e-3)
    return float(view_count)


def history_feature_row(
    day7_views: float, age_days: float, duration_s: float | None,
    anchor_s: float, maturation_curve: dict[int, float],
) -> list[float]:
    """[rel_perf, log1p(age)/5, log1p(duration)/8] for one history video, age
    measured from the target's publish time.

    Training divides the day-7 count by g(age) again when the history video is
    under 7 days older than the target, even though that count is already a
    full day-7 value. It is kept because the weights were fit on it; the input
    is day7_estimate() at serving time and the corpus day-7 count in tests."""
    age = max(age_days, 1.0)
    if age < HORIZON_DAYS:
        equiv = day7_views / max(maturation_fraction(age, maturation_curve), 1e-3)
    else:
        equiv = day7_views
    rel_perf = float(np.clip(math.log(max(equiv, 1.0) / max(anchor_s, 1.0)), -6, 6))
    return [rel_perf, math.log1p(age) / 5.0, math.log1p(duration_s or 0.0) / 8.0]


def build_history_arrays(
    videos: list[dict[str, Any]],
    target_time: datetime,
    anchor_s: float,
    maturation_curve: dict[int, float],
    max_hist: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """(hist_emb (1,H,1024), hist_feat (1,H,3), mask (1,H)) from the most
    recent max_hist videos published strictly before target_time, oldest
    first. Each video needs published_at, day7_views, duration_s and embedding
    (a joint_embedding). Returns None when no video qualifies."""
    prior = sorted(
        (v for v in videos if v["published_at"] < target_time),
        key=lambda v: v["published_at"],
    )[-max_hist:]
    if not prior:
        return None

    dim = len(prior[0]["embedding"])
    emb = np.zeros((1, max_hist, dim), dtype=np.float32)
    feat = np.zeros((1, max_hist, N_HIST_FEATURES), dtype=np.float32)
    mask = np.zeros((1, max_hist), dtype=np.float32)
    for j, v in enumerate(prior):
        age_days = (target_time - v["published_at"]).total_seconds() / 86400.0
        emb[0, j] = v["embedding"]
        feat[0, j] = history_feature_row(
            v["day7_views"], age_days, v.get("duration_s"), anchor_s, maturation_curve
        )
        mask[0, j] = 1.0
    return emb, feat, mask


def predict_log_m(
    model: HistAttnV2,
    tab_scaled: np.ndarray,
    target_embedding: np.ndarray,
    history: tuple[np.ndarray, np.ndarray, np.ndarray],
) -> float:
    hist_emb, hist_feat, mask = history
    with torch.inference_mode():
        out = model(
            torch.as_tensor(np.asarray(tab_scaled, dtype=np.float32).reshape(1, -1)),
            torch.as_tensor(np.asarray(target_embedding, dtype=np.float32).reshape(1, -1)),
            torch.as_tensor(hist_emb),
            torch.as_tensor(hist_feat),
            torch.as_tensor(mask),
        )
    return float(out.item())
