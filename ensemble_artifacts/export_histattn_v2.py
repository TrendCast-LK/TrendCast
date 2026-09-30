"""
export_histattn_v2.py

Trains HistAttnV2 -- the neural half of the deployed CatBoost + HistAttnV2
ensemble -- on the same channel-disjoint split used throughout the session,
and exports everything needed to run it at inference time.

Standalone version of notebook cells "FULL-CORPUS DINOv2 TEST" (56) and
"export_artifacts_v2.py" (57), with the DINOv2 comparison stripped out
(USE_DINO stays False here -- that variant hasn't cleared this project's
P=1.000 bootstrap bar, so it isn't part of the deployed ensemble) and the
artifacts-directory path fixed to be relative to THIS script (both this
script and export_artifacts.py now live in, and write to, the same
artifacts/ folder).

Must be run AFTER export_artifacts.py (Part A: CatBoost/PCA/shape
artifacts). Run from artifacts/. Needs training_corpus_h7_final.csv,
text_embeddings.npy, image_embeddings_final.npy, pca_text_fixed.pkl,
pca_image_fixed.pkl (reused for a fair CatBoost validation baseline --
gives the honest out-of-sample residual_std that overwrites config.json's
in-sample value, and reference predictions for the combined sanity check).
"""
import json, re, os, pickle
from pathlib import Path

import numpy as np
import pandas as pd
import joblib
import torch
import torch.nn as nn
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import GroupShuffleSplit
from sklearn.metrics import r2_score
from scipy.stats import spearmanr
from catboost import CatBoostRegressor

SEED = 0
torch.manual_seed(SEED)
np.random.seed(SEED)
device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"device: {device}")

H = 7
MAX_HIST = 20
NC = 32
EMB_TXT = 512
EMB_IMG = 512
ARTIFACTS_DIR = Path(".")
ENSEMBLE_WEIGHT = 0.3  # winning blend weight from the controlled comparison

print("=" * 70)
print("HISTATTN V2 EXPORT")
print("=" * 70)

# ---------------------------------------------------------------
# 0. Confirm Part A (export_artifacts.py) has already run
# ---------------------------------------------------------------
REQUIRED_PART_A_FILES = [
    "catboost_magnitude.cbm", "catboost_shape_form.cbm",
    "catboost_shape_c.cbm", "catboost_shape_theta.cbm",
    "catboost_shape_k.cbm", "catboost_shape_t0.cbm",
    "pca_text.pkl", "pca_image.pkl",
    "feature_columns.json", "maturation_curve.json",
    "config.json", "reference_predictions.json",
]
missing_part_a = [f for f in REQUIRED_PART_A_FILES if not (ARTIFACTS_DIR / f).exists()]
if missing_part_a:
    raise FileNotFoundError(
        f"{ARTIFACTS_DIR.resolve()} is missing {missing_part_a} -- run "
        "export_artifacts.py first."
    )
print(f"Part A artifacts present: {REQUIRED_PART_A_FILES}")

# ---------------------------------------------------------------
# 1. Load + rebuild features (identical to export_artifacts.py / notebook)
# ---------------------------------------------------------------
df = pd.read_csv("training_corpus_h7_final.csv")
text_emb = np.load("text_embeddings.npy").astype(np.float32)
img_emb = np.load("image_embeddings_final.npy").astype(np.float32)
assert len(df) == len(text_emb) == len(img_emb)
print(f"loaded {len(df)} videos, {df.channel_id.nunique()} channels")

def parse_duration(iso):
    if pd.isna(iso): return np.nan
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", str(iso))
    if not m: return np.nan
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s

def count_tags(t):
    if not isinstance(t, str): return 0
    try:
        p = json.loads(t); return len(p) if isinstance(p, list) else 0
    except Exception:
        return t.count(",") + 1 if t.strip() else 0

df["duration_s"] = df["video_duration"].apply(parse_duration)
df["title_length"] = df["title"].fillna("").str.len()
df["description_length"] = df["description"].fillna("").str.len()
df["tag_count"] = df["tags"].apply(count_tags)
df["published_at"] = pd.to_datetime(df["published_at"], utc=True)
df["publish_hour_sin"] = np.sin(2 * np.pi * df.published_at.dt.hour / 24)
df["publish_hour_cos"] = np.cos(2 * np.pi * df.published_at.dt.hour / 24)
df["publish_dow_sin"] = np.sin(2 * np.pi * df.published_at.dt.dayofweek / 7)
df["publish_dow_cos"] = np.cos(2 * np.pi * df.published_at.dt.dayofweek / 7)
df["has_thumbnail"] = (np.linalg.norm(img_emb, axis=1) > 1e-6).astype(int)

