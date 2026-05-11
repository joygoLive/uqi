#!/usr/bin/env python3
"""
Phase 0 보조 — golden_set.json 의 expected 후보를 SQL 기반으로 자동 추천.

각 쿼리의 intent 를 우리가 알기 때문에, 임베딩과 무관한 **deterministic 룰**
(필드 일치, 키워드 매칭)로 정답 후보를 먼저 뽑아둔다. 사용자는 출력된
`expected_candidates` 를 검토하고 적절한 id 들을 golden_set.json 의
"expected" 배열에 옮기면 된다.

이 추천기는 baseline (Chroma) 가 놓친 케이스를 식별하기 위해서이지,
expected 가 추천 결과와 같아야 한다는 의미는 아님 — 진실 라벨은 사용자 판단.
"""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))
from uqi_rag import UQIRAG  # noqa: E402


def _matches(records, predicate, limit=10):
    out = []
    for r in records:
        try:
            if predicate(r):
                out.append(r)
                if len(out) >= limit:
                    break
        except Exception:
            continue
    return out


def _by_type(rag, t, limit=999):
    return rag.search(record_type=t, limit=limit)


def suggest_all(rag: UQIRAG) -> dict:
    opt   = _by_type(rag, "optimization")
    exe   = _by_type(rag, "execution")
    gpu   = _by_type(rag, "gpu_benchmark")
    qec   = _by_type(rag, "qec_experiment")
    pipe  = _by_type(rag, "pipeline_issue")
    sec   = _by_type(rag, "security_block")
    trp   = _by_type(rag, "transpile_pattern")
    cvp   = _by_type(rag, "conversion_pattern")

    def ids(records, k=10):
        return [r["id"] for r in records[:k]]

    def ids_all(records):
        return [r["id"] for r in records]

    sug = {}
    sug["Q01"] = ids(_matches(opt,   lambda r: r["data"].get("qpu_name") == "ibm_fez"))
    sug["Q02"] = ids(_matches(qec,   lambda r: r["data"].get("qpu_name") == "iqm_garnet"))
    sug["Q03"] = ids(_matches(trp,   lambda r: r["data"].get("sdk")      == "CUDAQ"))
    sug["Q04"] = ids(_matches(pipe,  lambda r: r["data"].get("sdk")      == "Perceval"))
    sug["Q05"] = ids(_matches(gpu,   lambda r: r["data"].get("framework") == "Qrisp"))
    sug["Q06"] = ids(_matches(opt,   lambda r: r["data"].get("combination") == "tket+sabre"))
    sug["Q07"] = ids(_matches(qec,   lambda r: r["data"].get("code") == "bit_flip"))
    sug["Q08"] = ids(_matches(exe,   lambda r: (r["data"].get("backend") or "").startswith("noise_sim")))
    sug["Q09"] = ids(sorted(opt, key=lambda r: -(r["data"].get("gate_reduction") or 0))[:10])
    # Q10: fidelity 높은 양자 실험 — qec effective + noise_sim execution fidelity 모두 검토
    fid_qec = [r for r in qec if (r["data"].get("fidelity_after") or 0) > 0.9]
    noise_exe_hi = []
    for r in exe:
        d = r["data"]
        if not str(d.get("backend", "")).startswith("noise_sim"):
            continue
        comp = d.get("comparison") or {}
        if (comp.get("fidelity") or 0) > 0.9:
            noise_exe_hi.append(r)
    sug["Q10"] = ids(fid_qec)[:5] + ids(noise_exe_hi)[:5]
    sug["Q11"] = ids(sorted(gpu, key=lambda r: -(r["data"].get("speedup") or 0))[:10])
    sug["Q12"] = ids(sorted(exe, key=lambda r: -(r["data"].get("error_rate") or 0))[:10])
    sug["Q13"] = ids(_matches(opt + exe, lambda r: "IterativeAmplitudeEstimation" in (r["data"].get("circuit_name") or "")))
    sug["Q14"] = ids(_matches(opt + exe, lambda r: any(k in (r["data"].get("circuit_name") or "").lower() for k in ("qpe", "phase_estimation"))))
    sug["Q15"] = ids(_matches(opt + exe, lambda r: any(k in (r["data"].get("circuit_name") or "").lower() for k in ("option", "qae", "amplitudeestimation", "interest_rate"))))
    sug["Q16"] = ids(_matches(opt + exe + qec, lambda r: "bell" in (r["data"].get("circuit_name") or "").lower()))
    sug["Q17"] = ids(sec)
    sug["Q18"] = ids(_matches(sec, lambda r: "os" in (r["data"].get("reason") or "").lower() or "os" in (r["data"].get("pattern") or "").lower()))
    sug["Q19"] = ids(_matches(pipe, lambda r: r["data"].get("stage") == "uqi_analyze"))
    sug["Q20"] = ids(_matches(pipe, lambda r: r["data"].get("sdk") == "Perceval"))
    sug["Q21"] = ids(_matches(qec, lambda r: r["data"].get("effective") is False))
    sug["Q22"] = ids(_matches(exe, lambda r: r["data"].get("ok") is False))
    sug["Q23"] = ids(_matches(gpu, lambda r: (r["data"].get("speedup") or 1.0) < 1.0))
    sug["Q24"] = ids(_matches(trp, lambda r: bool(r["data"].get("workaround"))))
    # Q25: IBM에서 fidelity 좋지 않은 회로 — ibm_* noise_simulate fidelity 낮거나 qec effective=False (ibm_*)
    ibm_qec = [r for r in qec if (r["data"].get("qpu_name") or "").startswith("ibm")
               and r["data"].get("effective") is False]
    ibm_noise_lo = []
    for r in exe:
        d = r["data"]
        if not str(d.get("backend", "")).startswith("noise_sim"):
            continue
        if not (d.get("qpu_name") or "").startswith("ibm"):
            continue
        comp = d.get("comparison") or {}
        if 0 < (comp.get("fidelity") or 1.0) < 0.5:
            ibm_noise_lo.append(r)
    sug["Q25"] = ids(ibm_qec)[:5] + ids(ibm_noise_lo)[:5]
    sug["Q26"] = (
        ids(_matches(opt + exe, lambda r: (r["data"].get("sdk") or "").lower() == "pennylane"))[:5]
        + ids(_matches(gpu, lambda r: r["data"].get("framework") == "PennyLane"))[:5]
    )
    sug["Q27"] = ids(_matches(opt, lambda r: r["data"].get("num_qubits") == 8))
    sug["Q28"] = ids(sorted(
        [r for r in (opt + exe + qec) if (r["data"].get("qpu_name") or "").startswith("iqm")],
        key=lambda r: r["timestamp"],
        reverse=True,
    )[:10])
    sug["Q29"] = ids(_matches(pipe, lambda r: r["data"].get("sdk") == "Qiskit"))
    sug["Q30"] = []  # 의도적으로 빈 결과 — false-positive 검증용

    return sug


