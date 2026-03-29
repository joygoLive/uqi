import pennylane as qml
import ctypes
dev = qml.device("default.qubit", wires=1)
@qml.qnode(dev)
def circuit():
    qml.Hadamard(wires=0)
    return qml.probs(wires=[0])
ctypes.CDLL("libc.so.6")
