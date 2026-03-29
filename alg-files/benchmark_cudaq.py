import cudaq
import numpy as np

# Hull-White 이자율 모델 파라미터
r0    = 0.03
kappa = 0.1
theta = 0.05
sigma = 0.01
T     = 1.0

n_state = 8
n_phase = 10

# 커널 외부에서 전부 미리 계산
r_mean = theta + (r0 - theta) * np.exp(-kappa * T)
r_std  = sigma * np.sqrt((1 - np.exp(-2 * kappa * T)) / (2 * kappa))

# 인코딩 각도
encoding_angles = []
for i in range(n_state):
    x = -1.0 + 2.0 * i / (n_state - 1)
    prob = float(np.clip(np.exp(-0.5 * ((x - r_mean / r_std) ** 2)), 0.0, 1.0))
    encoding_angles.append(float(2.0 * np.arcsin(np.sqrt(prob))))

# Controlled-U: flat list
ctrl_j_idx = []
ctrl_s_idx = []
ctrl_angle  = []
for j in range(n_phase):
    power = 2 ** j
    angle = 2.0 * np.pi * r_mean * power / n_phase / n_state
    for _ in range(power):
        for i in range(n_state):
            ctrl_j_idx.append(j)
            ctrl_s_idx.append(i)
            ctrl_angle.append(float(angle))

# IQFT CR1: flat list
iqft_i_idx = []
iqft_j_idx = []
iqft_angle = []
for i in range(n_phase):
    for j in range(i + 1, n_phase):
        iqft_i_idx.append(i)
        iqft_j_idx.append(j)
        iqft_angle.append(float(-np.pi / (2 ** (j - i))))

n_ctrl = len(ctrl_angle)
n_iqft = len(iqft_angle)

@cudaq.kernel
def qpe_interest_rate(
    enc:       list[float],
    c_angle:   list[float],
    c_j:       list[int],
    c_s:       list[int],
    q_angle:   list[float],
    q_i:       list[int],
    q_j:       list[int],
    n_s:       int,
    n_p:       int,
    n_c:       int,
    n_q:       int,
):
    q = cudaq.qvector(n_s + n_p)

    # 이자율 분포 인코딩
    for i in range(n_s):
        ry(enc[i], q[i])

    # Phase qubits 초기화
    for i in range(n_p):
        h(q[n_s + i])

    # Controlled-U (flat 순회)
    for k in range(n_c):
        cr1(c_angle[k], q[n_s + c_j[k]], q[c_s[k]])

    # Inverse QFT - swap
    for i in range(n_p // 2):
        swap(q[n_s + i], q[n_s + n_p - 1 - i])

    # Inverse QFT - H + CR1 (flat 순회)
    for i in range(n_p):
        h(q[n_s + i])
    for k in range(n_q):
        cr1(q_angle[k], q[n_s + q_j[k]], q[n_s + q_i[k]])

    mz(q)

result = cudaq.sample(
    qpe_interest_rate,
    encoding_angles,
    ctrl_angle, ctrl_j_idx, ctrl_s_idx,
    iqft_angle, iqft_i_idx, iqft_j_idx,
    n_state, n_phase, n_ctrl, n_iqft,
    shots_count=2048
)

# 결과 처리
counts_dict = {bits: result.count(bits) for bits in result}
top_bitstr  = max(counts_dict, key=counts_dict.get)
phase_bits  = top_bitstr[n_state:]
phase_val   = int(phase_bits, 2) / 2**n_phase
r_estimated = phase_val * 2 * r_std + (r_mean - r_std)

print(f"[CUDAQ] QPE Hull-White Rate Estimate: {r_estimated:.4f}")
print(f"[CUDAQ] Classical r_mean:             {r_mean:.4f}")
print(f"[CUDAQ] Phase value:                  {phase_val:.4f}")
