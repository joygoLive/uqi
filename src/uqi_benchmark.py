# uqi_benchmark.py
from qiskit_ibm_runtime import QiskitRuntimeService, SamplerV2
from qiskit_aer.primitives import SamplerV2 as AerSamplerV2
from dotenv import load_dotenv
import os, json, statistics, math, numpy as np
from datetime import datetime
from qiskit import QuantumCircuit
from qiskit.circuit.library import (
    QFT, GroverOperator, PhaseEstimation, RealAmplitudes, QAOAAnsatz,
    ZZFeatureMap, EfficientSU2
)
from qiskit.quantum_info import SparsePauliOp
from qiskit.transpiler import generate_preset_pass_manager

load_dotenv()
service = QiskitRuntimeService(
    channel="ibm_quantum_platform",
    token=os.getenv("IBM_QUANTUM_TOKEN")
)
backend = service.backend("ibm_fez")
props = backend.properties()

# ── 하드웨어 특성 ──────────────────────────
cz_errors, sq_errors, readout_errors = [], [], []
for gate in props.gates:
    for param in gate.parameters:
        if param.name == 'gate_error':
            if gate.gate == 'cz' and len(gate.qubits) == 2:
                cz_errors.append(param.value)
            elif gate.gate in ('sx', 'x') and len(gate.qubits) == 1:
                sq_errors.append(param.value)
for q in range(backend.num_qubits):
    readout_errors.append(props.qubit_property(q, 'readout_error')[0])

e_cz      = statistics.median(cz_errors)
e_1q      = statistics.median(sq_errors)
e_readout = statistics.median(readout_errors)

print(f"CZ error: {e_cz:.5f} | 1Q error: {e_1q:.5f} | Readout: {e_readout:.4f}")

def predicted_fidelity(n_cz, n_1q, n_qubits):
    return (1 - e_cz)**n_cz * (1 - e_1q)**n_1q * (1 - e_readout)**n_qubits

pm = generate_preset_pass_manager(target=backend.target, optimization_level=3)

def transpile_circuit(qc):
    if not qc.cregs:
        qc.measure_all()
    transpiled = pm.run(qc)
    ops = transpiled.count_ops()
    n_cz = ops.get('cz', 0)
    n_1q = sum(v for k, v in ops.items() if k in ('sx', 'x', 'rz', 'id'))
    return transpiled, n_cz, n_1q

def bind_parameters(qc):
    if qc.parameters:
        param_values = {p: np.random.uniform(0, 2*np.pi) for p in qc.parameters}
        return qc.assign_parameters(param_values)
    return qc

def compute_tvd(dist1, dist2):
    all_keys = set(dist1) | set(dist2)
    total1, total2 = sum(dist1.values()), sum(dist2.values())
    return sum(abs(dist1.get(k,0)/total1 - dist2.get(k,0)/total2) for k in all_keys) / 2

# ── 회로 빌더 ────────────────────────────────
def build_qaoa(n, reps):
    ops = [('I'*i + 'ZZ' + 'I'*(n-2-i), 1.0) for i in range(n-1)]
    return QAOAAnsatz(SparsePauliOp.from_list(ops), reps=reps)

def build_grover(n):
    oracle = QuantumCircuit(n)
    oracle.cz(0, n-1)
    grover_op = GroverOperator(oracle)
    qc = QuantumCircuit(n)
    qc.h(range(n))
    qc.compose(grover_op, inplace=True)
    return qc

def build_simon(n):
    qc = QuantumCircuit(2*n)
    qc.h(range(n))
    for i in range(n):
        qc.cx(i, n+i)
    qc.h(range(n))
    return qc

def build_ghz(n):
    qc = QuantumCircuit(n)
    qc.h(0)
    for i in range(n-1):
        qc.cx(i, i+1)
    return qc

def build_iqae(n):
    A = QuantumCircuit(1)
    A.ry(0.5, 0)
    return PhaseEstimation(n, A)

def build_qpe(n):
    A = QuantumCircuit(1)
    A.x(0)
    return PhaseEstimation(n, A)

def build_qnn(n):
    return ZZFeatureMap(n, reps=1).compose(RealAmplitudes(n, reps=1))

