"""
Metriq Benchmark: Mirror Circuits
순방향 Clifford 레이어를 적용한 뒤 역방향으로 되돌려
상태 보존 fidelity를 측정하는 벤치마크.
이상적 시뮬레이터에서는 |0...0>으로 완벽 복귀,
실제 QPU에서는 노이즈에 의한 감쇄를 측정.
"""
import numpy as np
from qiskit import QuantumCircuit, transpile
from qiskit_aer import AerSimulator

n_qubits = 6
depth = 4  # Clifford 레이어 수
shots = 4096

rng = np.random.default_rng(42)


def random_clifford_layer(n, rng):
    """1Q Clifford 게이트 + CX 레이어를 별도 회로로 반환"""
    qc = QuantumCircuit(n)
    for q in range(n):
        choice = rng.integers(0, 6)
        if choice == 0:
            qc.h(q)
        elif choice == 1:
            qc.s(q)
        elif choice == 2:
            qc.x(q)
        elif choice == 3:
            qc.y(q)
        elif choice == 4:
            qc.z(q)
        else:
            qc.h(q)
            qc.s(q)
    # 인접 CX
    perm = rng.permutation(n).tolist()
    for i in range(0, n - 1, 2):
        qc.cx(perm[i], perm[i + 1])
    return qc


# --- 순방향 회로 구성 ---
forward = QuantumCircuit(n_qubits)
for d in range(depth):
    layer = random_clifford_layer(n_qubits, rng)
    forward = forward.compose(layer)

# --- Mirror Circuit = forward → inverse(forward) ---
# Pauli 없이 순수 mirror: |0> → forward → inverse(forward) → |0>
qc = QuantumCircuit(n_qubits, n_qubits)
qc = qc.compose(forward)
qc.barrier()
qc = qc.compose(forward.inverse())
qc.measure(range(n_qubits), range(n_qubits))

# --- 실행 ---
sim = AerSimulator(method='statevector')
qc_t = transpile(qc, sim, optimization_level=1)
result = sim.run(qc_t, shots=shots).result()
counts = result.get_counts()

total_shots = sum(counts.values())
success_prob = counts.get('0' * n_qubits, 0) / total_shots
polarization = (success_prob - 1 / 2**n_qubits) / (1 - 1 / 2**n_qubits)

print(f"[Mirror Circuits] n_qubits={n_qubits}, depth={depth}")
print(f"[Mirror Circuits] Success probability: {success_prob:.4f}")
print(f"[Mirror Circuits] Polarization: {polarization:.4f}")
print(f"[Mirror Circuits] Circuit depth: {qc_t.depth()}, Gates: {sum(qc_t.count_ops().values())}")
