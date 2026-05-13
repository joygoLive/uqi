#!/usr/bin/env python3
"""UQI cross-encoder reranker server.

POST /rerank
  {"query":"...", "documents":["...","..."], "top_n": 5}
→ [{"index": 2, "score": 0.97}, ...]
"""
import os, time
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sentence_transformers import CrossEncoder
import torch
import uvicorn

MODEL_NAME = os.environ.get("UQI_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
PORT       = int(os.environ.get("UQI_RERANK_PORT", "7998"))
DEVICE     = os.environ.get("UQI_RERANK_DEVICE", "auto")
if DEVICE == "auto":
    DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

print(f"[rerank] loading {MODEL_NAME} on {DEVICE}...", flush=True)
model = CrossEncoder(MODEL_NAME, device=DEVICE, trust_remote_code=False)
print(f"[rerank] ready", flush=True)

app = FastAPI(title="UQI rerank", version="0.1")

class RerankRequest(BaseModel):
    query: str
    documents: list[str]
    top_n: int | None = None
    model: str | None = None

@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_NAME, "device": DEVICE}

@app.post("/rerank")
def rerank(req: RerankRequest):
    if not req.query or not req.documents:
        raise HTTPException(status_code=400, detail="query and documents required")
    t0 = time.time()
    pairs = [[req.query, d] for d in req.documents]
    with torch.inference_mode():
        scores = model.predict(pairs, batch_size=32, show_progress_bar=False)
    ranked = sorted(
        [{"index": i, "score": float(s)} for i, s in enumerate(scores)],
        key=lambda x: -x["score"],
    )
    if req.top_n:
        ranked = ranked[: req.top_n]
    return {
        "model": req.model or MODEL_NAME,
        "results": ranked,
        "elapsed_ms": int((time.time() - t0) * 1000),
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