EXPERIMENTS = [
    ("QAOA(p=1)", "최적화",    "얕은/파라미터有", 2,  lambda: build_qaoa(2,  1)),
    ("QAOA(p=1)", "최적화",    "얕은/파라미터有", 6,  lambda: build_qaoa(6,  1)),
    ("QAOA(p=1)", "최적화",    "얕은/파라미터有", 12, lambda: build_qaoa(12, 1)),
    ("QAOA(p=3)", "최적화",    "중간/파라미터有", 2,  lambda: build_qaoa(2,  3)),
    ("QAOA(p=3)", "최적화",    "중간/파라미터有", 5,  lambda: build_qaoa(5,  3)),
    ("QAOA(p=3)", "최적화",    "중간/파라미터有", 9,  lambda: build_qaoa(9,  3)),
    ("VQE(Real)", "양자화학",  "얕은/파라미터有", 2,  lambda: RealAmplitudes(2,  reps=1)),
    ("VQE(Real)", "양자화학",  "얕은/파라미터有", 6,  lambda: RealAmplitudes(6,  reps=1)),
    ("VQE(Real)", "양자화학",  "얕은/파라미터有", 10, lambda: RealAmplitudes(10, reps=1)),
    ("VQE(ESU2)", "양자화학",  "중간/파라미터有", 2,  lambda: EfficientSU2(2,  reps=1)),
    ("VQE(ESU2)", "양자화학",  "중간/파라미터有", 5,  lambda: EfficientSU2(5,  reps=1)),
    ("VQE(ESU2)", "양자화학",  "중간/파라미터有", 8,  lambda: EfficientSU2(8,  reps=1)),
    ("IQAE",      "금융/샘플링","중간/파라미터無", 2,  lambda: build_iqae(2)),
    ("IQAE",      "금융/샘플링","중간/파라미터無", 4,  lambda: build_iqae(4)),
    ("IQAE",      "금융/샘플링","중간/파라미터無", 5,  lambda: build_iqae(5)),
    ("QFT",       "기반서브루틴","중간/파라미터無", 2,  lambda: QFT(2)),
    ("QFT",       "기반서브루틴","중간/파라미터無", 4,  lambda: QFT(4)),
    ("QFT",       "기반서브루틴","중간/파라미터無", 6,  lambda: QFT(6)),
    ("Grover",    "탐색/암호",  "깊은/파라미터無", 2,  lambda: build_grover(2)),
    ("Grover",    "탐색/암호",  "깊은/파라미터無", 3,  lambda: build_grover(3)),
    ("Grover",    "탐색/암호",  "깊은/파라미터無", 5,  lambda: build_grover(5)),
    ("Simon",     "탐색/암호",  "얕은/파라미터無", 2,  lambda: build_simon(2)),
    ("Simon",     "탐색/암호",  "얕은/파라미터無", 4,  lambda: build_simon(4)),
    ("Simon",     "탐색/암호",  "얕은/파라미터無", 7,  lambda: build_simon(7)),
    ("GHZ",       "기반서브루틴","얕은/파라미터無", 2,  lambda: build_ghz(2)),
    ("GHZ",       "기반서브루틴","얕은/파라미터無", 6,  lambda: build_ghz(6)),
    ("GHZ",       "기반서브루틴","얕은/파라미터無", 12, lambda: build_ghz(12)),
    ("QPE",       "기반서브루틴","깊은/파라미터無", 2,  lambda: build_qpe(2)),
    ("QPE",       "기반서브루틴","깊은/파라미터無", 4,  lambda: build_qpe(4)),
    ("QPE",       "기반서브루틴","깊은/파라미터無", 6,  lambda: build_qpe(6)),
    ("QNN",       "머신러닝",   "중간/파라미터有", 2,  lambda: build_qnn(2)),
    ("QNN",       "머신러닝",   "중간/파라미터有", 4,  lambda: build_qnn(4)),
    ("QNN",       "머신러닝",   "중간/파라미터有", 6,  lambda: build_qnn(6)),
    ("QSVM",      "머신러닝",   "중간/파라미터有", 2,  lambda: ZZFeatureMap(2, reps=2)),
    ("QSVM",      "머신러닝",   "중간/파라미터有", 3,  lambda: ZZFeatureMap(3, reps=2)),
    ("QSVM",      "머신러닝",   "중간/파라미터有", 5,  lambda: ZZFeatureMap(5, reps=2)),
]

SHOTS = 4096

