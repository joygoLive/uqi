"""
Metriq Benchmark: WIT (Wormhole-Inspired Teleportation)
6~7큐빗 텔레포테이션 회로로 Pauli-Z 기대값을 측정하여
양자 텔레포테이션 fidelity를 평가하는 벤치마크.
"""
import numpy as np
from qiskit import QuantumCircuit, transpile
from qiskit_aer import AerSimulator

n_qubits = 7
shots = 8192

# --- WIT 회로 구성 ---
# 구조: Alice(0,1,2) - EPR pair(3,4) - Bob(4,5,6)
# 큐빗 0: 입력 상태, 큐빗 6: 출력 (텔레포트 결과)
qc = QuantumCircuit(n_qubits, n_qubits)

# 1. 입력 상태 준비 (|+> 상태)
qc.h(0)

# 2. 내부 entanglement (SYK-inspired scrambling)
# Alice side scrambling
qc.cx(0, 1)
qc.h(1)
qc.cx(1, 2)
qc.rz(np.pi / 4, 2)
qc.cx(0, 2)

qc.barrier()

# 3. EPR 채널 (wormhole analog)
qc.h(3)
qc.cx(3, 4)

qc.barrier()

# 4. Alice 측정 + 고전 통신 시뮬레이션
# coupling: Alice side → EPR
qc.cx(2, 3)
qc.h(2)

qc.barrier()

# 5. Bob side unscrambling (time-reversed)
qc.cx(4, 5)
qc.h(5)
qc.cx(5, 6)
qc.rz(-np.pi / 4, 6)
qc.cx(4, 6)

# 조건부 보정 (simplified)
qc.cx(2, 5)
qc.cz(3, 4)

qc.barrier()

# 6. 전체 측정
qc.measure(range(n_qubits), range(n_qubits))

# --- 실행 ---
sim = AerSimulator(method='statevector')
qc_t = transpile(qc, sim, optimization_level=1)
result = sim.run(qc_t, shots=shots).result()
counts = result.get_counts()

# Pauli-Z 기대값 (출력 큐빗 6)
total = sum(counts.values())
z_expect = 0
for bitstring, count in counts.items():
    # Qiskit은 little-endian: bitstring[0]이 마지막 큐빗(6)
    output_bit = int(bitstring[0])
    z_expect += (1 - 2 * output_bit) * count
z_expect /= total

# 불확실성 (binomial)
p_zero = (z_expect + 1) / 2
uncertainty = np.sqrt(p_zero * (1 - p_zero) / total)

print(f"[WIT] n_qubits={n_qubits}, shots={shots}")
print(f"[WIT] <Z> expectation (output qubit): {z_expect:.4f} ± {uncertainty:.4f}")
print(f"[WIT] Teleportation score: {(z_expect + 1) / 2:.4f}")
print(f"[WIT] Circuit depth: {qc_t.depth()}, Gates: {sum(qc_t.count_ops().values())}")
