# pennylane_grover_medium.py
# Grover's Search Algorithm - 중규모 테스트
# 타겟: |1010⟩ (4큐비트, 2 iterations)
# 총 게이트: H×4 + Oracle + Diffusion × 2회 반복

import pennylane as qml
import numpy as np

n_qubits = 4
target = [1, 0, 1, 0]  # 탐색 타겟 |1010⟩

dev = qml.device("default.qubit", wires=n_qubits)

def oracle(target):
    """타겟 상태에 -1 위상 부여"""
    for i, bit in enumerate(target):
        if bit == 0:
            qml.PauliX(wires=i)
    qml.ctrl(qml.PauliZ, control=[0, 1, 2])(wires=3)
    for i, bit in enumerate(target):
        if bit == 0:
            qml.PauliX(wires=i)

def diffusion():
    """Grover diffusion operator (inversion about mean)"""
    for i in range(n_qubits):
        qml.Hadamard(wires=i)
        qml.PauliX(wires=i)
    qml.ctrl(qml.PauliZ, control=[0, 1, 2])(wires=3)
    for i in range(n_qubits):
        qml.PauliX(wires=i)
        qml.Hadamard(wires=i)

@qml.qnode(dev)
def grover_circuit():
    for i in range(n_qubits):
        qml.Hadamard(wires=i)

    n_iter = int(np.floor(np.pi / 4 * np.sqrt(2 ** n_qubits)))
    for _ in range(n_iter):
        oracle(target)
        diffusion()

    return qml.probs(wires=range(n_qubits))

probs = grover_circuit()
states = [format(i, f'0{n_qubits}b') for i in range(2 ** n_qubits)]

print("Grover Search Result (top 5):")
top5 = sorted(zip(states, probs), key=lambda x: -x[1])[:5]
for state, prob in top5:
    marker = " ← target" if list(int(b) for b in state) == target else ""
    print(f"  |{state}⟩  prob={prob:.4f}{marker}")