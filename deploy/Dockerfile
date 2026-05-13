# UQI RAG inference image — bge-m3 embedding + bge-reranker-v2-m3 cross-encoder
# Based on NGC PyTorch 25.06 (arm64, CUDA 13, GB10 verified)
FROM nvcr.io/nvidia/pytorch:25.06-py3

# Pinned versions for reproducibility; bump deliberately when desired.
RUN pip install --no-cache-dir \
        'sentence-transformers>=3.0,<5.0' \
        'fastapi>=0.110,<1.0' \
        'uvicorn[standard]>=0.27,<1.0' \
        'pydantic>=2.5,<3.0'

WORKDIR /app
COPY embed_server.py rerank_server.py /app/

# Both ports — actual exposed one is selected at runtime by which script runs.
EXPOSE 7997 7998
