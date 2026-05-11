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


def metrics(retrieved_ids: list[str], expected_ids: list[str], k: int,
            retrieved_types: list[str] | None = None,
            expected_types: list[str] | None = None) -> dict:
    """Recall@K + MRR + NDCG@K + Type-Recall@K.

    Type-Recall@K = (top-K 결과 중 expected_types 에 속하는 것의 비율).
    expected_ids 가 시간순 sampling 으로 좁아도, 의미적으로 같은 type 의 다른
    record 를 검색기가 가져오는 게 정상인 경우(Q06 'tket+sabre' 처럼 237건
    모두 valid)에 더 충실한 측정.
    """
    if not expected_ids:
        return {"recall": None, "mrr": None, "ndcg": None, "type_recall": None,
                "hits": 0, "expected": 0}
    topk = retrieved_ids[:k]
    expected_set = set(expected_ids)
    hits = sum(1 for x in topk if x in expected_set)
    recall = hits / len(expected_ids)
    mrr = 0.0
    for i, rid in enumerate(topk, start=1):
        if rid in expected_set:
            mrr = 1.0 / i
            break
    dcg = sum(1.0 / math.log2(i + 1) for i, rid in enumerate(topk, start=1) if rid in expected_set)
    ideal_hits = min(len(expected_ids), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    ndcg = (dcg / idcg) if idcg > 0 else 0.0

    # Type-Recall@K
    type_recall = None
    if expected_types and retrieved_types is not None:
        type_set = set(expected_types)
        topk_types = retrieved_types[:k]
        if topk_types:
            type_hits = sum(1 for t in topk_types if t in type_set)
            type_recall = type_hits / len(topk_types)

    return {
        "recall":      round(recall, 4),
        "mrr":         round(mrr, 4),
        "ndcg":        round(ndcg, 4),
        "type_recall": round(type_recall, 4) if type_recall is not None else None,
        "hits":        hits,
        "expected":    len(expected_ids),
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
    sums = {"recall": 0.0, "mrr": 0.0, "ndcg": 0.0, "type_recall": 0.0, "type_n": 0}
    per_cat: dict[str, dict] = {}
    per_q_rows = []
    skipped = []

    for it in items:
        qid, cat, query, expected = it["id"], it["category"], it["query"], it.get("expected") or []
        exp_types = it.get("expected_types") or []
        if not expected:
            skipped.append(qid)
            continue
        n_eval += 1

        # Get retrieved ids + types
        retrieved: list[str] = []
        retrieved_types: list[str] = []
        if args.live:
            try:
                hits = rag.search_semantic(query, limit=args.k)
                retrieved       = [h["id"]   for h in hits]
                retrieved_types = [h.get("type", "") for h in hits]
            except Exception as e:
                print(f"  [{qid}] live search failed: {e}", file=sys.stderr)
        else:
            retrieved       = [h["id"]   for h in it.get("current_top5", [])][:args.k]
            retrieved_types = [h.get("type", "") for h in it.get("current_top5", [])][:args.k]

        m = metrics(retrieved, expected, args.k,
                    retrieved_types=retrieved_types, expected_types=exp_types)
        sums["recall"] += m["recall"] or 0.0
        sums["mrr"]    += m["mrr"]    or 0.0
        sums["ndcg"]   += m["ndcg"]   or 0.0
        if m["type_recall"] is not None:
            sums["type_recall"] += m["type_recall"]
            sums["type_n"]      += 1
        per_cat.setdefault(cat, {"n": 0, "recall": 0.0, "mrr": 0.0, "ndcg": 0.0,
                                 "type_recall": 0.0, "type_n": 0})
        per_cat[cat]["n"]      += 1
        per_cat[cat]["recall"] += m["recall"] or 0.0
        per_cat[cat]["mrr"]    += m["mrr"]    or 0.0
        per_cat[cat]["ndcg"]   += m["ndcg"]   or 0.0
        if m["type_recall"] is not None:
            per_cat[cat]["type_recall"] += m["type_recall"]
            per_cat[cat]["type_n"]      += 1
        per_q_rows.append((qid, cat, m["recall"], m["mrr"], m["ndcg"],
                           m["type_recall"], m["hits"], m["expected"], query[:42]))

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
    print(f"  {'Q':4s}  {'category':11s}  recall  mrr     ndcg    tRec   hits/exp  query")
    for qid, cat, r, mrr, n, tr, hits, exp, query in per_q_rows:
        tr_s = f"{tr:.3f}" if tr is not None else " —  "
        print(f"  {qid:4s}  {cat:11s}  {r:.3f}  {mrr:.3f}  {n:.3f}  {tr_s}  {hits}/{exp:<6d}  {query}")

    print()
    print("  Per-category averages")
    print(f"  {'category':11s}  n   recall  mrr     ndcg    tRec")
    for cat, agg in sorted(per_cat.items()):
        n = agg["n"]
        tn = agg.get("type_n", 0)
        tr_avg = (agg["type_recall"] / tn) if tn else None
        tr_s = f"{tr_avg:.3f}" if tr_avg is not None else "  —  "
        print(f"  {cat:11s}  {n:<3d} {agg['recall']/n:.3f}  {agg['mrr']/n:.3f}  {agg['ndcg']/n:.3f}  {tr_s}")

    print()
    print(f"  Overall mean Recall@{args.k}:      {sums['recall']/n_eval:.3f}")
    print(f"  Overall mean MRR:             {sums['mrr']/n_eval:.3f}")
    print(f"  Overall mean NDCG@{args.k}:        {sums['ndcg']/n_eval:.3f}")
    if sums["type_n"] > 0:
        print(f"  Overall mean Type-Recall@{args.k}: {sums['type_recall']/sums['type_n']:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