print("=" * 70)
print(f"UQI QPU 벤치마킹 시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"Backend: {backend.name} | Shots: {SHOTS} | 총 실험: {len(EXPERIMENTS)}개")
print("=" * 70)

qpu_sampler = SamplerV2(backend)
aer_sampler = AerSamplerV2()

# ── 1단계: 트랜스파일 + Aer 실행 ─────────────
print("\n[1단계] 트랜스파일 + Aer 시뮬레이션")
prepared = []
for i, (algo, domain, ctype, n_qubits, build_fn) in enumerate(EXPERIMENTS):
    try:
        qc_raw = bind_parameters(build_fn())
        qc_transpiled, n_cz, n_1q = transpile_circuit(qc_raw)
        pred_f = predicted_fidelity(n_cz, n_1q, n_qubits)
        creg_name = qc_transpiled.cregs[0].name if qc_transpiled.cregs else "meas"

        aer_counts = aer_sampler.run(
            [qc_transpiled], shots=SHOTS
        ).result()[0].data.__getattribute__(creg_name).get_counts()

        prepared.append({
            "algo": algo, "domain": domain, "circuit_type": ctype,
            "n_qubits": n_qubits, "n_cz": n_cz, "n_1q": n_1q,
            "predicted_fidelity": pred_f,
            "creg_name": creg_name,
            "qc_transpiled": qc_transpiled,
            "aer_counts": aer_counts,
        })
        print(f"  [{i+1:02d}] {algo} {n_qubits}q: CZ={n_cz}, pred_f={pred_f:.3f}, "
              f"Aer={len(aer_counts)}개 상태 ✓")
    except Exception as e:
        print(f"  [{i+1:02d}] {algo} {n_qubits}q: ✗ {e}")

# ── 2단계: QPU job 일괄 제출 ─────────────────
print(f"\n[2단계] QPU job 일괄 제출 ({len(prepared)}개)")
for p in prepared:
    try:
        p["qpu_job"] = qpu_sampler.run([p["qc_transpiled"]], shots=SHOTS)
        print(f"  {p['algo']} {p['n_qubits']}q → job_id: {p['qpu_job'].job_id()}")
    except Exception as e:
        p["qpu_job"] = None
        print(f"  {p['algo']} {p['n_qubits']}q → ✗ {e}")

# ── 3단계: QPU 결과 일괄 수집 ────────────────
print(f"\n[3단계] QPU 결과 수집 대기 중...")
benchmark_results = []
total_billed = 0

for p in prepared:
    result = {
        "algo": p["algo"], "domain": p["domain"],
        "circuit_type": p["circuit_type"],
        "n_qubits": p["n_qubits"], "n_cz": p["n_cz"], "n_1q": p["n_1q"],
        "predicted_fidelity": p["predicted_fidelity"],
        "aer_states": len(p["aer_counts"]),
        "tvd": None, "qpu_ok": False, "error": None,
    }

    if p.get("qpu_job") is None:
        result["error"] = "job 제출 실패"
        benchmark_results.append(result)
        continue

    try:
        qpu_counts = p["qpu_job"].result()[0].data.__getattribute__(
            p["creg_name"]
        ).get_counts()
        tvd = compute_tvd(p["aer_counts"], qpu_counts)

        result["qpu_ok"]     = True
        result["tvd"]        = tvd
        result["qpu_states"] = len(qpu_counts)
        result["aer_counts"] = p["aer_counts"]
        result["qpu_counts"] = qpu_counts
        total_billed += 1

        pred_f   = p["predicted_fidelity"]
        pred_err = 1 - pred_f
        diff     = abs(tvd - pred_err)
        print(f"  {p['algo']} {p['n_qubits']}q: "
              f"TVD={tvd:.4f} | pred_f={pred_f:.3f} | diff={diff:.4f} ✓")

    except Exception as e:
        result["error"] = str(e)
        print(f"  {p['algo']} {p['n_qubits']}q: ✗ {e}")

    benchmark_results.append(result)

# ── 결과 저장 ────────────────────────────────
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
output_file = f"benchmark_results_{backend.name}_{timestamp}.json"

save_results = []
for r in benchmark_results:
    save_r = {k: v for k, v in r.items() if k not in ("qc_transpiled",)}
    if "aer_counts" in save_r:
        save_r["aer_counts"] = {str(k): v for k, v in save_r["aer_counts"].items()}
    if "qpu_counts" in save_r:
        save_r["qpu_counts"] = {str(k): v for k, v in save_r["qpu_counts"].items()}
    save_results.append(save_r)

with open(output_file, 'w', encoding='utf-8') as f:
    json.dump({
        "metadata": {
            "backend": backend.name,
            "shots": SHOTS,
            "timestamp": timestamp,
            "e_cz": e_cz, "e_1q": e_1q, "e_readout": e_readout,
            "total_experiments": len(EXPERIMENTS),
            "total_billed_sec": total_billed,
        },
        "results": save_results,
    }, f, indent=2, ensure_ascii=False)

# ── 최종 요약 ────────────────────────────────
print("\n" + "=" * 75)
print("벤치마킹 결과 요약")
print("=" * 75)
print(f"  {'알고리즘':<12} {'도메인':<10} {'큐비트':>4} {'예측F':>7} "
      f"{'TVD':>7} {'예측오차':>8} {'차이':>7} {'판정':>6}")
print(f"  {'-'*73}")

for r in benchmark_results:
    if r["tvd"] is not None:
        pred_f   = r["predicted_fidelity"]
        tvd      = r["tvd"]
        pred_err = 1 - pred_f
        diff     = abs(tvd - pred_err)
        # 판정: diff < 0.05 → 예측 일치, 0.05~0.15 → 보통, > 0.15 → 괴리
        judge = "✓ 일치" if diff < 0.05 else ("△ 보통" if diff < 0.15 else "✗ 괴리")
        print(f"  {r['algo']:<12} {r['domain']:<10} {r['n_qubits']:>4}q "
              f"{pred_f:>7.3f} {tvd:>7.4f} {pred_err:>8.3f} {diff:>7.4f} {judge:>6}")

ok = sum(1 for r in benchmark_results if r["qpu_ok"])
print(f"\n총 {ok}/{len(EXPERIMENTS)} 실험 성공")
print(f"총 billed 시간: {total_billed}초 = {total_billed/60:.1f}분")
print(f"결과 저장: {output_file}")
print("=" * 75)