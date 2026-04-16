"""
Metriq Benchmark: EPLG (Error Per Layered Gate)
큐빗 체인 길이별로 랜덤 Clifford 레이어를 적용하여
2-qubit 게이트의 레이어 fidelity를 측정하는 벤치마크.
"""
import numpy as np
from qiskit import QuantumCircuit, transpile
from qiskit_aer import AerSimulator
from qiskit.circuit.library import HGate, SGate, CXGate

chain_length = 10  # 체인 큐빗 수
n_layers = 8       # 랜덤 레이어 수
shots = 4096

rng = np.random.default_rng(42)

def build_eplg_circuit(n_qubits, n_layers, rng):
    """EPLG 회로: 체인 구조에서 랜덤 Clifford 레이어 반복"""
    qc = QuantumCircuit(n_qubits, n_qubits)

    for layer in range(n_layers):
        # 1Q 랜덤 Clifford
        for q in range(n_qubits):
            choice = rng.integers(0, 3)
            if choice == 0:
                qc.h(q)
            elif choice == 1:
                qc.s(q)
            else:
                qc.h(q)
                qc.s(q)

        # 2Q 체인 CX (홀/짝 레이어 교대)
        start = layer % 2
        for q in range(start, n_qubits - 1, 2):
            qc.cx(q, q + 1)

    qc.barrier()

    # 역방향으로 동일 회로 적용 (mirror)
    inverse = qc.inverse()
    qc_full = qc.compose(inverse)

    cr = qc_full
    meas = QuantumCircuit(n_qubits, n_qubits)
    meas.measure(range(n_qubits), range(n_qubits))
    qc_final = qc_full.compose(meas)

    return qc_final

# --- 회로 생성 & 실행 ---
qc = build_eplg_circuit(chain_length, n_layers, rng)

sim = AerSimulator(method='statevector')
qc_t = transpile(qc, sim, optimization_level=1)
result = sim.run(qc_t, shots=shots).result()
counts = result.get_counts()

total = sum(counts.values())
success_prob = counts.get('0' * chain_length, 0) / total

# EPLG = 1 - F^(1/n_layers) where F = success probability
if success_prob > 0:
    layer_fidelity = success_prob ** (1.0 / n_layers)
    eplg = 1.0 - layer_fidelity
else:
    layer_fidelity = 0.0
    eplg = 1.0

print(f"[EPLG] chain_length={chain_length}, n_layers={n_layers}")
print(f"[EPLG] Success probability: {success_prob:.4f}")
print(f"[EPLG] Layer fidelity: {layer_fidelity:.4f}")
print(f"[EPLG] EPLG score: {eplg:.6f}")
print(f"[EPLG] Circuit depth: {qc_t.depth()}, Gates: {sum(qc_t.count_ops().values())}")
