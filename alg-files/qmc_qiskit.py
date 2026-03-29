"""
Quantum Monte Carlo for Black-Scholes Option Pricing
Using Qiskit Finance native modules (LogNormalDistribution + EuropeanCallPricing)

원본 PennyLane 코드와 동일한 파라미터:
  r=0.0, K=100, vol=0.2, T=1.0
  m=5 (distribution qubits), n=5 (estimation qubits)
"""

import numpy as np
import matplotlib.pyplot as plt
from scipy.stats import norm

# ── Qiskit imports ──────────────────────────────────────────────────────────
from qiskit_finance.circuit.library import LogNormalDistribution
from qiskit_finance.applications.estimation import EuropeanCallPricing
from qiskit_algorithms import IterativeAmplitudeEstimation, EstimationProblem
from qiskit_aer import AerSimulator
from qiskit.primitives import StatevectorSampler as Sampler

# ── Option parameters (원본과 동일) ─────────────────────────────────────────
r   = 0.05      # risk-free rate  ※ log-normal에서 drift로 사용
K   = 100.0     # strike price
vol = 0.2       # volatility
T   = 1.0       # time to maturity
S0  = 100.0     # spot price (log-normal 분포 중심)

m = 5           # distribution qubits  → 2^m = 32 grid points
n = 5           # estimation qubits    → QPE 정밀도

# ── Black-Scholes 정답 ──────────────────────────────────────────────────────
def BSexact(S0, K=K, r=r, vol=vol, T=T):
    d1 = (np.log(S0 / K) + (r + vol**2 / 2) * T) / (vol * np.sqrt(T))
    d2 = d1 - vol * np.sqrt(T)
    return S0 * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)

# ── Log-normal distribution 파라미터 ────────────────────────────────────────
# S_T ~ LogNormal(mu, sigma^2)
# mu    = log(S0) + (r - vol^2/2)*T
# sigma = vol * sqrt(T)
mu_ln    = np.log(S0) + (r - vol**2 / 2) * T
sigma_ln = vol * np.sqrt(T)

# 분포 범위: [low, high] ─ 약 ±3σ
low  = np.exp(mu_ln - 3 * sigma_ln)
high = np.exp(mu_ln + 3 * sigma_ln)

print("=" * 55)
print("  Qiskit Finance QMC – Black-Scholes Option Pricing")
print("=" * 55)
print(f"  S0={S0}, K={K}, r={r*100:.1f}%, vol={vol*100:.0f}%, T={T}")
print(f"  Log-normal range: [{low:.2f}, {high:.2f}]")
print(f"  m={m} (dist qubits), n={n} (est qubits)")
print(f"  Total qubits: {m + n + 1}")
print("-" * 55)

# ── 1. Log-Normal 분포 회로 ─────────────────────────────────────────────────
uncertainty_model = LogNormalDistribution(
    num_qubits=m,
    mu=mu_ln,
    sigma=sigma_ln**2,   # LogNormalDistribution은 sigma^2 (variance)를 받음
    bounds=(low, high),
)

# ── 2. EuropeanCallPricing (payoff 인코딩 + 정규화) ─────────────────────────
european_call = EuropeanCallPricing(
    num_state_qubits=m,
    strike_price=K,
    rescaling_factor=0.25,   # payoff을 [0,1]로 정규화하는 스케일 팩터
    bounds=(low, high),
    uncertainty_model=uncertainty_model,
)

# ── 3. EstimationProblem 정의 ────────────────────────────────────────────────
problem = EstimationProblem(
    state_preparation=european_call,
    objective_qubits=[m],           # ancilla qubit index
    # post_processing=european_call.post_processing,
)

# ── 4. IQAE 실행 함수 ────────────────────────────────────────────────────────
def qMC_finance(S0_val, epsilon=0.01, alpha=0.05):
    """
    S0_val : spot price
    epsilon: 추정 정밀도 (IQAE convergence threshold)
    alpha  : 신뢰 구간 유의수준
    """
    # S0에 따라 distribution 파라미터 업데이트
    mu_val    = np.log(S0_val) + (r - vol**2 / 2) * T
    sigma_val = vol * np.sqrt(T)
    low_val   = np.exp(mu_val - 3 * sigma_val)
    high_val  = np.exp(mu_val + 3 * sigma_val)

    dist = LogNormalDistribution(
        num_qubits=m,
        mu=mu_val,
        sigma=sigma_val**2,
        bounds=(low_val, high_val),
    )

    call = EuropeanCallPricing(
        num_state_qubits=m,
        strike_price=K,
        rescaling_factor=0.25,
        bounds=(low_val, high_val),
        uncertainty_model=dist,
    )

    prob = call.to_estimation_problem()

    sampler = Sampler()

    iae = IterativeAmplitudeEstimation(
        epsilon_target=epsilon,
        alpha=alpha,
        sampler=sampler,
    )

    result = iae.estimate(prob)

    result = iae.estimate(prob)
    price = np.exp(-r * T) * prob.post_processing(result.estimation)
    return price

# ── 5. S0=100 단일 테스트 ────────────────────────────────────────────────────
print("\n[Single test: S0 = 100]")
qmc_100 = qMC_finance(100.0)
bs_100  = BSexact(100.0)
err_100 = abs(qmc_100 - bs_100)
print(f"  QMC  result : {qmc_100:.4f}")
print(f"  BS   theory : {bs_100:.4f}")
print(f"  Error       : {err_100:.4f}  ({err_100/bs_100*100:.2f}%)")

# ── 6. S0 range 비교 ─────────────────────────────────────────────────────────
print("\n[Running QMC over S0 = 70..130 (step 10)...]")
S0_range_exact = range(70, 131)
S0_range_qmc   = range(70, 131, 10)

final_payoff = [max(x - K, 0) for x in S0_range_exact]
val_exact    = [BSexact(x) for x in S0_range_exact]

val_qmc = []
for s in S0_range_qmc:
    v = qMC_finance(float(s))
    val_qmc.append(v)
    print(f"  S0={s:3d}  QMC={v:.4f}  BS={BSexact(float(s)):.4f}  "
          f"err={abs(v-BSexact(float(s))):.4f}")

# ── 7. 플롯 ──────────────────────────────────────────────────────────────────
plt.figure(figsize=(9, 5))
plt.plot(S0_range_exact, final_payoff, "r-",  label="Payoff")
plt.plot(S0_range_exact, val_exact,    "b-",  label="Black-Scholes")
plt.plot(S0_range_qmc,   val_qmc,      "go",  label="QMC (Qiskit Finance)", ms=8)
plt.legend(fontsize=11)
plt.xlabel("Stock Price S0")
plt.ylabel("Option Price")
plt.title("European Call Option – QMC vs Black-Scholes")
plt.annotate(f"K={K}, r={r*100:.1f}%, vol={vol*100:.0f}%, T={T}",
             xy=(70, max(val_exact) * 0.85))
plt.annotate(f"Qubits: {m+n+1}  (m={m}, n={n})",
             xy=(70, max(val_exact) * 0.75))
plt.tight_layout()
plt.savefig("qmc_qiskit_result.png", dpi=150)
plt.show()
print("\nDone. Plot saved to qmc_result.png")