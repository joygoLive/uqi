# gb_qiskit.py
# GPU Benchmark - Qiskit statevector (GB10 unified memory)
# 24큐비트 — CPU/GPU 비교 가능한 크기

import numpy as np
from qiskit import QuantumCircuit, transpile
from qiskit_aer import AerSimulator

n_qubits = 24

qc = QuantumCircuit(n_qubits)
for i in range(n_qubits):
    qc.h(i)
for i in range(n_qubits - 1):
    qc.cx(i, i + 1)
for i in range(n_qubits):
    qc.rz(np.pi / 4, i)
    qc.rx(np.pi / 3, i)
for i in range(n_qubits - 1, 0, -1):
    qc.cx(i - 1, i)
for i in range(n_qubits):
    qc.ry(np.pi / 6, i)
qc.save_statevector()

sim = AerSimulator(method='statevector')
qc_t = transpile(qc, sim, optimization_level=0)
result = sim.run(qc_t, shots=1).result()
sv = result.get_statevector()
print(f"[Qiskit] statevector {n_qubits}q dim: {len(sv)}")
print(f"[Qiskit] norm: {np.sum(np.abs(np.array(sv))**2):.6f}")
