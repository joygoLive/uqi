# qrisp_qpe_medium.py
# Quantum Phase Estimation (QPE) - 중규모 테스트
# 타겟 위상: θ = 0.3 (T-gate 계열 커스텀 유니터리)
# QPE 레지스터: 5 큐비트 / 타겟 레지스터: 3 큐비트 → 총 8 큐비트
# cp() 미사용 - cx+rz 분해로 controlled phase 구현

from qrisp import QuantumSession, QuantumVariable, h, swap, cx, x, rz
import numpy as np

def controlled_phase(angle, ctrl, tgt):
    """cp(angle, ctrl, tgt) 분해: cx+rz 조합"""
    rz(angle / 2, ctrl)
    cx(ctrl, tgt)
    rz(-angle / 2, tgt)
    cx(ctrl, tgt)
    rz(angle / 2, tgt)

# ── 세션 ──────────────────────────────────────────────
qs = QuantumSession()

# QPE ancilla 레지스터 (위상 추정용, 5큐비트)
qpe_reg = QuantumVariable(5, qs=qs)

# 타겟 레지스터 (고유벡터 |1⟩ 인코딩, 3큐비트)
target = QuantumVariable(3, qs=qs)

# ── 초기화 ────────────────────────────────────────────
for i in range(5):
    h(qpe_reg[i])

x(target[0])

# ── Controlled-U^(2^k) 적용 ───────────────────────────
theta = 2 * np.pi * 0.3

for k in range(5):
    angle = theta * (2 ** k)
    controlled_phase(angle, qpe_reg[k], target[0])

# ── Inverse QFT (5큐비트) ─────────────────────────────
def inverse_qft(qv, n):
    for i in range(n // 2):
        swap(qv[i], qv[n - 1 - i])
    for i in range(n):
        h(qv[i])
        for j in range(i + 1, n):
            controlled_phase(-np.pi / (2 ** (j - i)), qv[j], qv[i])

inverse_qft(qpe_reg, 5)

# ── 측정 ──────────────────────────────────────────────
result = qpe_reg.get_measurement()

print("QPE result (위상 추정):")
top = sorted(result.items(), key=lambda x: -x[1])[:5]
for state, prob in top:
    estimated_phase = int(state, 2) / (2 ** 5)
    print(f"  |{state}⟩  prob={prob:.4f}  θ_est={estimated_phase:.4f}  (true θ=0.3)")