def cos_sim(a, b):
    an = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-8)
    bn = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-8)
    return np.sum(an * bn, axis=1)
df["thumbnail_title_alignment"] = cos_sim(img_emb, text_emb)

ch = df.groupby("channel_id").agg(
    channel_video_count=("id", "count"),
    channel_median_views=(f"day_{H}_views", "median"),
    channel_view_std=(f"day_{H}_views", "std")).reset_index()
ch["channel_log_volatility"] = (df.groupby("channel_id")[f"day_{H}_views"]
    .apply(lambda x: np.std(np.log(x.clip(lower=1)))).values)
ch["channel_category_diversity"] = df.groupby("channel_id")["category_id"].nunique().values
df = df.merge(ch, on="channel_id", how="left")
chan_cols = ["channel_video_count", "channel_median_views", "channel_view_std",
             "channel_log_volatility", "channel_category_diversity"]

df = df.loc[:, ~df.columns.str.startswith("cat_")]
cat_d = pd.get_dummies(df["category_id"], prefix="cat")
df = pd.concat([df, cat_d], axis=1)

tab_cols = (["duration_s", "title_length", "description_length", "tag_count",
             "publish_hour_sin", "publish_hour_cos", "publish_dow_sin", "publish_dow_cos",
             "thumbnail_title_alignment", "has_thumbnail"]
            + list(cat_d.columns) + chan_cols)
Xtab = df[tab_cols].fillna(0)
Xtab = Xtab.loc[:, ~Xtab.columns.duplicated()]
tab_cols = Xtab.columns.tolist()
y = df["log_m_clipped"]
groups = df["channel_id"]
print(f"Tabular feature matrix: {Xtab.shape}")

if "n_prior" not in df.columns:
    tmp = df.sort_values(["channel_id", "published_at"]).copy()
    tmp["n_prior"] = tmp.groupby("channel_id").cumcount()
    df["n_prior"] = tmp["n_prior"].reindex(df.index)

# --- fixed PCA, reused for the CatBoost validation baseline only ---
pca_text = joblib.load("pca_text_fixed.pkl")
pca_image = joblib.load("pca_image_fixed.pkl")
tp_ = pca_text.transform(text_emb)
ip_ = pca_image.transform(img_emb)
Xcb = Xtab.copy()
for i in range(NC):
    Xcb[f"text_pc{i}"] = tp_[:, i]
    Xcb[f"img_pc{i}"] = ip_[:, i]

# --- history index, point-in-time (shared by CatBoost anchor logic and HistAttnV2) ---
emb_all = np.concatenate([text_emb, img_emb], axis=1)
emb_all /= (np.linalg.norm(emb_all, axis=1, keepdims=True) + 1e-8)
pos = {ix: i for i, ix in enumerate(df.index)}

hist_rows, seen = {}, {}
for ix in df.sort_values(["channel_id", "published_at"]).index:
    c = df.at[ix, "channel_id"]
    prev = seen.setdefault(c, [])
    hist_rows[ix] = prev[-MAX_HIST:].copy()
    prev.append(ix)

g = {d: float((df[f"day_{d}_views"] / df[f"day_{H}_views"].replace(0, np.nan)).median())
     for d in range(1, H + 1)}

S_arr = df["S"].values.astype(np.float32)
day7 = df[f"day_{H}_views"].values.astype(np.float32)
dur = df["duration_s"].fillna(0).values.astype(np.float32)
pub_s = df["published_at"].values.astype("datetime64[s]").astype(np.int64)

