import numpy as np
from scipy.stats import norm
from qiskit_aer import Aer
from qiskit_algorithms import IterativeAmplitudeEstimation, EstimationProblem
from qiskit.primitives import BackendSampler
from qiskit import QuantumCircuit
from qiskit.circuit.library import UnitaryGate

# 1. 문제 파라미터 (Qiskit Finance 튜토리얼 기준)
n_z, z_max = 2, 2
p_zeros, rhos, lgd = [0.15, 0.25], [0.1, 0.05], [1, 2]
K = len(p_zeros)
confidence_level = 0.95  # VaR 95%

# 2. Oracle (State Preparation) 생성 함수
def build_legacy_oracle(n_z, z_max, p_zeros, rhos, lgd, mode='el', x_eval=None):
    N_dim = 2**(n_z + K + 1)
    z_vals = np.linspace(-z_max, z_max, 2**n_z)
    pz = norm.pdf(z_vals) / norm.pdf(z_vals).sum()
    sv = np.zeros(N_dim, dtype=complex)
    L_max = float(sum(lgd))

    for i, z in enumerate(z_vals):
        p_cond = [norm.cdf((norm.ppf(p_zeros[k]) - np.sqrt(rhos[k]) * z)/np.sqrt(1-rhos[k])) for k in range(K)]
        for mask in range(2**K):
            prob = pz[i]
            loss = 0
            for k in range(K):
                if mask & (1 << k): 
                    prob *= p_cond[k]
                    loss += lgd[k]
                else: 
                    prob *= (1 - p_cond[k])
            
            # 인코딩 모드 선택: Expected Loss vs CDF
            f_hat = (loss/L_max) if mode == 'el' else (1.0 if loss <= x_eval else 0.0)
            
            # 상태 인덱스 계산 (z bits | default bits | objective bit)
            idx = ((i << K) | mask) << 1
            sv[idx] = np.sqrt(prob * (1.0 - f_hat))
            sv[idx+1] = np.sqrt(prob * f_hat)

    # [핵심] UnitaryGate 생성 (Householder Transformation)
    # 복소수 타입 에러를 피하기 위해 행렬 형태로 주입합니다.
    sv /= np.linalg.norm(sv)
    e1 = np.zeros(N_dim); e1[0] = 1.0
    v = sv - e1
    if np.linalg.norm(v) < 1e-10:
        U = np.eye(N_dim)
    else:
        U = np.eye(N_dim) - 2 * np.outer(v, v.conj()) / np.dot(v.conj(), v)
    
    qc = QuantumCircuit(n_z + K + 1)
    qc.append(UnitaryGate(U), range(n_z + K + 1))
    return qc, L_max

# 3. 메인 실행 루틴
if __name__ == "__main__":
    print("="*60)
    print(f"📊 Qiskit Legacy Credit Risk Analysis (Aer 0.13.3)")
    print("="*60)

    # A. 백엔드 및 IQAE 설정
    backend = Aer.get_backend('statevector_simulator')
    sampler = BackendSampler(backend)
    iae = IterativeAmplitudeEstimation(epsilon_target=0.01, alpha=0.05, sampler=sampler)

    # B. Expected Loss 계산
    print("\n[Step 1] Calculating Expected Loss...")
    oracle_el, L_max = build_legacy_oracle(n_z, z_max, p_zeros, rhos, lgd, mode='el')
    problem_el = EstimationProblem(state_preparation=oracle_el, objective_qubits=[n_z + K])
    
    result_el = iae.estimate(problem_el)
    qmc_el = result_el.estimation * L_max
    print(f">> QMC Expected Loss: {qmc_el:.4f}")

    # C. VaR (Value at Risk) 계산 - 이진 탐색
    print("\n[Step 2] Calculating VaR (95%) via Bisection...")
    low, high = 0, int(L_max)
    var_estimate = high
    
    print(f"{'Loss':<8} | {'CDF Prob':<10} | {'Status'}")
    print("-" * 35)

    while low <= high:
        mid = (low + high) // 2
        oracle_cdf, _ = build_legacy_oracle(n_z, z_max, p_zeros, rhos, lgd, mode='cdf', x_eval=mid)
        problem_cdf = EstimationProblem(state_preparation=oracle_cdf, objective_qubits=[n_z + K])
        
        result_cdf = iae.estimate(problem_cdf)
        cdf_val = result_cdf.estimation
        
        status = "KEEP" if cdf_val >= confidence_level else "UP"
        print(f"{mid:<8} | {cdf_val:<10.4f} | {status}")
        
        if cdf_val >= confidence_level:
            var_estimate = mid
            high = mid - 1
        else:
            low = mid + 1

    print(f"\n>> Final QMC VaR (95%): {var_estimate}")
    print("\n" + "="*60)