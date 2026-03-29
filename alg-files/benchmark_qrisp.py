import numpy as np
from qrisp import QuantumFloat, QuantumBool, h, ry, cx, mcx, qft, measure
from qrisp import QuantumVariable

# 포트폴리오 파라미터 (3-asset)
weights     = np.array([0.4, 0.35, 0.25])
returns_mu  = np.array([0.08, 0.12, 0.06])
returns_sig = np.array([0.15, 0.20, 0.10])
n_bits      = 6   # 자산당 수익률 인코딩 비트
n_iter      = 4   # IQAE 반복

def encode_asset_return(qv: QuantumFloat, mu: float, sig: float):
    """자산 수익률 분포 인코딩"""
    n = qv.size
    for i in range(n):
        x = -1 + 2 * i / max(n - 1, 1)
        prob = np.exp(-0.5 * ((x - mu / sig) ** 2))
        prob = np.clip(prob / (sig * np.sqrt(2 * np.pi) * 0.5), 0, 1)
        ry(2 * np.arcsin(np.sqrt(prob)), qv[i])

def grover_operator(qv_list, anc):
    """Grover 반사 연산자"""
    for qv in qv_list:
        for q in qv:
            h(q)
            ry(np.pi, q)
            h(q)
    h(anc)
    cx(qv_list[0][0], anc)
    h(anc)

# 자산별 QuantumFloat 생성
assets = []
for i in range(len(weights)):
    qv = QuantumFloat(n_bits, signed=False)
    encode_asset_return(qv, returns_mu[i], returns_sig[i])
    assets.append(qv)

# 포트폴리오 수익률 추정용 보조 큐비트
anc = QuantumBool()
h(anc)

# IQAE 반복 (포트폴리오 기댓값 추정)
for iteration in range(n_iter):
    power = 2 ** iteration
    for _ in range(power):
        for i, (asset, w) in enumerate(zip(assets, weights)):
            angle = 2 * np.arcsin(np.sqrt(w * returns_mu[i] / sum(weights * returns_mu)))
            ry(angle * (iteration + 1) / n_iter, anc)
        grover_operator(assets, anc)

# 측정
anc_result  = anc.get_measurement()
asset_probs = [a.get_measurement() for a in assets]

# 포트폴리오 기댓값 추정
p1 = anc_result.get(True, 0.0)
portfolio_return = np.arcsin(np.sqrt(p1)) / np.pi * 2 * sum(weights * returns_mu)

print(f"[Qrisp] IQAE Portfolio Expected Return: {portfolio_return:.4f}")
print(f"[Qrisp] Classical Expected Return:      {sum(weights * returns_mu):.4f}")
for i, ap in enumerate(asset_probs):
    top = max(ap, key=ap.get) if ap else 0
    print(f"[Qrisp] Asset {i+1} top state: {top}")
