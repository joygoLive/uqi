import numpy as np
from qiskit import QuantumCircuit, QuantumRegister, ClassicalRegister, transpile
from qiskit_aer import AerSimulator
from qiskit.circuit.library import QFT

# VaR 파라미터
confidence_level = 0.95
mu_loss    = 0.02   # 손실 기댓값
sigma_loss = 0.05   # 손실 표준편차
n_eval     = 5      # evaluation qubits (2^5=32 구간)
n_state    = 6      # state qubits

def build_loss_distribution(n: int, mu: float, sig: float) -> QuantumCircuit:
    """손실분포 로딩 회로"""
    qc = QuantumCircuit(n)
    for i in range(n):
        x = -1 + 2 * i / (n - 1) if n > 1 else 0
        prob = np.exp(-0.5 * ((x - mu / sig) ** 2))
        prob = np.clip(prob, 0, 1)
        qc.ry(2 * np.arcsin(np.sqrt(prob)), i)
    return qc

def build_var_oracle(n_state: int, threshold: float) -> QuantumCircuit:
    """VaR 임계값 초과 여부 오라클"""
    qc = QuantumCircuit(n_state + 1)
    threshold_idx = int(threshold * (2**n_state))
    for i in range(n_state):
        if (threshold_idx >> i) & 1 == 0:
            qc.x(i)
    qc.mcx(list(range(n_state)), n_state)
    for i in range(n_state):
        if (threshold_idx >> i) & 1 == 0:
            qc.x(i)
    return qc

# QAE 회로 구성
eval_reg  = QuantumRegister(n_eval,  name='eval')
state_reg = QuantumRegister(n_state, name='state')
anc_reg   = QuantumRegister(1,       name='anc')
cr        = ClassicalRegister(n_eval, name='c')

qc = QuantumCircuit(eval_reg, state_reg, anc_reg, cr)

for i in range(n_eval):
    qc.h(eval_reg[i])

loss_circ = build_loss_distribution(n_state, mu_loss, sigma_loss)
qc.append(loss_circ, state_reg)

var_oracle = build_var_oracle(n_state, confidence_level)
for j in range(n_eval):
    reps = 2 ** j
    for _ in range(reps):
        qc.append(var_oracle, list(state_reg) + [anc_reg[0]])

qc.append(QFT(n_eval, inverse=True), eval_reg)
qc.measure(eval_reg, cr)

sim = AerSimulator(method='statevector')
qc_t = transpile(qc, sim, optimization_level=1)
result = sim.run(qc_t, shots=2048).result()
counts = result.get_counts()

top_state = max(counts, key=counts.get)
phase = int(top_state, 2) / 2**n_eval
var_estimate = mu_loss + sigma_loss * np.sin(np.pi * phase) ** 2
print(f"[Qiskit] QAE-based VaR ({int(confidence_level*100)}%): {var_estimate:.4f}")
print(f"[Qiskit] Circuit depth: {qc_t.depth()}, Gates: {sum(qc_t.count_ops().values())}")
