
# PennyLane — VQE (Hydrogen 분자 근사)
import pennylane as qml
from pennylane import numpy as np

dev = qml.device("default.qubit", wires=4)

@qml.qnode(dev)
def vqe_circuit(params):
    # UCCSD-style ansatz
    qml.BasisState(np.array([1, 1, 0, 0]), wires=range(4))
    qml.DoubleExcitation(params[0], wires=[0, 1, 2, 3])
    qml.SingleExcitation(params[1], wires=[0, 2])
    qml.SingleExcitation(params[2], wires=[1, 3])
    return qml.expval(qml.PauliZ(0) @ qml.PauliZ(1))

params = np.array([0.1, 0.2, 0.3])
result = vqe_circuit(params)
print("VQE energy:", result)

# 샘플링도 추가
dev2 = qml.device("default.qubit", wires=4)

@qml.qnode(dev2)
def vqe_sample(params):
    qml.BasisState(np.array([1, 1, 0, 0]), wires=range(4))
    qml.DoubleExcitation(params[0], wires=[0, 1, 2, 3])
    qml.SingleExcitation(params[1], wires=[0, 2])
    return qml.sample(wires=range(4))

samples = vqe_sample(params)
print("Samples shape:", samples.shape)
