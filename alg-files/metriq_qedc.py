"""
Metriq Benchmark: QED-C (Quantum Economic Development Consortium)
BV, QPE, Hidden Shift, QFT 4가지 응용 알고리즘의
회로별 fidelity를 측정하는 벤치마크.
"""
import numpy as np
from qiskit import QuantumCircuit, transpile
from qiskit_aer import AerSimulator
from qiskit.circuit.library import QFTGate

shots = 4096
sim = AerSimulator(method='statevector')

# ============================================================
# 1. Bernstein-Vazirani
# ============================================================
def build_bv(n, secret):
    """BV 회로: 비밀 문자열 s를 찾는 알고리즘"""
    qc = QuantumCircuit(n + 1, n)
    qc.x(n)
    qc.h(range(n + 1))
    for i in range(n):
        if (secret >> i) & 1:
            qc.cx(i, n)
    qc.h(range(n))
    qc.measure(range(n), range(n))
    return qc

bv_n = 6
bv_secret = 0b101011
qc_bv = build_bv(bv_n, bv_secret)
qc_bv_t = transpile(qc_bv, sim, optimization_level=1)
result_bv = sim.run(qc_bv_t, shots=shots).result()
counts_bv = result_bv.get_counts()
expected_bv = format(bv_secret, f'0{bv_n}b')
fidelity_bv = counts_bv.get(expected_bv, 0) / sum(counts_bv.values())

# ============================================================
# 2. Quantum Phase Estimation
# ============================================================
def build_qpe(n_count, phase):
    """QPE 회로: 위상 추정"""
    n_total = n_count + 1
    qc = QuantumCircuit(n_total, n_count)

    # 고유상태 준비
    qc.x(n_count)

    # Hadamard on counting qubits
    qc.h(range(n_count))

    # Controlled-U^(2^k) rotations
    for k in range(n_count):
        angle = 2 * np.pi * phase * (2 ** k)
        qc.cp(angle, k, n_count)

    # Inverse QFT
    qc.append(QFTGate(n_count).inverse(), range(n_count))
    qc.measure(range(n_count), range(n_count))
    return qc

qpe_n = 5
target_phase = 3 / 8  # = 0.375
qc_qpe = build_qpe(qpe_n, target_phase)
qc_qpe_t = transpile(qc_qpe, sim, optimization_level=1)
result_qpe = sim.run(qc_qpe_t, shots=shots).result()
counts_qpe = result_qpe.get_counts()
expected_qpe_val = int(target_phase * 2**qpe_n)
expected_qpe = format(expected_qpe_val, f'0{qpe_n}b')
fidelity_qpe = counts_qpe.get(expected_qpe, 0) / sum(counts_qpe.values())

# ============================================================
# 3. Hidden Shift
# ============================================================
def build_hidden_shift(n, shift):
    """Hidden Shift: Simon's algorithm variant
    Oracle: f(x) = x ⊕ s (bitwise XOR with hidden shift)
    H^n → Oracle → H^n 패턴으로 s 복원"""
    qc = QuantumCircuit(n, n)
    # Hadamard
    qc.h(range(n))
    # Oracle: shift에 해당하는 비트에 Z 적용
    # H·Z·H = X이므로, H → Z → H 패턴은 X와 동일
    # 직접 phase kickback: Z_i for each bit i where s_i = 1
    for i in range(n):
        if (shift >> i) & 1:
            qc.z(i)
    # Hadamard
    qc.h(range(n))
    qc.measure(range(n), range(n))
    return qc

hs_n = 6
hs_shift = 0b101010
qc_hs = build_hidden_shift(hs_n, hs_shift)
qc_hs_t = transpile(qc_hs, sim, optimization_level=1)
result_hs = sim.run(qc_hs_t, shots=shots).result()
counts_hs = result_hs.get_counts()
expected_hs = format(hs_shift, f'0{hs_n}b')
fidelity_hs = counts_hs.get(expected_hs, 0) / sum(counts_hs.values())

# ============================================================
# 4. Quantum Fourier Transform
# ============================================================
def build_qft_test(n, input_state):
    """QFT 회로: 특정 입력에 대한 QFT → inverse QFT 왕복"""
    qc = QuantumCircuit(n, n)
    # 입력 상태 준비
    for i in range(n):
        if (input_state >> i) & 1:
            qc.x(i)
    # QFT → inverse QFT (왕복 fidelity)
    qc.append(QFTGate(n), range(n))
    qc.append(QFTGate(n).inverse(), range(n))
    qc.measure(range(n), range(n))
    return qc

qft_n = 6
qft_input = 0b101010
qc_qft = build_qft_test(qft_n, qft_input)
qc_qft_t = transpile(qc_qft, sim, optimization_level=1)
result_qft = sim.run(qc_qft_t, shots=shots).result()
counts_qft = result_qft.get_counts()
expected_qft = format(qft_input, f'0{qft_n}b')
fidelity_qft = counts_qft.get(expected_qft, 0) / sum(counts_qft.values())

# --- 종합 결과 ---
print(f"[QED-C] Bernstein-Vazirani (n={bv_n}): fidelity={fidelity_bv:.4f}, depth={qc_bv_t.depth()}")
print(f"[QED-C] Phase Estimation (n={qpe_n}): fidelity={fidelity_qpe:.4f}, depth={qc_qpe_t.depth()}")
print(f"[QED-C] Hidden Shift (n={hs_n}): fidelity={fidelity_hs:.4f}, depth={qc_hs_t.depth()}")
print(f"[QED-C] QFT Round-trip (n={qft_n}): fidelity={fidelity_qft:.4f}, depth={qc_qft_t.depth()}")
avg_fidelity = (fidelity_bv + fidelity_qpe + fidelity_hs + fidelity_qft) / 4
print(f"[QED-C] Average fidelity: {avg_fidelity:.4f}")
