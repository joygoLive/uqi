
# Qiskit — Grover 3큐비트
from qiskit import QuantumCircuit
from qiskit_aer.primitives import Sampler

def grover_oracle(qc, target='101'):
    for i, bit in enumerate(reversed(target)):
        if bit == '0':
            qc.x(i)
    qc.ccx(0, 1, 2)
    for i, bit in enumerate(reversed(target)):
        if bit == '0':
            qc.x(i)

def diffuser(qc, n):
    qc.h(range(n))
    qc.x(range(n))
    qc.h(n-1)
    qc.mcx(list(range(n-1)), n-1)
    qc.h(n-1)
    qc.x(range(n))
    qc.h(range(n))

n = 3
qc = QuantumCircuit(n)
qc.h(range(n))
grover_oracle(qc, '101')
diffuser(qc, n)
qc.measure_all()

sampler = Sampler()
job = sampler.run([qc], shots=1024)
result = job.result()
print(result.quasi_dists[0])
