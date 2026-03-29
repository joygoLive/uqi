import pennylane as qml
import threading
dev = qml.device("default.qubit", wires=1)
@qml.qnode(dev)
def circuit():
    qml.Hadamard(wires=0)
    return qml.probs(wires=[0])
t = threading.Thread(target=circuit)
t.start()
