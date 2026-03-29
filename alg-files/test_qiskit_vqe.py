from qiskit import QuantumCircuit
from qiskit.circuit import ParameterVector
from qiskit.primitives import StatevectorEstimator
from qiskit.quantum_info import SparsePauliOp
import numpy as np

n = 8
reps = 3

# Ansatz: RealAmplitudes 스타일
theta = ParameterVector('θ', n * reps * 2)
qc = QuantumCircuit(n)

idx = 0
for r in range(reps):
    for i in range(n):
        qc.ry(theta[idx], i)
        idx += 1
    for i in range(n - 1):
        qc.cx(i, i + 1)
    for i in range(n):
        qc.rz(theta[idx], i)
        idx += 1
    for i in range(0, n - 1, 2):
        qc.cx(i, i + 1)

qc.measure_all()

# Hamiltonian
pauli_terms = []
for i in range(n - 1):
    z_str = 'I' * (n - 2 - i) + 'ZZ' + 'I' * i
    pauli_terms.append((z_str, -1.0))
for i in range(n):
    x_str = 'I' * (n - 1 - i) + 'X' + 'I' * i
    pauli_terms.append((x_str, -0.5))
H = SparsePauliOp.from_list(pauli_terms)

# Estimator run
params = np.random.uniform(-np.pi, np.pi, len(theta))
estimator = StatevectorEstimator()
pub = (qc.remove_final_measurements(inplace=False), H, params)
result = estimator.run([pub]).result()
