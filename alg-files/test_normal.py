import pennylane as qml
import numpy as np

dev = qml.device("default.qubit", wires=2)

@qml.qnode(dev)
def circuit(theta):
    qml.RX(theta, wires=0)
    qml.CNOT(wires=[0, 1])
    return qml.probs(wires=[0, 1])

result = circuit(0.5)
