#!/usr/bin/env python3
"""UQI embedding server — OpenAI-compatible, sentence-transformers backed."""
import os, time
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
import torch
import uvicorn

MODEL_NAME = os.environ.get("UQI_EMBED_MODEL", "BAAI/bge-m3")
PORT       = int(os.environ.get("UQI_EMBED_PORT", "7997"))
DEVICE     = os.environ.get("UQI_EMBED_DEVICE", "auto")
if DEVICE == "auto":
    DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

print(f"[embed] loading {MODEL_NAME} on {DEVICE}...", flush=True)
model = SentenceTransformer(MODEL_NAME, device=DEVICE)
# sentence-transformers >= 3.x renamed the method; tolerate both APIs.
DIM = (getattr(model, "get_embedding_dimension", None)
       or getattr(model, "get_sentence_embedding_dimension"))()
print(f"[embed] ready: dim={DIM}", flush=True)

app = FastAPI(title="UQI embed", version="0.1")

class EmbedRequest(BaseModel):
    model: str | None = None
    input: list[str] | str
    encoding_format: str | None = None  # ignored — always float

@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_NAME, "device": DEVICE, "dim": DIM}

@app.post("/embeddings")
def embeddings(req: EmbedRequest):
    texts = req.input if isinstance(req.input, list) else [req.input]
    if not texts:
        raise HTTPException(status_code=400, detail="empty input")
    t0 = time.time()
    with torch.inference_mode():
        vecs = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True, batch_size=32)
    return {
        "object": "list",
        "data": [{"object": "embedding", "index": i, "embedding": v.tolist()} for i, v in enumerate(vecs)],
        "model": req.model or MODEL_NAME,
        "usage": {"prompt_tokens": sum(len(t) for t in texts),
                  "total_tokens": sum(len(t) for t in texts)},
        "elapsed_ms": int((time.time() - t0) * 1000),
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
