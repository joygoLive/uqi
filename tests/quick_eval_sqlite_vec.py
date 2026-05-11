#!/usr/bin/env python3
"""
Phase 2c 검증 — sqlite-vec + bge-m3 + v2 자연어화 조합의 golden set Recall@10.

baseline (chroma + all-MiniLM-L6-v2 + v1 schema-flat) 와 비교용 일회성 평가.
Phase 3a 이후엔 golden_set_eval.py --live 가 동일 역할 수행.
"""
import json
import math
import sys
import time
from pathlib import Path

import requests
import sqlite_vec

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))
from uqi_rag import _connect, EMBED_URL, EMBED_MODEL, RAG_FILE  # noqa: E402


def embed(text: str) -> list[float]:
    r = requests.post(
        f"{EMBED_URL.rstrip('/')}/embeddings",
        json={"model": EMBED_MODEL, "input": text},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["data"][0]["embedding"]


def search_vec(conn, q_vec, k=10):
    rows = conn.execute(
        "SELECT record_id, distance FROM record_vec "
        "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
        (sqlite_vec.serialize_float32(q_vec), k),
    ).fetchall()
    return [r[0] for r in rows]


def recall_mrr_ndcg(retrieved, expected, k):
    if not expected:
        return None, None, None, 0
    topk = retrieved[:k]
    es = set(expected)
    hits = sum(1 for x in topk if x in es)
    recall = hits / len(expected)
    mrr = 0.0
    for i, rid in enumerate(topk, 1):
        if rid in es:
            mrr = 1.0 / i
            break
    dcg = sum(1.0 / math.log2(i + 1) for i, rid in enumerate(topk, 1) if rid in es)
    ideal_hits = min(len(expected), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    ndcg = dcg / idcg if idcg else 0.0
    return recall, mrr, ndcg, hits


def main() -> int:
    gs = json.loads((_ROOT / "tests" / "golden_set.json").read_text(encoding="utf-8"))
    conn = _connect(RAG_FILE)

    print(f"Evaluating against sqlite-vec + bge-m3 + v2 text (K=10)\n")
    print(f"  {'Q':4s}  {'category':11s}  recall  mrr     ndcg    hits/exp  query")

    n_eval = 0
    per_cat: dict[str, dict] = {}
    sums = {"recall": 0.0, "mrr": 0.0, "ndcg": 0.0}
    t0 = time.time()
    rows = []
    for it in gs:
        qid, cat, query, expected = it["id"], it["category"], it["query"], it.get("expected") or []
        if not expected:
            continue
        n_eval += 1
        q_vec = embed(query)
        retrieved = search_vec(conn, q_vec, k=10)
        r, mrr, n, hits = recall_mrr_ndcg(retrieved, expected, 10)
        sums["recall"] += r or 0; sums["mrr"] += mrr or 0; sums["ndcg"] += n or 0
        agg = per_cat.setdefault(cat, {"n": 0, "recall": 0.0, "mrr": 0.0, "ndcg": 0.0})
        agg["n"] += 1; agg["recall"] += r or 0; agg["mrr"] += mrr or 0; agg["ndcg"] += n or 0
        rows.append((qid, cat, r, mrr, n, hits, len(expected), query[:48]))

    for qid, cat, r, mrr, n, hits, exp, query in rows:
        print(f"  {qid:4s}  {cat:11s}  {r:.3f}  {mrr:.3f}  {n:.3f}  {hits}/{exp:<6d}  {query}")

    print(f"\n  evaluated: {n_eval}")
    print(f"  total time: {time.time() - t0:.1f}s")
    print()
    print(f"  Per-category averages")
    print(f"  {'category':11s}  n   recall  mrr     ndcg")
    for cat in sorted(per_cat):
        a = per_cat[cat]
        n = a["n"]
        print(f"  {cat:11s}  {n:<3d} {a['recall']/n:.3f}  {a['mrr']/n:.3f}  {a['ndcg']/n:.3f}")
    print()
    print(f"  Overall mean Recall@10: {sums['recall']/n_eval:.3f}")
    print(f"  Overall mean MRR:       {sums['mrr']/n_eval:.3f}")
    print(f"  Overall mean NDCG@10:   {sums['ndcg']/n_eval:.3f}")
    print()
    print(f"  baseline (chroma + all-MiniLM-L6-v2 + v1 text):")
    print(f"    Recall@10 = 0.164    MRR = 0.227   NDCG@10 = 0.179")
    return 0


if __name__ == "__main__":
    sys.exit(main())
