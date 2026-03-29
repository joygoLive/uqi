from qiskit import QuantumCircuit
from qiskit.circuit.library import QFT
import numpy as np

n = 10  # 10 qubits → QFT 기준 ~200+ gates

qc = QuantumCircuit(n)

# Layer 1: Hadamard + Rx rotations
for i in range(n):
    qc.h(i)
    qc.rx(np.pi / (i + 1), i)

# Layer 2: CNOT entanglement chain
for i in range(n - 1):
    qc.cx(i, i + 1)

# Layer 3: Ry + Rz rotations
for i in range(n):
    qc.ry(np.pi / (i + 2), i)
    qc.rz(np.pi / (i + 3), i)

# Layer 4: QFT (~n*(n+1)/2 gates for n=10 → 55 CX-equivalent)
qft = QFT(n, approximation_degree=0, do_swaps=True)
qc.compose(qft, inplace=True)

# Layer 5: Additional CX pairs
for i in range(0, n - 1, 2):
    qc.cx(i, i + 1)
    qc.cx(i + 1, i)

# Layer 6: Final Rz sweep
for i in range(n):
    qc.rz(np.pi * i / n, i)

qc.measure_all()
