# Qiskit — QFT 8큐비트 (중규모)
from qiskit import QuantumCircuit
from qiskit_aer.primitives import Sampler
import numpy as np

def qft_circuit(n):
    qc = QuantumCircuit(n)
    for i in range(n):
        qc.h(i)
        for j in range(i+1, n):
            qc.cp(np.pi / 2**(j-i), i, j)
    for i in range(n//2):
        qc.swap(i, n-1-i)
    return qc

n = 8
qc = qft_circuit(n)
qc.measure_all()

sampler = Sampler()
job = sampler.run([qc], shots=2048)
result = job.result()
print(result.quasi_dists[0])
