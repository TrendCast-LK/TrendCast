"""
EXPORT SCRIPT — produces every artifact the integration guide expects.

Run this from the artifacts/ folder. It needs three files already present:
    training_corpus_h7_final.csv
    text_embeddings.npy
    image_embeddings_final.npy

It retrains all six CatBoost models on the FULL corpus (not the train split --
for deployment you want every row) and writes eleven files.

Runtime: a few minutes on CPU.

    pip install catboost scikit-learn pandas numpy joblib --break-system-packages
"""
import numpy as np
import pandas as pd
import json, re, os
import joblib
from sklearn.decomposition import PCA
from catboost import CatBoostRegressor, CatBoostClassifier

H = 7
N_COMPONENTS = 32
OUT = "."          # write artifacts alongside the inputs

print("=" * 70)
print("TRENDCAST ARTIFACT EXPORT")
print("=" * 70)

# ---------------------------------------------------------------
# 1. Load
# ---------------------------------------------------------------
df = pd.read_csv("training_corpus_h7_final.csv")
text_embeddings = np.load("text_embeddings.npy")
image_embeddings = np.load("image_embeddings_final.npy")

assert len(df) == len(text_embeddings) == len(image_embeddings), (
    f"Row mismatch: df={len(df)}, text={len(text_embeddings)}, img={len(image_embeddings)}"
)
print(f"Loaded {len(df)} videos")

# ---------------------------------------------------------------
# 2. Tabular features (must match the guide exactly)
# ---------------------------------------------------------------
def parse_duration(iso):
    if pd.isna(iso):
        return np.nan
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", str(iso))
    if not m:
        return np.nan
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s

def count_tags(t):
    if not isinstance(t, str):
        return 0
    try:
        parsed = json.loads(t)
        return len(parsed) if isinstance(parsed, list) else 0
    except Exception:
        return t.count(",") + 1 if t.strip() else 0

df["duration_s"] = df["video_duration"].apply(parse_duration)
df["title_length"] = df["title"].fillna("").str.len()
df["description_length"] = df["description"].fillna("").str.len()
df["tag_count"] = df["tags"].apply(count_tags)

df["published_at"] = pd.to_datetime(df["published_at"], utc=True)
df["publish_hour_sin"] = np.sin(2 * np.pi * df["published_at"].dt.hour / 24)
df["publish_hour_cos"] = np.cos(2 * np.pi * df["published_at"].dt.hour / 24)
df["publish_dow_sin"] = np.sin(2 * np.pi * df["published_at"].dt.dayofweek / 7)
df["publish_dow_cos"] = np.cos(2 * np.pi * df["published_at"].dt.dayofweek / 7)

# has_thumbnail: real embeddings have non-trivial norm
img_norm = np.linalg.norm(image_embeddings, axis=1)
df["has_thumbnail"] = (img_norm > 1e-6).astype(int)

def cos_sim(a, b):
    an = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-8)
    bn = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-8)
    return np.sum(an * bn, axis=1)

df["thumbnail_title_alignment"] = cos_sim(image_embeddings, text_embeddings)
print("Tabular features built")

# ---------------------------------------------------------------
# 3. PCA -- fit ONCE here, save, never refit at inference
# ---------------------------------------------------------------
pca_text = PCA(n_components=N_COMPONENTS, random_state=0).fit(text_embeddings)
pca_image = PCA(n_components=N_COMPONENTS, random_state=0).fit(image_embeddings)

text_cols = [f"text_pc{i}" for i in range(N_COMPONENTS)]
img_cols = [f"img_pc{i}" for i in range(N_COMPONENTS)]
df[text_cols] = pca_text.transform(text_embeddings)
df[img_cols] = pca_image.transform(image_embeddings)

joblib.dump(pca_text, os.path.join(OUT, "pca_text.pkl"))
joblib.dump(pca_image, os.path.join(OUT, "pca_image.pkl"))
print(f"PCA fitted and saved "
      f"(text var explained={pca_text.explained_variance_ratio_.sum():.3f}, "
      f"image={pca_image.explained_variance_ratio_.sum():.3f})")