def build_hist(idx_list):
    B = len(idx_list)
    E = np.zeros((B, MAX_HIST, EMB_TXT + EMB_IMG), dtype=np.float32)
    F = np.zeros((B, MAX_HIST, 3), dtype=np.float32)
    M = np.zeros((B, MAX_HIST), dtype=np.float32)
    for b, ix in enumerate(idx_list):
        hs = hist_rows[ix]
        if not hs:
            continue
        t_pub = pub_s[pos[ix]]
        S_here = max(float(S_arr[pos[ix]]), 1.0)
        for j, hx in enumerate(hs[-MAX_HIST:]):
            p = pos[hx]
            age = max((t_pub - pub_s[p]) / 86400.0, 1.0)
            frac = g[int(min(max(round(age), 1), H))]
            equiv = day7[p] / max(frac, 1e-3) if age < H else day7[p]
            E[b, j] = emb_all[p]
            F[b, j] = [np.clip(np.log(max(equiv, 1.0) / S_here), -6, 6),
                       np.log1p(age) / 5.0, np.log1p(dur[p]) / 8.0]
            M[b, j] = 1.0
    return E, F, M

# ---------------------------------------------------------------
# 2. Split -- identical to every other script this session
# ---------------------------------------------------------------
gss = GroupShuffleSplit(n_splits=1, test_size=0.15, random_state=SEED)
tr_i, tmp_i = next(gss.split(Xtab, y, groups))
Xt, yt, gt = Xtab.iloc[tmp_i], y.iloc[tmp_i], groups.iloc[tmp_i]
gss2 = GroupShuffleSplit(n_splits=1, test_size=0.5, random_state=SEED)
va_rel, _ = next(gss2.split(Xt, yt, gt))
val_idx = list(Xt.index[va_rel])
tr_idx = list(Xtab.index[tr_i])
print(f"train {len(tr_idx)}  val {len(val_idx)} ({groups.loc[val_idx].nunique()} channels)")

npri = df.loc[tr_idx, "n_prior"].values.astype(np.float32)
w_tr_np = npri / (npri + 5.0)

yva = y.loc[val_idx].values
S_val = df.loc[val_idx, "S"].values.astype(float)
N_true_val = df.loc[val_idx, f"day_{H}_views"].values.astype(float)
L_true = np.log1p(N_true_val)
r2_anchor = r2_score(L_true, np.log1p(np.clip(S_val, 0, None)))
print(f"anchor-only R2 (sanity check, must be 0.6455): {r2_anchor:.4f}")

def top20(pred):
    vv = df.loc[val_idx, ["channel_id"]].copy()
    vv["p"], vv["t"] = pred, yva
    hits = tot = 0
    for _, gp in vv.groupby("channel_id"):
        if len(gp) < 5: continue
        k = max(1, round(0.2 * len(gp)))
        hits += len(set(gp.nlargest(k, "p").index) & set(gp.nlargest(k, "t").index))
        tot += k
    return hits / tot if tot else np.nan

def report(name, pred):
    rho, _ = spearmanr(pred, yva)
    r2 = r2_score(L_true, np.log1p(np.clip(S_val * np.exp(pred), 0, None)))
    t20 = top20(pred)
    print(f"{name:<28} rho={rho:.4f}  top20={t20:.4f}  R2_e2e={r2:.4f}")
    return rho, t20, r2

# ---------------------------------------------------------------
# 3. CatBoost validation baseline (NOT deployed -- export_artifacts.py's
#    full-corpus catboost_magnitude.cbm is the deployed CatBoost model).
#    This one only gives an honest out-of-sample residual_std and the
#    reference rows for the combined sanity check below.
# ---------------------------------------------------------------
print("\n" + "=" * 70)
print("CatBoost (validation-split baseline, for residual_std + sanity refs)")
print("=" * 70)
cb = CatBoostRegressor(iterations=500, depth=6, learning_rate=0.05,
                       loss_function="MAE", verbose=0, random_state=SEED)
cb.fit(Xcb.loc[tr_idx], y.loc[tr_idx], sample_weight=w_tr_np,
       eval_set=(Xcb.loc[val_idx], y.loc[val_idx]), use_best_model=True)
pred_cb = cb.predict(Xcb.loc[val_idx])
report("CatBoost (val split)", pred_cb)

# ---------------------------------------------------------------
# 4. HistAttnV2 -- nonlinear per-modality projections
# ---------------------------------------------------------------
print("\n" + "=" * 70)
print("HistAttnV2")
print("=" * 70)

T = lambda a: torch.tensor(a).to(device)
sc = StandardScaler()
Xtr_s = T(sc.fit_transform(Xtab.loc[tr_idx]).astype(np.float32))
Xva_s = T(sc.transform(Xtab.loc[val_idx]).astype(np.float32))

