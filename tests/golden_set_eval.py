#!/usr/bin/env python3
"""
Phase 0/7 — Golden set 회귀 평가.

tests/golden_set.json 의 expected (사용자가 검토·작성) 를 정답지로 두고,
지정한 검색 백엔드의 top-K 결과에 대해 Recall@K, MRR, NDCG@K 를 계산.

비교 모드:
  - --baseline   : golden_set.json 의 `current_top5` 를 그대로 사용
                   (= Chroma + all-MiniLM-L6-v2 시점에 캡처된 결과)
  - --live       : 현재 UQIRAG.search_semantic() 를 호출
                   (= 인덱스 / 임베딩 교체 후 효과 측정용)

사용:
  python3 tests/golden_set_eval.py --baseline   # baseline 회귀치
  python3 tests/golden_set_eval.py --live       # 현재 라이브 백엔드 회귀치
  python3 tests/golden_set_eval.py --live --k 10
"""
import argparse
import json
import math
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent


def metrics(retrieved_ids: list[str], expected_ids: list[str], k: int) -> dict:
    """Recall@K + MRR + NDCG@K (binary relevance)."""
    if not expected_ids:
        return {"recall": None, "mrr": None, "ndcg": None, "hits": 0, "expected": 0}
    topk = retrieved_ids[:k]
    expected_set = set(expected_ids)
    hits = sum(1 for x in topk if x in expected_set)
    # Recall@K
    recall = hits / len(expected_ids)
    # MRR (first relevant rank)
    mrr = 0.0
    for i, rid in enumerate(topk, start=1):
        if rid in expected_set:
            mrr = 1.0 / i
            break
    # NDCG@K with binary relevance
    dcg = sum(1.0 / math.log2(i + 1) for i, rid in enumerate(topk, start=1) if rid in expected_set)
    ideal_hits = min(len(expected_ids), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    ndcg = (dcg / idcg) if idcg > 0 else 0.0
    return {
        "recall": round(recall, 4),
        "mrr":    round(mrr, 4),
        "ndcg":   round(ndcg, 4),
        "hits":   hits,
        "expected": len(expected_ids),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gs", default=str(_ROOT / "tests" / "golden_set.json"))
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--baseline", action="store_true",
                     help="use golden_set.json's current_top5 as the retrieval source")
    src.add_argument("--live", action="store_true",
                     help="call UQIRAG.search_semantic() live")
    ap.add_argument("--k", type=int, default=10)
    args = ap.parse_args()

    gs_path = Path(args.gs)
    if not gs_path.exists():
        print(f"ERROR: {gs_path} not found. Run golden_set_builder.py first.", file=sys.stderr)
        return 1

    items = json.loads(gs_path.read_text(encoding="utf-8"))

    rag = None
    if args.live:
        sys.path.insert(0, str(_ROOT / "src"))
        from uqi_rag import UQIRAG
        rag = UQIRAG()

    # Aggregate
    n_eval = 0
    sums = {"recall": 0.0, "mrr": 0.0, "ndcg": 0.0}
    per_cat: dict[str, dict] = {}
    per_q_rows = []
    skipped = []

    for it in items:
        qid, cat, query, expected = it["id"], it["category"], it["query"], it.get("expected") or []
        if not expected:
            skipped.append(qid)
            continue
        n_eval += 1

        # Get retrieved ids
        if args.live:
            try:
                hits = rag.search_semantic(query, limit=args.k)
                retrieved = [h["id"] for h in hits]
            except Exception as e:
                retrieved = []
                print(f"  [{qid}] live search failed: {e}", file=sys.stderr)
        else:
            retrieved = [h["id"] for h in it.get("current_top5", [])][:args.k]

        m = metrics(retrieved, expected, args.k)
        sums["recall"] += m["recall"] or 0.0
        sums["mrr"]    += m["mrr"]    or 0.0
        sums["ndcg"]   += m["ndcg"]   or 0.0
        per_cat.setdefault(cat, {"n": 0, "recall": 0.0, "mrr": 0.0, "ndcg": 0.0})
        per_cat[cat]["n"]      += 1
        per_cat[cat]["recall"] += m["recall"] or 0.0
        per_cat[cat]["mrr"]    += m["mrr"]    or 0.0
        per_cat[cat]["ndcg"]   += m["ndcg"]   or 0.0
        per_q_rows.append((qid, cat, m["recall"], m["mrr"], m["ndcg"], m["hits"], m["expected"], query[:48]))

    src_label = "BASELINE (current_top5 in golden_set)" if args.baseline else "LIVE (UQIRAG.search_semantic)"
    print(f"\nGolden set evaluation — {src_label}, K={args.k}")
    print(f"  evaluated: {n_eval} / total {len(items)}")
    if skipped:
        print(f"  skipped (no expected set): {len(skipped)} → {skipped[:6]}{'...' if len(skipped) > 6 else ''}")
    if n_eval == 0:
        print("\nNo expected sets present. Fill in `expected` arrays in golden_set.json first.")
        return 2

    # Per-query rows
    print()
    print(f"  {'Q':4s}  {'category':11s}  recall  mrr     ndcg    hits/exp  query")
    for qid, cat, r, mrr, n, hits, exp, query in per_q_rows:
        print(f"  {qid:4s}  {cat:11s}  {r:.3f}  {mrr:.3f}  {n:.3f}  {hits}/{exp:<6d}  {query}")

    print()
    print("  Per-category averages")
    print(f"  {'category':11s}  n   recall  mrr     ndcg")
    for cat, agg in sorted(per_cat.items()):
        n = agg["n"]
        print(f"  {cat:11s}  {n:<3d} {agg['recall']/n:.3f}  {agg['mrr']/n:.3f}  {agg['ndcg']/n:.3f}")

    print()
    print(f"  Overall mean Recall@{args.k}: {sums['recall']/n_eval:.3f}")
    print(f"  Overall mean MRR:          {sums['mrr']/n_eval:.3f}")
    print(f"  Overall mean NDCG@{args.k}:    {sums['ndcg']/n_eval:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
