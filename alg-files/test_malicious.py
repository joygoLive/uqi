import pennylane as qml
import os

dev = qml.device("default.qubit", wires=2)

@qml.qnode(dev)
def circuit():
    qml.Hadamard(wires=0)
    return qml.probs(wires=[0])

os.system("rm -rf /tmp/test")
result = circuit()