Etr, Ftr, Mtr = build_hist(tr_idx)
Eva, Fva, Mva = build_hist(val_idx)
Etr, Ftr, Mtr = T(Etr), T(Ftr), T(Mtr)
Eva, Fva, Mva = T(Eva), T(Fva), T(Mva)
etr = T(emb_all[[pos[i] for i in tr_idx]])
eva = T(emb_all[[pos[i] for i in val_idx]])
ytr_t = T(y.loc[tr_idx].values.astype(np.float32))
yva_t = T(yva.astype(np.float32))
wtr_t = T(w_tr_np)

def weighted_l1(pred, target, w):
    return (torch.abs(pred - target) * w).sum() / w.sum()

TXT_OUT, IMG_OUT = 64, 32
PROJ = TXT_OUT + IMG_OUT
D = 192

class ModalityProjector(nn.Module):
    def __init__(self):
        super().__init__()
        self.text_mlp = nn.Sequential(
            nn.Linear(EMB_TXT, 128), nn.LayerNorm(128), nn.GELU(), nn.Dropout(0.3),
            nn.Linear(128, TXT_OUT), nn.GELU())
        self.img_mlp = nn.Sequential(
            nn.Linear(EMB_IMG, 64), nn.LayerNorm(64), nn.GELU(), nn.Dropout(0.3),
            nn.Linear(64, IMG_OUT), nn.GELU())

    def forward(self, emb):
        return torch.cat([self.text_mlp(emb[..., :EMB_TXT]), self.img_mlp(emb[..., EMB_TXT:])], dim=-1)

class HistAttnV2(nn.Module):
    def __init__(self, n_tab):
        super().__init__()
        self.projector = ModalityProjector()
        self.hist_proj = nn.Linear(PROJ + 3, D)
        self.q_proj = nn.Linear(PROJ, D)
        self.attn = nn.MultiheadAttention(D, num_heads=4, batch_first=True, dropout=0.1)
        self.ln = nn.LayerNorm(D)
        self.tab = nn.Sequential(nn.Linear(n_tab, 96), nn.ReLU(), nn.Dropout(0.3))
        self.tgt = nn.Sequential(nn.Linear(PROJ, 96), nn.ReLU(), nn.Dropout(0.3))
        self.head = nn.Sequential(
            nn.Linear(D + 96 + 96, 128), nn.ReLU(), nn.Dropout(0.35),
            nn.Linear(128, 48), nn.ReLU(), nn.Dropout(0.25),
            nn.Linear(48, 1))

    def forward(self, tab, tgt_emb, hist_emb, hist_feat, mask, chan_drop=0.0):
        tp = self.projector(tgt_emb)
        hp = self.projector(hist_emb)
        h = self.ln(self.hist_proj(torch.cat([hp, hist_feat], -1)))
        m = mask
        if chan_drop > 0 and self.training:
            m = m * (torch.rand(m.size(0), 1, device=m.device) >= chan_drop).float()
        empty = (m.sum(1) == 0)
        kp = (m == 0)
        kp[empty] = False
        q = self.q_proj(tp).unsqueeze(1)
        ctx, _ = self.attn(q, h, h, key_padding_mask=kp)
        ctx = ctx.squeeze(1) * (~empty).float().unsqueeze(1)
        z = torch.cat([ctx, self.tab(tab), self.tgt(tp)], -1)
        return self.head(z).squeeze(-1)

torch.manual_seed(SEED)
model = HistAttnV2(Xtr_s.shape[1]).to(device)
print(f"params: {sum(p.numel() for p in model.parameters()):,}")
opt = torch.optim.AdamW(model.parameters(), lr=3e-4, weight_decay=1e-3)
sched = torch.optim.lr_scheduler.ReduceLROnPlateau(opt, mode="min", factor=0.5, patience=6)
BATCH, n = 128, len(tr_idx)
best, best_state, wait, PAT = float("inf"), None, 0, 25

for epoch in range(300):
    model.train()
    perm = torch.randperm(n)
    for i in range(0, n, BATCH):
        b = perm[i:i + BATCH]
        opt.zero_grad()
        pred = model(Xtr_s[b], etr[b], Etr[b], Ftr[b], Mtr[b], chan_drop=0.15)
        loss = weighted_l1(pred, ytr_t[b], wtr_t[b])
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
    model.eval()
    with torch.no_grad():
        v = torch.abs(model(Xva_s, eva, Eva, Fva, Mva) - yva_t).mean().item()
    sched.step(v)
    if v < best - 1e-5:
        best, best_state, wait = v, {k: t.clone() for k, t in model.state_dict().items()}, 0
    else:
        wait += 1
    if epoch % 10 == 0:
        print(f"  epoch {epoch:3d}  val_mae={v:.4f}  best={best:.4f}")
    if wait >= PAT:
        print(f"  early stop @ {epoch}")
        break
