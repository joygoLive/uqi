"""
Metriq Benchmark: CLOPS (Circuit Layer Operations Per Second)
파라미터화된 Quantum Volume 회로의 실행 처리량을 측정하는 벤치마크.
컴파일, 파라미터 바인딩, 실행, 결과 수집까지의 전체 시간을 포함.
"""
import time
import numpy as np
from qiskit import QuantumCircuit, transpile
from qiskit_aer import AerSimulator
from qiskit.circuit import ParameterVector
from qiskit.circuit.library import QuantumVolume

n_qubits = 5
n_updates = 10   # 파라미터 업데이트 횟수
n_shots = 100     # 업데이트당 shot 수

# --- 파라미터화된 QV-style 회로 ---
# QV 회로를 직접 만들되 파라미터화
n_params = n_qubits * 3  # 각 큐빗에 3개 회전
params = ParameterVector('θ', n_params)

qc = QuantumCircuit(n_qubits, n_qubits)
# SU(4) 레이어 근사: 1Q rotation + CX 레이어
for q in range(n_qubits):
    qc.rz(params[q * 3], q)
    qc.ry(params[q * 3 + 1], q)
    qc.rz(params[q * 3 + 2], q)
for q in range(0, n_qubits - 1, 2):
    qc.cx(q, q + 1)
for q in range(1, n_qubits - 1, 2):
    qc.cx(q, q + 1)
# 두 번째 레이어
for q in range(n_qubits):
    qc.rz(params[q * 3], q)
    qc.ry(params[q * 3 + 1], q)
    qc.rz(params[q * 3 + 2], q)
for q in range(0, n_qubits - 1, 2):
    qc.cx(q, q + 1)
qc.measure(range(n_qubits), range(n_qubits))

sim = AerSimulator(method='statevector')

rng = np.random.default_rng(42)

# --- CLOPS 측정 ---
# Warm-up
warmup_params = rng.uniform(-np.pi, np.pi, n_params)
qc_bound = qc.assign_parameters(warmup_params)
qc_t = transpile(qc_bound, sim, optimization_level=1)
sim.run(qc_t, shots=n_shots).result()

# Steady-state 측정
times = []
for i in range(n_updates):
    param_vals = rng.uniform(-np.pi, np.pi, n_params)
    t0 = time.perf_counter()
    qc_bound = qc.assign_parameters(param_vals)
    qc_t = transpile(qc_bound, sim, optimization_level=1)
    result = sim.run(qc_t, shots=n_shots).result()
    _ = result.get_counts()
    t1 = time.perf_counter()
    times.append(t1 - t0)

total_time = sum(times)
# CLOPS = (M * K * S * D) / total_time
# M=1 (circuit), K=n_updates, S=n_shots, D=n_qubits (QV depth = width)
clops = (1 * n_updates * n_shots * n_qubits) / total_time
steady_state_times = times[2:]  # 처음 2개 제외
if steady_state_times:
    ss_total = sum(steady_state_times)
    clops_ss = (1 * len(steady_state_times) * n_shots * n_qubits) / ss_total
else:
    clops_ss = clops

print(f"[CLOPS] n_qubits={n_qubits}, updates={n_updates}, shots/update={n_shots}")
print(f"[CLOPS] Total time: {total_time:.4f}s")
print(f"[CLOPS] Avg time per update: {total_time / n_updates:.4f}s")
print(f"[CLOPS] CLOPS (overall): {clops:.0f}")
print(f"[CLOPS] CLOPS (steady-state): {clops_ss:.0f}")
print(f"[CLOPS] Circuit depth: {qc_t.depth()}, Gates: {sum(qc_t.count_ops().values())}")
