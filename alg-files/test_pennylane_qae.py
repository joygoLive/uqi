import pennylane as qml
import numpy as np

# European Call Option parameters
S0 = 100.0
K  = 105.0
r  = 0.05
T  = 1.0
sigma = 0.2

N = 2 ** 5
dx = 4 * sigma * np.sqrt(T) / N
x_vals = S0 * np.exp((r - 0.5 * sigma**2) * T + (np.arange(N) - N//2) * dx)
payoffs = np.maximum(x_vals - K, 0.0)
payoffs_norm = payoffs / (payoffs.max() + 1e-9)

log_x = np.log(x_vals / S0)
mu  = (r - 0.5 * sigma**2) * T
std = sigma * np.sqrt(T)
probs = np.exp(-0.5 * ((log_x - mu) / std) ** 2)
probs /= probs.sum()
amplitudes    = np.sqrt(probs[:5]).tolist()
payoff_angles = (2 * np.arcsin(np.sqrt(payoffs_norm[:5]))).tolist()

a01 = -np.pi / 2.0
a02 = -np.pi / 4.0
a03 = -np.pi / 8.0
a12 = -np.pi / 2.0
a13 = -np.pi / 4.0
a23 = -np.pi / 2.0

n_total = 10
dev = qml.device("default.qubit", wires=n_total, shots=1024)

@qml.qnode(dev)
def qae_circuit():
    price_wires  = list(range(0, 5))
    ancilla_wire = 5
    ae_wires     = list(range(6, 10))

    # AE register: Hadamard
    for w in ae_wires:
        qml.Hadamard(wires=w)

    # State prep: Hadamard + RY on price register
    for i, w in enumerate(price_wires):
        qml.Hadamard(wires=w)
        qml.RY(amplitudes[i], wires=w)

    # Payoff oracle: controlled-RY onto ancilla
    for i, w in enumerate(price_wires):
        qml.CRY(payoff_angles[i], wires=[w, ancilla_wire])

    # Inverse QFT on AE register (unrolled)
    qml.Hadamard(wires=ae_wires[0])
    qml.ControlledPhaseShift(a01, wires=[ae_wires[0], ae_wires[1]])
    qml.ControlledPhaseShift(a02, wires=[ae_wires[0], ae_wires[2]])
    qml.ControlledPhaseShift(a03, wires=[ae_wires[0], ae_wires[3]])

    qml.Hadamard(wires=ae_wires[1])
    qml.ControlledPhaseShift(a12, wires=[ae_wires[1], ae_wires[2]])
    qml.ControlledPhaseShift(a13, wires=[ae_wires[1], ae_wires[3]])

    qml.Hadamard(wires=ae_wires[2])
    qml.ControlledPhaseShift(a23, wires=[ae_wires[2], ae_wires[3]])

    qml.Hadamard(wires=ae_wires[3])

    return qml.sample(wires=ae_wires)

result = qae_circuit()