# ---------------------------------------------------------------
# 4. Channel features
# ---------------------------------------------------------------
ch = df.groupby("channel_id").agg(
    channel_video_count=("id", "count"),
    channel_median_views=(f"day_{H}_views", "median"),
    channel_view_std=(f"day_{H}_views", "std"),
).reset_index()
ch["channel_log_volatility"] = (
    df.groupby("channel_id")[f"day_{H}_views"]
    .apply(lambda x: np.std(np.log(x.clip(lower=1)))).values
)
ch["channel_category_diversity"] = df.groupby("channel_id")["category_id"].nunique().values
df = df.merge(ch, on="channel_id", how="left")
chan_cols = ["channel_video_count", "channel_median_views", "channel_view_std",
             "channel_log_volatility", "channel_category_diversity"]

# ---------------------------------------------------------------
# 5. Category one-hot -- record the exact category list
# ---------------------------------------------------------------
df = df.loc[:, ~df.columns.str.startswith("cat_")]
categories = sorted(df["category_id"].dropna().unique().tolist())
cat_d = pd.get_dummies(df["category_id"], prefix="cat")
df = pd.concat([df, cat_d], axis=1)

feature_cols = (
    ["duration_s", "title_length", "description_length", "tag_count",
     "publish_hour_sin", "publish_hour_cos", "publish_dow_sin", "publish_dow_cos",
     "thumbnail_title_alignment", "has_thumbnail"]
    + text_cols + img_cols + list(cat_d.columns) + chan_cols
)
X = df[feature_cols].fillna(0)
X = X.loc[:, ~X.columns.duplicated()]
feature_cols = X.columns.tolist()
print(f"Feature matrix: {X.shape}")

# ---------------------------------------------------------------
# 6. Sample weights (k=5, the adopted config)
# ---------------------------------------------------------------
tmp = df.sort_values(["channel_id", "published_at"]).copy()
tmp["n_prior"] = tmp.groupby("channel_id").cumcount()
df["n_prior"] = tmp["n_prior"].reindex(df.index)
w = df["n_prior"].values.astype(float)
w = w / (w + 5.0)

# ---------------------------------------------------------------
# 7. Train the six models on the FULL corpus
# ---------------------------------------------------------------
print("\nTraining models...")

y_mag = df["log_m_clipped"]
m_mag = CatBoostRegressor(iterations=500, depth=6, learning_rate=0.05,
                          loss_function="MAE", verbose=0, random_state=0)
m_mag.fit(X, y_mag, sample_weight=w)
m_mag.save_model(os.path.join(OUT, "catboost_magnitude.cbm"))
print("  magnitude       -> catboost_magnitude.cbm")

usable = df["shape_usable"] == True
y_form = (df.loc[usable, "shape_form"] == "logistic").astype(int)
m_form = CatBoostClassifier(iterations=400, depth=5, learning_rate=0.05,
                            verbose=0, random_state=0)
m_form.fit(X.loc[usable], y_form)
m_form.save_model(os.path.join(OUT, "catboost_shape_form.cbm"))
print("  shape family    -> catboost_shape_form.cbm")

for form, params in [("power", ["c", "theta"]), ("logistic", ["k", "t0"])]:
    for p in params:
        mask = usable & (df["shape_form"] == form) & df[p].notna()
        mdl = CatBoostRegressor(iterations=300, depth=5, learning_rate=0.05,
                                loss_function="MAE", verbose=0, random_state=0)
        mdl.fit(X.loc[mask], df.loc[mask, p])
        fn = f"catboost_shape_{p}.cbm"
        mdl.save_model(os.path.join(OUT, fn))
        print(f"  {form}/{p:<10} -> {fn}  ({mask.sum()} rows)")

# ---------------------------------------------------------------
# 8. Maturation curve g(d)
# ---------------------------------------------------------------
g = {}
for d in range(1, H + 1):
    ratio = df[f"day_{d}_views"] / df[f"day_{H}_views"].replace(0, np.nan)
    g[str(d)] = float(ratio.median())
