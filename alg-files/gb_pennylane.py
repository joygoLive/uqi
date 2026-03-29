# gb_pennylane.py
# GPU Benchmark - PennyLane lightning.gpu (GPU 가속 확인용)
# 26큐비트 — 9.73x GPU 가속 확인된 설정

import pennylane as qml
import numpy as np

n_qubits = 26
dev = qml.device("default.qubit", wires=n_qubits)

@qml.qnode(dev)
def vqe_circuit(params):
    for i in range(n_qubits):
        qml.Hadamard(wires=i)
    for layer in range(4):
        for i in range(n_qubits):
            qml.RY(params[layer, i], wires=i)
            qml.RZ(params[layer, i] * 0.5, wires=i)
        for i in range(n_qubits - 1):
            qml.CNOT(wires=[i, i + 1])
        qml.CNOT(wires=[n_qubits - 1, 0])
    return qml.probs(wires=range(n_qubits))

params = np.random.uniform(0, 2 * np.pi, (4, n_qubits))
result = vqe_circuit(params)
print(f"[PennyLane] VQE {n_qubits}q probs sum: {result.sum():.6f}")
print(f"[PennyLane] Top state prob: {result.max():.8f}")
