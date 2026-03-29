"""
Quantum Monte Carlo for Black-Scholes Option Pricing
Using Qrisp IQAE directly (following official tutorial pattern)
"""

import numpy as np
import matplotlib.pyplot as plt
from scipy.stats import norm as scipy_norm
from qrisp import QuantumFloat, QuantumBool, h, x, ry, mcx, auto_uncompute, prepare
from qrisp.alg_primitives import IQAE
from qrisp import rz, MCRXGate
from qrisp.circuit import QuantumCircuit


# ── Option parameters ────────────────────────────────────────────────────────
r   = 0.05
K   = 100.0
vol = 0.2
T   = 1.0
m   = 5   # x축 qubit 수 → 2^m = 32 샘플링 포인트

# ── Black-Scholes 정답 ───────────────────────────────────────────────────────
def BSexact(S0, K=K, r=r, vol=vol, T=T):
    d1 = (np.log(S0/K) + (r + vol**2/2)*T) / (vol*np.sqrt(T))
    d2 = d1 - vol*np.sqrt(T)
    return S0*scipy_norm.cdf(d1) - K*np.exp(-r*T)*scipy_norm.cdf(d2)

# ── IQAE 기반 옵션 가격 계산 ─────────────────────────────────────────────────
def qMC_qrisp(S0_val):
    drift = (r - vol**2/2)*T
    b     = vol*np.sqrt(T)
    z_low, z_high = -3.0, 3.0
    n_points = 2**m

    xs = np.linspace(z_low, z_high, n_points)
    probs = scipy_norm.pdf(xs)
    probs /= probs.sum()

    payoffs = np.array([max(S0_val*np.exp(drift + b*z) - K, 0) for z in xs])
    scale_factor = max(payoffs) + 1e-10
    payoffs_norm = payoffs / scale_factor  # [0, 1]

    # RY angles: theta_i = 2*arcsin(sqrt(payoffs_norm[i]))
    thetas = 2 * np.arcsin(np.sqrt(payoffs_norm))

    # expected_classical = sum(probs[i] * payoffs_norm[i] for i in range(n_points))

    qf_x = QuantumFloat(m, -m)   # index qubit
    qbl  = QuantumBool()

    @auto_uncompute
    def state_function(qf_x, qbl):
        prepare(qf_x, np.sqrt(probs))

        for i in range(n_points):
            binary = format(i, f'0{m}b')[::-1]  # LSB first로 reverse
            for j, bit in enumerate(binary):
                if bit == '0':
                    x(qf_x[j])
            
            # 변경 후: RY = RZ(-π/2) · RX(θ) · RZ(π/2)
            rz(np.pi/2, qbl)
            qbl.qs.append(MCRXGate(thetas[i], m), list(qf_x) + list(qbl))
            rz(-np.pi/2, qbl)
            
            # X gate 복원
            for j, bit in enumerate(binary):
                if bit == '0':
                    x(qf_x[j])

    a_est = IQAE([qf_x, qbl], state_function, eps=0.01, alpha=0.01)
    
    price = a_est * scale_factor * np.exp(-r*T)
    return price

# ── 테스트 ─────────────────────────────────────────────────────────────────
print("=" * 55)
print("  Qrisp IQAE – Black-Scholes Option Pricing")
print("=" * 55)
print(f"  K={K}, r={r*100:.1f}%, vol={vol*100:.0f}%, T={T}, m={m}")
print("-" * 55)

print("\n[Single test: S0 = 100]")
qmc_100 = qMC_qrisp(100.0)
bs_100  = BSexact(100.0)
print(f"  QMC  result : {qmc_100:.4f}")
print(f"  BS   theory : {bs_100:.4f}")
print(f"  Error       : {abs(qmc_100-bs_100):.4f}  ({abs(qmc_100-bs_100)/bs_100*100:.2f}%)")

# print("\n[Running QMC over S0 = 70..130 (step 10)...]")
# S0_range_exact = range(70, 131)
# S0_range_qmc   = range(70, 131, 10)

# final_payoff = [max(x-K, 0) for x in S0_range_exact]
# val_exact    = [BSexact(x) for x in S0_range_exact]

# val_qmc = []
# for s in S0_range_qmc:
#     v = qMC_qrisp(float(s))
#     val_qmc.append(v)
#     print(f"  S0={s:3d}  QMC={v:.4f}  BS={BSexact(float(s)):.4f}  "
#           f"err={abs(v-BSexact(float(s))):.4f}")

# plt.figure(figsize=(9,5))
# plt.plot(S0_range_exact, final_payoff, "r-",  label="Payoff")
# plt.plot(S0_range_exact, val_exact,    "b-",  label="Black-Scholes")
# plt.plot(S0_range_qmc,   val_qmc,      "go",  label="QMC (Qrisp)", ms=8)
# plt.legend(fontsize=11)
# plt.xlabel("Stock Price S0")
# plt.ylabel("Option Price")
# plt.title("European Call Option – Qrisp IQAE vs Black-Scholes")
# plt.annotate(f"K={K}, r={r*100:.1f}%, vol={vol*100:.0f}%, T={T}", xy=(70, max(val_exact)*0.85))
# plt.tight_layout()
# plt.savefig("qmc_qrisp_result.png", dpi=150)
# plt.show()
print("\nDone.")