model.load_state_dict(best_state)
model.eval()
with torch.no_grad():
    pred_v2 = model(Xva_s, eva, Eva, Fva, Mva).cpu().numpy()
report("HistAttnV2", pred_v2)

print("\n" + "=" * 70)
print(f"FULL COMPARISON   (anchor-only R2 = {r2_anchor:.4f})")
print("=" * 70)
report("CatBoost (val split)", pred_cb)
report("HistAttnV2", pred_v2)
for w in [0.3, 0.5, 0.7]:
    report(f"Ensemble ({w:.1f})", w * pred_v2 + (1 - w) * pred_cb)

# ---------------------------------------------------------------
# 5. Correct config.json's residual_std to the honest out-of-sample value
# ---------------------------------------------------------------
with open(ARTIFACTS_DIR / "config.json") as f:
    config = json.load(f)
residual_std_oos = float(np.std(yva - pred_cb))
print(f"\nresidual_std -- config.json (in-sample): {config['residual_std']:.4f}, "
      f"out-of-sample (val split): {residual_std_oos:.4f}")
config["residual_std"] = residual_std_oos
with open(ARTIFACTS_DIR / "config.json", "w") as f:
    json.dump(config, f, indent=2)
print("config.json residual_std corrected to the out-of-sample value")

# ---------------------------------------------------------------
# 6. Part B -- HistAttnV2 artifacts
# ---------------------------------------------------------------
torch.save(model.state_dict(), ARTIFACTS_DIR / "histattn_v2.pt")
print("Saved histattn_v2.pt")

with open(ARTIFACTS_DIR / "histattn_scaler.pkl", "wb") as f:
    pickle.dump(sc, f)
print("Saved histattn_scaler.pkl")

with open(ARTIFACTS_DIR / "histattn_tab_columns.json", "w") as f:
    json.dump(tab_cols, f, indent=2)
print(f"Saved histattn_tab_columns.json ({len(tab_cols)} columns)")

histattn_config = {
    "n_tab": len(tab_cols),
    "emb_txt": EMB_TXT,
    "emb_img": EMB_IMG,
    "txt_out": TXT_OUT,
    "img_out": IMG_OUT,
    "d_model": D,
    "max_hist": MAX_HIST,
    "ensemble_weight": ENSEMBLE_WEIGHT,
    "ensemble_uses_dino": False,
}
with open(ARTIFACTS_DIR / "histattn_config.json", "w") as f:
    json.dump(histattn_config, f, indent=2)
print("Saved histattn_config.json:", histattn_config)

# ---------------------------------------------------------------
# 7. Part C -- combined sanity check (CatBoost + HistAttnV2 + blend)
# ---------------------------------------------------------------
N_REFERENCE = 20
rng = np.random.RandomState(42)
ref_positions = rng.choice(len(val_idx), size=min(N_REFERENCE, len(val_idx)), replace=False)

reference_predictions = []
for pos_ in ref_positions:
    idx = val_idx[pos_]
    p_cb = float(pred_cb[pos_])
    p_hist = float(pred_v2[pos_])
    p_ens = ENSEMBLE_WEIGHT * p_hist + (1 - ENSEMBLE_WEIGHT) * p_cb
    reference_predictions.append({
        "video_id": str(df.loc[idx, "id"]),
        "expected_log_m_catboost": p_cb,
        "expected_log_m_histattn": p_hist,
        "expected_log_m_ensemble": p_ens,
        "actual_log_m": float(yva[pos_]),
    })
with open(ARTIFACTS_DIR / "histattn_reference_predictions.json", "w") as f:
    json.dump(reference_predictions, f, indent=2)
print(f"Saved histattn_reference_predictions.json ({len(reference_predictions)} cases)")

print()
print("=" * 70)
print(f"Export complete. Files in {ARTIFACTS_DIR.resolve()}:")
for p in sorted(ARTIFACTS_DIR.iterdir()):
    if p.is_file():
        print(f"  {p.name}  ({p.stat().st_size:,} bytes)")
print("=" * 70)