def main() -> int:
    gs_path = _ROOT / "tests" / "golden_set.json"
    items = json.loads(gs_path.read_text(encoding="utf-8"))
    rag = UQIRAG()
    suggestions = suggest_all(rag)

    print("Suggested expected sets per query (deterministic, rule-based):\n")
    for it in items:
        qid = it["id"]
        sugg = suggestions.get(qid, [])
        existing = it.get("expected") or []
        print(f"  {qid} [{it['category']:11s}] {it['query'][:60]}")
        print(f"      suggested ({len(sugg)}): {sugg[:8]}{'...' if len(sugg) > 8 else ''}")
        if existing:
            print(f"      already in expected ({len(existing)}): {existing[:8]}")
        # baseline current_top5 와 비교: 추천에 있지만 baseline 못 잡은 것 → baseline weakness
        baseline_ids = {h["id"] for h in (it.get("current_top5") or [])}
        missed_by_baseline = [x for x in sugg if x not in baseline_ids]
        if missed_by_baseline:
            print(f"      ⚠ baseline missed: {missed_by_baseline[:6]}")
        print()

    # Auto-merge: if --apply, write suggestions into golden_set.json's expected fields
    if "--apply" in sys.argv or "--types" in sys.argv:
        # records 인덱스를 만들어 type 추론에 사용
        id_to_type: dict[str, str] = {}
        for type_name in ["optimization", "execution", "gpu_benchmark", "qec_experiment",
                          "pipeline_issue", "security_block", "transpile_pattern",
                          "conversion_pattern"]:
            for r in _by_type(rag, type_name):
                id_to_type[r["id"]] = type_name

        for it in items:
            sugg = suggestions.get(it["id"], [])
            if "--apply" in sys.argv:
                # only overwrite if empty (don't clobber user edits)
                if not it.get("expected") and sugg:
                    it["expected"] = sugg
            # expected_types: expected_ids 의 type set 으로 추론 (Q06 처럼 type 이 명확한 쿼리)
            exp = it.get("expected") or []
            types = {id_to_type.get(rid) for rid in exp if id_to_type.get(rid)}
            types.discard(None)
            it["expected_types"] = sorted(types)

        gs_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✓ {gs_path.name} updated"
              f"{' (+ expected_types)' if '--types' in sys.argv else ''}.")
    else:
        print("To apply suggestions as a starting point:")
        print(f"  python3 tests/golden_set_suggest.py --apply")
        print("To re-populate expected_types from current expected lists:")
        print(f"  python3 tests/golden_set_suggest.py --types")
    return 0


if __name__ == "__main__":
    sys.exit(main())
