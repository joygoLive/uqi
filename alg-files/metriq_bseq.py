"""
Metriq Benchmark: BSEQ (Bell State Effective Qubits)
인접 큐빗 쌍에서 Bell state를 생성하고 CHSH 부등식 위반을 측정하여
실효 entanglement 큐빗 수를 평가하는 벤치마크.
"""
import numpy as np
from qiskit import QuantumCircuit, transpile
from qiskit_aer import AerSimulator

n_qubits = 10  # 짝수
shots = 8192
chsh_classical_bound = 2.0

# 최적 CHSH 측정 각도 (measurement angles in XZ plane)
# Alice: a0=0, a1=pi/4
# Bob:   b0=pi/8, b1=-pi/8
ALICE_ANGLES = [0, np.pi / 4]
BOB_ANGLES = [np.pi / 8, -np.pi / 8]


def build_chsh_circuit(q0, q1, alice_angle, bob_angle, n_total):
    """CHSH 측정 회로
    Ry(-2*theta)를 적용하여 theta 방향 측정을 Z basis 측정으로 변환
    """
    qc = QuantumCircuit(n_total, 2)

    # |Φ+> = (|00> + |11>) / sqrt(2)
    qc.h(q0)
    qc.cx(q0, q1)

    # 측정 basis 회전: Ry(-2θ) 적용
    if alice_angle != 0:
        qc.ry(-2 * alice_angle, q0)
    if bob_angle != 0:
        qc.ry(-2 * bob_angle, q1)

    qc.measure(q0, 0)
    qc.measure(q1, 1)
    return qc


def compute_correlator(counts):
    """E(a,b) = P(same) - P(different)"""
    total = sum(counts.values())
    same = counts.get('00', 0) + counts.get('11', 0)
    diff = counts.get('01', 0) + counts.get('10', 0)
    return (same - diff) / total


# --- 각 인접 큐빗 쌍에 대해 CHSH 테스트 ---
sim = AerSimulator(method='statevector')
pairs = [(i, i + 1) for i in range(0, n_qubits - 1, 2)]
chsh_values = []
violating_pairs = []

for q0, q1 in pairs:
    correlators = {}
    for ai, a_angle in enumerate(ALICE_ANGLES):
        for bi, b_angle in enumerate(BOB_ANGLES):
            circ = build_chsh_circuit(q0, q1, a_angle, b_angle, n_qubits)
            circ_t = transpile(circ, sim, optimization_level=1)
            result = sim.run(circ_t, shots=shots).result()
            correlators[(ai, bi)] = compute_correlator(result.get_counts())

    # S = E(a0,b0) + E(a0,b1) + E(a1,b0) - E(a1,b1)
    S = (correlators[(0, 0)] + correlators[(0, 1)]
         + correlators[(1, 0)] - correlators[(1, 1)])
    chsh_values.append(abs(S))

    if abs(S) > chsh_classical_bound:
        violating_pairs.append((q0, q1))

# --- 결과 ---
n_violating = len(violating_pairs)
fraction_connected = n_violating / len(pairs) if pairs else 0

print(f"[BSEQ] n_qubits={n_qubits}, pairs tested={len(pairs)}")
for i, ((q0, q1), s) in enumerate(zip(pairs, chsh_values)):
    status = "VIOLATES" if s > chsh_classical_bound else "classical"
    print(f"  Pair ({q0},{q1}): |S| = {s:.4f} [{status}]")
print(f"[BSEQ] Violating pairs: {n_violating}/{len(pairs)}")
print(f"[BSEQ] Fraction connected: {fraction_connected:.4f}")
print(f"[BSEQ] Effective entangled qubits: {n_violating * 2}")