with open(os.path.join(OUT, "maturation_curve.json"), "w") as f:
    json.dump(g, f, indent=2)
print(f"\nMaturation curve: { {k: round(v,3) for k,v in g.items()} }")

# ---------------------------------------------------------------
# 9. Feature column order
# ---------------------------------------------------------------
with open(os.path.join(OUT, "feature_columns.json"), "w") as f:
    json.dump(feature_cols, f, indent=2)
print(f"Feature order saved ({len(feature_cols)} columns)")

# ---------------------------------------------------------------
# 10. Config: clip bounds, residual std, mean embeddings, categories
# ---------------------------------------------------------------
resid = y_mag.values - m_mag.predict(X)
mean_img = image_embeddings[img_norm > 1e-6].mean(axis=0)
mean_txt = text_embeddings.mean(axis=0)

config = {
    "horizon_days": H,
    "pca_components": N_COMPONENTS,
    "log_m_min": float(df["log_m_clipped"].min()),
    "log_m_max": float(df["log_m_clipped"].max()),
    "residual_std": float(np.std(resid)),
    "band_multiplier": 0.8,
    "min_prior_videos": 5,
    "max_history_videos": 30,
    "categories": [int(c) for c in categories],
    "shape_param_bounds": {
        "c": [0.05, 100], "theta": [0.05, 20],
        "k": [0.05, 10], "t0": [-5, 7],
    },
    "mean_image_embedding": mean_img.tolist(),
    "mean_text_embedding": mean_txt.tolist(),
    "text_input_template": "{title}. {title}. {tags_joined}",
    "image_encoder": "clip-ViT-B-32",
    "text_encoder": "clip-ViT-B-32-multilingual-v1",
    "n_training_videos": int(len(df)),
    "n_training_channels": int(df["channel_id"].nunique()),
}
with open(os.path.join(OUT, "config.json"), "w") as f:
    json.dump(config, f, indent=2)
print(f"Config saved (residual_std={config['residual_std']:.4f})")

# ---------------------------------------------------------------
# 11. Reference predictions for the sanity test (guide section 7)
# ---------------------------------------------------------------
sample = df.sample(min(20, len(df)), random_state=42)
ref = []
for idx in sample.index:
    row = X.loc[[idx]]
    lm = float(m_mag.predict(row)[0])
    ref.append({
        "video_id": str(df.loc[idx, "id"]),
        "channel_id": str(df.loc[idx, "channel_id"]),
        "title": str(df.loc[idx, "title"])[:80],
        "duration": str(df.loc[idx, "video_duration"]),
        "S": float(df.loc[idx, "S"]),
        "expected_log_m": lm,
        "expected_m": float(np.exp(lm)),
        "expected_N7": float(df.loc[idx, "S"] * np.exp(lm)),
        "actual_N7": float(df.loc[idx, f"day_{H}_views"]),
    })
with open(os.path.join(OUT, "reference_predictions.json"), "w") as f:
    json.dump(ref, f, indent=2)
print("Reference predictions saved (20 rows, for the integration sanity test)")

# ---------------------------------------------------------------
# Summary
# ---------------------------------------------------------------
print("\n" + "=" * 70)
print("EXPORTED FILES")
print("=" * 70)
for fn in ["catboost_magnitude.cbm", "catboost_shape_form.cbm",
           "catboost_shape_c.cbm", "catboost_shape_theta.cbm",
           "catboost_shape_k.cbm", "catboost_shape_t0.cbm",
           "pca_text.pkl", "pca_image.pkl",
           "feature_columns.json", "maturation_curve.json", "config.json",
           "reference_predictions.json"]:
    p = os.path.join(OUT, fn)
    status = f"{os.path.getsize(p)/1024:8.1f} KB" if os.path.exists(p) else "   MISSING"
    print(f"  {status}  {fn}")

print("\nNOTE: models are trained on the FULL corpus here (correct for deployment).")
print("The metrics in the documentation come from the channel-disjoint validation")
print("split and should not be recomputed from these models.")