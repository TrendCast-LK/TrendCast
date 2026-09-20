"""Validate all 20 reference predictions through the inference pipeline."""
import ast, json, math, re
import numpy as np, pandas as pd
import backend.inference as inf

# Load artifacts
print('Loading artifacts...')
inf.load_artifacts()

# Load data
df = pd.read_csv(r'artifacts\training_corpus_h7_final.csv')
refs = json.load(open(r'artifacts\reference_predictions.json', 'r', encoding='utf-8'))
emb_text = np.load(r'artifacts\text_embeddings.npy')
emb_img = np.load(r'artifacts\image_embeddings_final.npy')

print(f'Testing {len(refs)} reference predictions...\n')

errs = []
for i, ref in enumerate(refs, start=1):
    # Find row
    row = df.loc[df['id'].astype(str) == str(ref['video_id'])].iloc[0]
    idx = df.index[df['id'].astype(str) == str(ref['video_id'])][0]
    
    # Get embeddings
    text = emb_text[idx]
    img = emb_img[idx]
    
    # Parse duration
    m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', str(row['video_duration']))
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    duration_s = h*3600 + mi*60 + s
    
    # Parse published_at
    published = pd.to_datetime(row['published_at'], utc=True)
    
    # Parse tags
    tag_count = len(ast.literal_eval(row['tags'])) if isinstance(row['tags'], str) and row['tags'].startswith('[') else (0 if not str(row['tags']).strip() else len(str(row['tags']).split(',')))
    
    # Build features
    feat = [
        duration_s,
        len(str(row['title'])),
        len(str(row['description'])),
        tag_count,
        math.sin(2*math.pi*published.hour/24),
        math.cos(2*math.pi*published.hour/24),
        math.sin(2*math.pi*published.dayofweek/7),
        math.cos(2*math.pi*published.dayofweek/7),
        float(np.sum((img/(np.linalg.norm(img)+1e-8))*(text/(np.linalg.norm(text)+1e-8)))),
        int(float(np.linalg.norm(img)>1e-6))
    ]
    
    # Add PCA embeddings
    feat += list(inf._state.pca_text.transform(text.reshape(1,-1))[0])
    feat += list(inf._state.pca_image.transform(img.reshape(1,-1))[0])
    
    # One-hot encode categories
    cats = sorted(df['category_id'].dropna().unique().tolist())
    for c in cats:
        feat.append(1.0 if int(row['category_id']) == c else 0.0)
    
    # Channel stats
    ch = df.groupby('channel_id').agg(
        channel_video_count=('id','count'),
        channel_median_views=('day_7_views','median'),
        channel_view_std=('day_7_views','std')
    ).reset_index()
    ch['channel_log_volatility'] = df.groupby('channel_id')['day_7_views'].apply(lambda x: np.std(np.log(x.clip(lower=1)))).values
    ch['channel_category_diversity'] = df.groupby('channel_id')['category_id'].nunique().values
    
    row_ch = ch.loc[ch['channel_id']==row['channel_id']].iloc[0]
    feat += [
        float(row_ch['channel_video_count']),
        float(row_ch['channel_median_views']),
        float(row_ch['channel_view_std']),
        float(row_ch['channel_log_volatility']),
        float(row_ch['channel_category_diversity'])
    ]
    
    # Predict
    X = np.asarray(feat, dtype=float).reshape(1,-1)
    pred = float(inf._state.magnitude_model.predict(X)[0])
    exp = float(ref['expected_log_m'])
    pct = abs(pred - exp) / max(abs(exp), 1.0)
    
    print(f'{i:2d}. Video {ref["video_id"]:12s} | pred={pred:8.5f} | exp={exp:8.5f} | error={pct*100:6.3f}%')
    errs.append(pct)

print()
print(f'Mean error: {np.mean(errs)*100:.3f}%')
print(f'Max error:  {max(errs)*100:.3f}%')
print(f'Median error: {np.median(errs)*100:.3f}%')
print(f'Std dev: {np.std(errs)*100:.3f}%')
