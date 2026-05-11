#!/usr/bin/env python3
"""
Phase 0 — Golden set 후보 자동 생성.

UQI 지식베이스(records 736~ 임베딩)의 실제 데이터 분포를 기반으로
대표 자연어 쿼리 30개를 정의하고, 각각에 대해 **현재 Chroma 시맨틱
검색(all-MiniLM-L6-v2)** 의 top-5 결과를 함께 기록한다.

산출물: tests/golden_set.json
  [
    {
      "id": "Q01",
      "query": "...",
      "category": "direct | concept | diagnostic | cross_type | korean | english | mixed",
      "intent": "사람이 봤을 때 이 쿼리로 찾고 싶은 것",
      "current_top5": [
        {"id": "...", "type": "...", "similarity": 0.42, "snippet": "..."},
        ...
      ],
      "expected": []          # ← 사용자 채움: 실제로 적합하다고 판단한 record_id 목록
    },
    ...
  ]

사용:
  python3 tests/golden_set_builder.py            # 골든셋 후보 작성
  python3 tests/golden_set_builder.py --update   # query 추가/수정 후 재실행
                                                  # expected는 보존, current_top5만 갱신
"""
import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from uqi_rag import UQIRAG  # noqa: E402


# ─────────────────────────────────────────────────────────
# 30 대표 쿼리 (한국어/영어/혼합 + 직접/개념/진단/교차)
# ─────────────────────────────────────────────────────────
QUERIES = [
    # ── 직접 속성 조회 (8건) ─────────────────────────────
    ("Q01", "direct",     "ibm_fez에서 실행한 최적화 결과",
        "ibm_fez QPU에서 수행된 optimization 레코드"),
    ("Q02", "direct",     "iqm_garnet에서의 QEC 효과",
        "qpu_name=iqm_garnet 인 qec_experiment 레코드"),
    ("Q03", "direct",     "CUDAQ 트랜스파일 패턴",
        "sdk=CUDAQ 인 transpile_pattern"),
    ("Q04", "direct",     "Perceval pipeline failures",
        "sdk=Perceval 인 pipeline_issue"),
    ("Q05", "direct",     "Qrisp GPU benchmark results",
        "framework=Qrisp 인 gpu_benchmark"),
    ("Q06", "direct",     "tket+sabre 조합으로 최적화한 회로",
        "combination=tket+sabre 인 optimization"),
    ("Q07", "direct",     "bit_flip code QEC 실험",
        "code=bit_flip 인 qec_experiment"),
    ("Q08", "direct",     "노이즈 시뮬레이션 실행 결과",
        "backend LIKE noise_sim% 인 execution"),

    # ── 도메인 개념 (8건) ───────────────────────────────
    ("Q09", "concept",    "게이트 수가 많이 줄어든 회로 최적화",
        "gate_reduction 가 높은 optimization 사례"),
    ("Q10", "concept",    "fidelity 가 높은 양자 실험",
        "fidelity_after 가 큰 qec_experiment 또는 fidelity 높은 noise_simulate"),
    ("Q11", "concept",    "GPU 가속이 큰 시뮬레이션",
        "speedup 가 큰 gpu_benchmark"),
    ("Q12", "concept",    "high error_rate executions",
        "error_rate 가 큰 execution"),
    ("Q13", "concept",    "Iterative Amplitude Estimation 회로",
        "circuit_name 에 IterativeAmplitudeEstimation 포함된 레코드"),
    ("Q14", "concept",    "Quantum Phase Estimation 시도",
        "circuit_name 에 qpe 또는 phase_estimation 포함"),
    ("Q15", "concept",    "옵션 가격 책정 양자 알고리즘",
        "qpe_interest_rate 또는 option pricing 관련 회로"),
    ("Q16", "concept",    "Bell state experiment",
        "circuit_name 에 bell 포함"),

    # ── 진단성 (8건) ────────────────────────────────────
    ("Q17", "diagnostic", "왜 보안 차단이 일어났나",
        "security_block — reason 다양, 정적분석 차단 사례"),
    ("Q18", "diagnostic", "os 모듈 임포트로 차단된 파일",
        "security_block 중 os 모듈 관련"),
    ("Q19", "diagnostic", "uqi_analyze stage에서의 실패",
        "stage=uqi_analyze 인 pipeline_issue"),
    ("Q20", "diagnostic", "Quandela 제출 실패 사례",
        "Perceval/Quandela 관련 pipeline_issue"),
    ("Q21", "diagnostic", "QEC를 적용했는데 효과 없었던 경우",
        "qec_experiment with effective=False"),
    ("Q22", "diagnostic", "device error 가 난 실 QPU 제출",
        "execution 중 ok=False 또는 error_rate 매우 높음"),
    ("Q23", "diagnostic", "GPU 시뮬레이션이 CPU보다 느렸던 경우",
        "gpu_benchmark with verdict=cpu_win 또는 speedup<1"),
    ("Q24", "diagnostic", "transpile workaround 가 있었던 사례",
        "transpile_pattern 중 workaround 필드 있는 레코드"),

    # ── 교차 타입 / 추론 (4건) ─────────────────────────
    ("Q25", "cross_type", "IBM에서 fidelity 가 좋지 않았던 회로",
        "ibm_* qpu 에서 noise_simulate fidelity 낮거나 qec effective=False"),
    ("Q26", "cross_type", "PennyLane 사용한 모든 작업",
        "sdk=PennyLane 또는 framework=PennyLane 모두"),
    ("Q27", "cross_type", "8큐빗 회로의 모든 처리 이력",
        "num_qubits=8 인 optimization + 같은 회로의 execution"),
    ("Q28", "cross_type", "iqm 계열 QPU에서의 최근 활동",
        "qpu_name LIKE iqm_% 인 최근 레코드"),

    # ── 한·영 혼합 어휘 (2건) ──────────────────────────
    ("Q29", "mixed",      "Qiskit 으로 transpile 할 때 자주 발생하는 issue",
        "sdk=Qiskit pipeline_issue"),
    ("Q30", "mixed",      "신한금융 양자 우편 같은 자료",  # 노이즈 쿼리(존재 X 예상) — 빈 결과 처리 검증용
        "예상: 빈 결과 또는 매우 낮은 유사도 (시스템 거짓 양성 검증)"),
]


