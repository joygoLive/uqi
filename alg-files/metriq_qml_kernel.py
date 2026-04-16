"""
Metriq Benchmark: QML Kernel
ZZ feature map 기반 양자 커널을 구성하고
all-zero 상태 복귀 확률로 커널 품질을 측정하는 벤치마크.
"""
import numpy as np
from qiskit import QuantumCircuit, transpile
from qiskit_aer import AerSimulator

n_qubits = 5
n_reps = 2  # feature map 반복 횟수
shots = 4096

rng = np.random.default_rng(42)

# --- ZZ Feature Map 구성 ---
def build_zz_feature_map(n, reps, x):
    """ZZFeatureMap: H → P(2*x_i) → ZZ entanglement"""
    qc = QuantumCircuit(n)
    for r in range(reps):
        # Hadamard 레이어
        for q in range(n):
            qc.h(q)
        # 단일 큐빗 위상
        for q in range(n):
            qc.p(2 * x[q], q)
        # ZZ entanglement
        for i in range(n):
            for j in range(i + 1, n):
                qc.cx(i, j)
                qc.p(2 * (np.pi - x[i]) * (np.pi - x[j]), j)
                qc.cx(i, j)
    return qc

# --- 커널 회로: phi(x)† · phi(x') ---
# 동일 데이터 → all-zero 복귀 확률 = 1 (이상적)
# 다른 데이터 → 복귀 확률 < 1 → 커널 값
x1 = rng.uniform(0, 2 * np.pi, n_qubits)
x2 = x1 + rng.normal(0, 0.3, n_qubits)  # 약간 다른 데이터

# Self-kernel (x1, x1) → 이상적으로 1.0
fm_x1 = build_zz_feature_map(n_qubits, n_reps, x1)
qc_self = QuantumCircuit(n_qubits, n_qubits)
qc_self.compose(fm_x1, inplace=True)
qc_self.compose(fm_x1.inverse(), inplace=True)
qc_self.measure(range(n_qubits), range(n_qubits))

# Cross-kernel (x1, x2)
fm_x2 = build_zz_feature_map(n_qubits, n_reps, x2)
qc_cross = QuantumCircuit(n_qubits, n_qubits)
qc_cross.compose(fm_x1, inplace=True)
qc_cross.compose(fm_x2.inverse(), inplace=True)
qc_cross.measure(range(n_qubits), range(n_qubits))

# --- 실행 ---
sim = AerSimulator(method='statevector')

qc_self_t = transpile(qc_self, sim, optimization_level=1)
result_self = sim.run(qc_self_t, shots=shots).result()
counts_self = result_self.get_counts()

qc_cross_t = transpile(qc_cross, sim, optimization_level=1)
result_cross = sim.run(qc_cross_t, shots=shots).result()
counts_cross = result_cross.get_counts()

zero_state = '0' * n_qubits
self_kernel = counts_self.get(zero_state, 0) / sum(counts_self.values())
cross_kernel = counts_cross.get(zero_state, 0) / sum(counts_cross.values())

print(f"[QML Kernel] n_qubits={n_qubits}, reps={n_reps}")
print(f"[QML Kernel] Self-kernel k(x,x): {self_kernel:.4f} (ideal=1.0)")
print(f"[QML Kernel] Cross-kernel k(x,x'): {cross_kernel:.4f}")
print(f"[QML Kernel] Kernel contrast: {self_kernel - cross_kernel:.4f}")
print(f"[QML Kernel] Self circuit depth: {qc_self_t.depth()}, Gates: {sum(qc_self_t.count_ops().values())}")
print(f"[QML Kernel] Cross circuit depth: {qc_cross_t.depth()}, Gates: {sum(qc_cross_t.count_ops().values())}")
