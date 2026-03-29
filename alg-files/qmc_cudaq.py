"""
qmc_cudaq.py
Quantum Monte Carlo for Black-Scholes Option Pricing
Using CUDA-Q + cq_iqae + cq_stateprep
"""

import numpy as np
import matplotlib.pyplot as plt
from scipy.stats import norm
from typing import List
import cudaq

from cq_iqae import EstimationProblem, IterativeAmplitudeEstimation
from cq_stateprep import mottonen_angles

from cq_backend import set_backend

target = "iqm"   # "nvidia" or "iqm"
set_backend(target)

# ── Option parameters ─────────────────────────────────────────────────────────
r   = 0.05
K   = 100.0
vol = 0.2
T   = 1.0
m   = 4

# ── Black-Scholes 정답 ────────────────────────────────────────────────────────
def BSexact(S0, K=K, r=r, vol=vol, T=T):
    d1 = (np.log(S0/K) + (r + vol**2/2)*T) / (vol*np.sqrt(T))
    d2 = d1 - vol*np.sqrt(T)
    return S0*norm.cdf(d1) - K*np.exp(-r*T)*norm.cdf(d2)

# ── QMC 옵션 가격 계산 ────────────────────────────────────────────────────────
def qMC_cudaq(S0_val, epsilon=0.005, alpha=0.05):
    drift    = (r - vol**2/2)*T
    b        = vol*np.sqrt(T)
    n_points = 2**m
    n_qubits = m + 1

    xs    = np.linspace(-3, 3, n_points)
    probs = norm.pdf(xs)
    probs /= probs.sum()

    payoffs      = np.array([max(S0_val*np.exp(drift + b*z) - K, 0) for z in xs])
    scale_factor = max(payoffs.max(), 1e-10)
    payoffs_norm = np.clip(payoffs / scale_factor, 0.0, 1.0)

    sv = np.zeros(2 ** n_qubits)
    for i in range(n_points):
        sv[i * 2 + 0] = np.sqrt(probs[i] * (1.0 - payoffs_norm[i]))
        sv[i * 2 + 1] = np.sqrt(probs[i] * payoffs_norm[i])
    sv /= np.linalg.norm(sv)

    types, angles, targets, controls = mottonen_angles(sv)
    controls = [c if c >= 0 else 0 for c in controls]

    problem = EstimationProblem(
        state_preparation = None,
        objective_qubits  = [0],   # m → 0 으로 변경
        post_processing   = lambda x: x * scale_factor * np.exp(-r*T),
        state_prep_args   = (types, angles, targets, controls),
    )
    problem._n_qubits = n_qubits

    iae = IterativeAmplitudeEstimation(
        epsilon_target = epsilon,
        alpha          = alpha,
        shots          = 1000,
    )

    result = iae.estimate(problem)
    # classical 기댓값 검증
    e_classical = sum(probs[i] * payoffs_norm[i] for i in range(n_points))
    e_classical_price = e_classical * scale_factor * np.exp(-r*T)
    
    # sv에서 직접 ancilla=1 확률 계산
    sv_prob_1 = sum(sv[i*2+1]**2 for i in range(n_points))

    return result.estimation_processed


# ── 단일 테스트 ───────────────────────────────────────────────────────────────
print("=" * 55)
print("  CUDA-Q QMC – Black-Scholes Option Pricing")
print("=" * 55)
print(f"  S0=100, K={K}, r={r*100:.1f}%, vol={vol*100:.0f}%, T={T}")
print(f"  m={m} ({2**m} grid points), total qubits={m+1}")
print("-" * 55)

print("\n[Single test: S0 = 100]")

qmc_100 = qMC_cudaq(100.0)
bs_100  = BSexact(100.0)
print(f"  QMC  result : {qmc_100:.4f}")
print(f"  BS   theory : {bs_100:.4f}")
print(f"  Error       : {abs(qmc_100-bs_100):.4f}  ({abs(qmc_100-bs_100)/bs_100*100:.2f}%)")

# # ── S0 range 비교 ─────────────────────────────────────────────────────────────
# print("\n[Running QMC over S0 = 70..130 (step 10)...]")
# S0_range_exact = range(70, 131)
# S0_range_qmc   = range(70, 131, 10)

# final_payoff = [max(x-K, 0) for x in S0_range_exact]
# val_exact    = [BSexact(x) for x in S0_range_exact]

# val_qmc = []
# for s in S0_range_qmc:
#     v = qMC_cudaq(float(s))
#     val_qmc.append(v)
#     print(f"  S0={s:3d}  QMC={v:.4f}  BS={BSexact(float(s)):.4f}  "
#           f"err={abs(v-BSexact(float(s))):.4f}")


# # ── 플롯 ─────────────────────────────────────────────────────────────────────
# plt.figure(figsize=(9, 5))
# plt.plot(S0_range_exact, final_payoff, "r-",  label="Payoff")
# plt.plot(S0_range_exact, val_exact,    "b-",  label="Black-Scholes")
# plt.plot(S0_range_qmc,   val_qmc,      "go",  label="QMC (CUDA-Q)", ms=8)
# plt.legend(fontsize=11)
# plt.xlabel("Stock Price S0")
# plt.ylabel("Option Price")
# plt.title("European Call Option – CUDA-Q QMC vs Black-Scholes")
# plt.annotate(f"K={K}, r={r*100:.1f}%, vol={vol*100:.0f}%, T={T}",
#              xy=(70, max(val_exact)*0.85))
# plt.annotate(f"m={m} ({2**m} pts), CUDA-Q nvidia",
#              xy=(70, max(val_exact)*0.75))
# plt.tight_layout()
# plt.savefig("qmc_cudaq_result.png", dpi=150)
# plt.show()
print("\nDone.")