def _snippet(record_type: str, data: dict) -> str:
    """프리뷰용 짧은 요약 (~140 chars)."""
    if record_type == "optimization":
        return (f"qpu={data.get('qpu_name')} circuit={data.get('circuit_name')} "
                f"combo={data.get('combination')} gate_red={data.get('gate_reduction')}")
    if record_type == "execution":
        return (f"qpu={data.get('qpu_name')} circuit={data.get('circuit_name')} "
                f"backend={data.get('backend')} ok={data.get('ok')} err={data.get('error_rate')}")
    if record_type == "gpu_benchmark":
        return (f"circuit={data.get('circuit_name')} fw={data.get('framework')} "
                f"speedup={data.get('speedup')} verdict={data.get('verdict')}")
    if record_type == "qec_experiment":
        return (f"qpu={data.get('qpu_name')} circuit={data.get('circuit_name')} "
                f"code={data.get('code')} f_after={data.get('fidelity_after')} eff={data.get('effective')}")
    if record_type == "pipeline_issue":
        issue = (data.get("issue") or "")[:80]
        return f"stage={data.get('stage')} sdk={data.get('sdk')} qpu={data.get('qpu_name')} issue={issue}"
    if record_type == "security_block":
        return (f"file={data.get('file_name')} reason={data.get('reason')} "
                f"tool={data.get('tool')}")
    if record_type == "transpile_pattern":
        return (f"sdk={data.get('sdk')} qpu={data.get('qpu_name')} "
                f"pattern={(data.get('pattern') or '')[:60]}")
    if record_type == "conversion_pattern":
        return (f"{data.get('from_format')}→{data.get('to_format')} "
                f"sdk={data.get('sdk')} success={data.get('success')}")
    # fallback
    return str(data)[:140]


def build(rag: UQIRAG, existing: dict | None = None) -> list:
    """현재 시맨틱 검색의 top-5를 각 쿼리에 첨부."""
    out = []
    for qid, cat, query, intent in QUERIES:
        try:
            hits = rag.search_semantic(query, limit=5)
        except Exception as e:
            hits = []
            print(f"  [{qid}] search failed: {e}", file=sys.stderr)
        current_top5 = [{
            "id":         h["id"],
            "type":       h["type"],
            "similarity": round(h.get("similarity") or 0.0, 4),
            "snippet":    _snippet(h["type"], h.get("data") or {}),
        } for h in hits]
        prev = (existing or {}).get(qid, {})
        expected = prev.get("expected", [])
        out.append({
            "id":           qid,
            "category":     cat,
            "query":        query,
            "intent":       intent,
            "current_top5": current_top5,
            "expected":     expected,  # ← 사용자가 채울 부분
        })
        n_ok = "✓" if current_top5 else "·"
        print(f"  {n_ok} {qid} [{cat:11s}] {query[:50]}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(_ROOT / "tests" / "golden_set.json"),
                    help="출력 파일 경로 (default: tests/golden_set.json)")
    ap.add_argument("--update", action="store_true",
                    help="기존 expected 보존, current_top5만 재생성")
    args = ap.parse_args()

    out_path = Path(args.out)
    existing = None
    if args.update and out_path.exists():
        existing = {x["id"]: x for x in json.loads(out_path.read_text(encoding="utf-8"))}

    print(f"Building golden set candidates → {out_path}")
    rag = UQIRAG()
    items = build(rag, existing=existing)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"✓ {len(items)} queries written.")
    print(f"  파일: {out_path}")
    print(f"  다음 단계: 각 항목의 \"expected\" 배열을 사용자가 검토·작성")
    print(f"           (current_top5 중 적합한 id를 expected 로 옮기고, 누락된 record_id 가 있으면 추가)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
