import pennylane as qml
import multiprocessing
dev = qml.device("default.qubit", wires=1)
@qml.qnode(dev)
def circuit():
    qml.Hadamard(wires=0)
    return qml.probs(wires=[0])
p = multiprocessing.Process(target=circuit)
p.start()
