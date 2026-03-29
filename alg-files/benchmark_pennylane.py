import pennylane as qml
import numpy as np

# Black-Scholes 파라미터
S0 = 100.0      # 현재 주가
K = 105.0       # 행사가
T = 1.0         # 만기 (년)
r = 0.05        # 무위험 이자율
sigma = 0.2     # 변동성

n_qubits = 20   # 분포 인코딩용
n_aux = 6       # 보조 큐비트
total_qubits = n_qubits + n_aux

dev = qml.device("default.qubit", wires=total_qubits)

def load_distribution(wires, mu, sigma_enc):
    """정규분포 근사 인코딩"""
    for i, w in enumerate(wires):
        angle = mu + sigma_enc * (2 * i / len(wires) - 1)
        qml.RY(2 * np.arcsin(np.clip(angle, -1, 1)), wires=w)

def payoff_rotation(ctrl_wires, target_wire, K_norm):
    """콜옵션 페이오프 회전"""
    for i, cw in enumerate(ctrl_wires):
        angle = np.pi / 2 ** (i + 1)
        qml.CRY(angle, wires=[cw, target_wire])

@qml.qnode(dev)
def qmc_call_option():
    dist_wires = list(range(n_qubits))
    aux_wires  = list(range(n_qubits, total_qubits))

    # 로그정규분포 근사 인코딩
    mu_enc    = np.log(S0) + (r - 0.5 * sigma**2) * T
    sig_enc   = sigma * np.sqrt(T)
    mu_norm   = (mu_enc - np.log(K)) / (3 * sig_enc)

    load_distribution(dist_wires, mu_norm, 0.3)

    # 보조 큐비트로 페이오프 추정
    for aw in aux_wires:
        qml.Hadamard(wires=aw)

    payoff_rotation(dist_wires[:len(aux_wires)], aux_wires[0], K)

    # Amplitude Estimation 근사 (반복 위상 킥백)
    for i in range(1, len(aux_wires)):
        qml.CRY(np.pi / 2**i, wires=[aux_wires[i], aux_wires[0]])

    qml.adjoint(qml.QFT)(wires=aux_wires)

    return qml.probs(wires=aux_wires)

probs = qmc_call_option()
# 기댓값 추정 → 콜옵션 가격
estimated_prob = sum(probs[len(probs)//2:])
call_price = np.exp(-r * T) * S0 * estimated_prob
print(f"[PennyLane] QMC Call Option Price: {call_price:.4f}")
print(f"[PennyLane] Estimated probability:  {estimated_prob:.4f}")
