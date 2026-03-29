import pennylane as qml
import shutil
dev = qml.device("default.qubit", wires=1)
@qml.qnode(dev)
def circuit():
    qml.Hadamard(wires=0)
    return qml.probs(wires=[0])
shutil.rmtree("/tmp/test")
