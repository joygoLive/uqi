import numpy as np
from qiskit import QuantumCircuit
from qiskit_aer.primitives import SamplerV2

# Quantum Value at Risk (VaR)
# Normal distribution encoded via RY rotations, 8 price qubits + 1 objective qubit
n_qubits = 8
n_total  = n_qubits + 1
mu, sigma = 0.0, 1.0
bounds = (-3.0, 3.0)

# Discretize normal distribution
x_vals = np.linspace(bounds[0], bounds[1], 2 ** n_qubits)
probs  = np.exp(-0.5 * ((x_vals - mu) / sigma) ** 2)
probs /= probs.sum()
amplitudes = np.sqrt(probs)

# Compute RY angles for amplitude encoding via QROM-style decomposition
# Simplified: encode marginal conditional angles
def compute_ry_angles(amplitudes):
    n = int(np.log2(len(amplitudes)))
    angles = []
    for k in range(n):
        step = 2 ** (n - k)
        half = step // 2
        layer = []
        for i in range(0, len(amplitudes), step):
            block = amplitudes[i:i+step]
            left  = np.linalg.norm(block[:half])
            right = np.linalg.norm(block[half:])
            norm  = np.linalg.norm(block)
            theta = 2 * np.arcsin(right / norm) if norm > 1e-10 else 0.0
            layer.append(theta)
        angles.append(layer)
    return angles

angles = compute_ry_angles(amplitudes)

# Payoff: loss = max(x - K, 0), K=0.5 (normalized)
K_norm = 0.5
payoff_vals = np.maximum(x_vals - K_norm, 0.0)
payoff_norm = payoff_vals / (payoff_vals.max() + 1e-9)
payoff_angles = 2 * np.arcsin(np.sqrt(payoff_norm))

qc = QuantumCircuit(n_total, n_total)

# State preparation: H + RY tree on price register
qc.h(0)
qc.ry(angles[0][0], 0)
for k in range(1, n_qubits):
    qc.h(k)
    for j, theta in enumerate(angles[k]):
        qc.cry(theta, k-1, k)

# Payoff oracle: CRY from MSB onto objective qubit
obj = n_qubits
for i in range(n_qubits):
    qc.cry(payoff_angles[i * (2**(n_qubits - 1 - i) if i < n_qubits else 1) % len(payoff_angles)], i, obj)

qc.measure(list(range(n_total)), list(range(n_total)))

sampler = SamplerV2()
job = sampler.run([qc], shots=1024)
result = job.result()
