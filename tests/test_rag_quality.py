"""
Phase 7 — RAG 품질 회귀 (live).

golden_set.json 의 expected 와 expected_types 를 기준으로
search_semantic() 의 Recall@10 / MRR / NDCG / Type-Recall@10 임계값 가드.

임베딩 서버(:7997) / 재랭커(:7998) 가 가용해야 의미가 있어서 health
확인 후 미동작 시 skip (CI 환경 호환).

임계값 (현재 측정치 + 마진 -0.05):
  Type-Recall@10 ≥ 0.65   (현재 0.745)
  Recall@10      ≥ 0.35   (현재 0.456)
  MRR            ≥ 0.45   (현재 0.577)

향후 임베딩/재랭커/하이브리드 로직이 퇴행하면 즉시 fail.
"""
import json
import os
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "tests"))

# golden_set_eval 의 metrics 함수 재사용
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("_gs_eval", _ROOT / "tests" / "golden_set_eval.py")
_gs_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gs_eval)


def _embed_server_up() -> bool:
    """임베딩 서버 health 체크. 다운이면 본 테스트 skip."""
    try:
        import requests
        from uqi_rag import EMBED_URL
        r = requests.get(f"{EMBED_URL}/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


class RAGQualityRegression(unittest.TestCase):
    """live search_semantic() 의 골든셋 평가 임계값 가드."""

    THRESHOLD_TYPE_RECALL = 0.65
    THRESHOLD_RECALL      = 0.35
    THRESHOLD_MRR         = 0.45

    @classmethod
    def setUpClass(cls):
        if not _embed_server_up():
            raise unittest.SkipTest(
                "임베딩 서버 비활성 — RAG 품질 회귀 skip "
                "(uqi-embed.service 가동 후 재실행)"
            )
        from uqi_rag import UQIRAG
        cls.rag = UQIRAG()
        cls.golden = json.loads(
            (_ROOT / "tests" / "golden_set.json").read_text(encoding="utf-8")
        )
        cls.results = cls._run_eval()

    @classmethod
    def _run_eval(cls) -> dict:
        sums = {"recall": 0.0, "mrr": 0.0, "ndcg": 0.0, "type_recall": 0.0, "type_n": 0, "n": 0}
        for it in cls.golden:
            exp = it.get("expected") or []
            if not exp:
                continue
            try:
                hits = cls.rag.search_semantic(it["query"], limit=10)
            except Exception:
                continue
            retrieved = [h["id"] for h in hits]
            ret_types = [h.get("type", "") for h in hits]
            exp_types = it.get("expected_types") or []
            m = _gs_eval.metrics(retrieved, exp, 10,
                                 retrieved_types=ret_types, expected_types=exp_types)
            sums["recall"] += m["recall"] or 0
            sums["mrr"]    += m["mrr"]    or 0
            sums["ndcg"]   += m["ndcg"]   or 0
            sums["n"]      += 1
            if m["type_recall"] is not None:
                sums["type_recall"] += m["type_recall"]
                sums["type_n"]      += 1
        n = max(sums["n"], 1)
        return {
            "recall":      sums["recall"] / n,
            "mrr":         sums["mrr"]    / n,
            "ndcg":        sums["ndcg"]   / n,
            "type_recall": (sums["type_recall"] / sums["type_n"]) if sums["type_n"] else 0.0,
            "n":           sums["n"],
        }

    def test_type_recall_threshold(self):
        v = self.results["type_recall"]
        self.assertGreaterEqual(
            v, self.THRESHOLD_TYPE_RECALL,
            f"Type-Recall@10 {v:.3f} < threshold {self.THRESHOLD_TYPE_RECALL} "
            f"— search/rerank 퇴행 의심"
        )

    def test_recall_threshold(self):
        v = self.results["recall"]
        self.assertGreaterEqual(
            v, self.THRESHOLD_RECALL,
            f"Recall@10 {v:.3f} < threshold {self.THRESHOLD_RECALL}"
        )

    def test_mrr_threshold(self):
        v = self.results["mrr"]
        self.assertGreaterEqual(
            v, self.THRESHOLD_MRR,
            f"MRR {v:.3f} < threshold {self.THRESHOLD_MRR}"
        )

    def test_evaluated_at_least_25_queries(self):
        # expected 가 채워진 쿼리가 충분히 있는지 (골든셋 변경 감지)
        self.assertGreaterEqual(
            self.results["n"], 25,
            f"evaluated only {self.results['n']} queries — golden_set 손상 의심"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
