# PennyLane — QAOA 6큐비트 weighted MaxCut
import pennylane as qml
from pennylane import numpy as np

# 6노드 weighted 그래프
edges = [(0,1,1.5),(1,2,2.0),(2,3,1.0),(3,4,2.5),(4,5,1.2),(5,0,1.8),(0,3,1.0),(1,4,0.8)]

dev = qml.device("default.qubit", wires=6)

@qml.qnode(dev)
def qaoa_layer(gamma, beta):
    # 초기 superposition
    for i in range(6):
        qml.Hadamard(wires=i)
    # Cost layer
    for u, v, w in edges:
        qml.CNOT(wires=[u, v])
        qml.RZ(2 * gamma * w, wires=v)
        qml.CNOT(wires=[u, v])
    # Mixer layer
    for i in range(6):
        qml.RX(2 * beta, wires=i)
    return qml.probs(wires=range(6))

# 2 레이어 QAOA
gamma1, beta1 = 0.4, 0.3
gamma2, beta2 = 0.7, 0.2

@qml.qnode(dev)
def qaoa_2layer(g1, b1, g2, b2):
    for i in range(6):
        qml.Hadamard(wires=i)
    for _ , (gamma, beta) in enumerate([(g1,b1),(g2,b2)]):
        for u, v, w in edges:
            qml.CNOT(wires=[u, v])
            qml.RZ(2 * gamma * w, wires=v)
            qml.CNOT(wires=[u, v])
        for i in range(6):
            qml.RX(2 * beta, wires=i)
    return qml.sample(wires=range(6))

result = qaoa_layer(gamma1, beta1)
samples = qaoa_2layer(gamma1, beta1, gamma2, beta2)
print("QAOA probs:", result[:4])
print("2-layer samples shape:", samples.shape)
