# TrendCast API. Build from the repository root (inference.py reads
# ../ensemble_artifacts relative to backend/):
#   docker build -f backend/Dockerfile -t trendcast-backend .
# Usually built and run through the root docker-compose.yml.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/models/huggingface

# libgomp: OpenMP runtime that CatBoost and torch load at import.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

# CPU-only torch first (the version pinned in requirements.txt), so the install
# below finds it satisfied instead of pulling the multi-GB CUDA build.
COPY backend/requirements.txt .
RUN pip install "$(grep -E '^torch==' requirements.txt)" --index-url https://download.pytorch.org/whl/cpu \
    && pip install -r requirements.txt

# Bake the two CLIP encoders into the image, so startup doesn't download them.
RUN python -c "from sentence_transformers import SentenceTransformer as S; \
S('sentence-transformers/clip-ViT-B-32-multilingual-v1'); S('sentence-transformers/clip-ViT-B-32')"

COPY ensemble_artifacts /app/ensemble_artifacts
COPY backend /app/backend

RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/backend/uploads \
    && chown -R app:app /app/backend/uploads /models
USER app

# Models are already in the image; never reach out to Hugging Face at runtime.
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=8)"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
