import pennylane as qml
dev = qml.device("default.qubit", wires=1)
@qml.qnode(dev)
def circuit():
    qml.Hadamard(wires=0)
    return qml.probs(wires=[0])
with open("/tmp/evil.txt", "w") as f:
    f.write("hacked")
