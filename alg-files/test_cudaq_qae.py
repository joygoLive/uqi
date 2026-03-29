import cudaq
import numpy as np

# European Call Option 파라미터
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
mu = (r - 0.5 * sigma**2) * T
std = sigma * np.sqrt(T)
probs = np.exp(-0.5 * ((log_x - mu) / std) ** 2)
probs /= probs.sum()
amplitudes = np.sqrt(probs).tolist()
payoff_angles = (2 * np.arcsin(np.sqrt(payoffs_norm))).tolist()

s0, s1, s2, s3, s4 = amplitudes[0], amplitudes[1], amplitudes[2], amplitudes[3], amplitudes[4]
p0, p1, p2, p3, p4 = payoff_angles[0], payoff_angles[1], payoff_angles[2], payoff_angles[3], payoff_angles[4]
a01 = -np.pi / 2.0
a02 = -np.pi / 4.0
a03 = -np.pi / 8.0
a12 = -np.pi / 2.0
a13 = -np.pi / 4.0
a23 = -np.pi / 2.0

@cudaq.kernel
def qae_circuit(
    s0: float, s1: float, s2: float, s3: float, s4: float,
    p0: float, p1: float, p2: float, p3: float, p4: float,
    a01: float, a02: float, a03: float,
    a12: float, a13: float, a23: float
):
    qubits  = cudaq.qvector(5)
    ancilla = cudaq.qvector(1)
    ae_reg  = cudaq.qvector(4)

    # AE 레지스터 초기화
    h(ae_reg[0])
    h(ae_reg[1])
    h(ae_reg[2])
    h(ae_reg[3])

    # State prep (인라인)
    h(qubits[0])
    h(qubits[1])
    h(qubits[2])
    h(qubits[3])
    h(qubits[4])
    ry(s0, qubits[0])
    ry(s1, qubits[1])
    ry(s2, qubits[2])
    ry(s3, qubits[3])
    ry(s4, qubits[4])

    # Payoff oracle (인라인)
    ry.ctrl(p0, qubits[0], ancilla[0])
    ry.ctrl(p1, qubits[1], ancilla[0])
    ry.ctrl(p2, qubits[2], ancilla[0])
    ry.ctrl(p3, qubits[3], ancilla[0])
    ry.ctrl(p4, qubits[4], ancilla[0])

    # Inverse QFT (완전 언롤)
    h(ae_reg[0])
    r1(a01, ae_reg[1])
    r1(a02, ae_reg[2])
    r1(a03, ae_reg[3])

    h(ae_reg[1])
    r1(a12, ae_reg[2])
    r1(a13, ae_reg[3])

    h(ae_reg[2])
    r1(a23, ae_reg[3])

    h(ae_reg[3])

    mz(ae_reg)

cudaq.sample(
    qae_circuit,
    s0, s1, s2, s3, s4,
    p0, p1, p2, p3, p4,
    a01, a02, a03, a12, a13, a23,
    shots_count=1024
)
