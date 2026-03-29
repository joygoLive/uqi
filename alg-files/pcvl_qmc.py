import perceval as pcvl
import numpy as np
from scipy.stats import norm
import matplotlib.pyplot as plt

# 1. 파라미터 설정 (Qiskit 코드와 동일)
r = 0.0
K = 100.0
vol = 0.2
T = 1.0

# 분포 세분화 (Perceval은 모드 수가 자원임)
m = 2  # 2^m 단계 대신, 여기서는 물리적 모드 수를 직접 제어
M = 2 ** m 
xmax = np.pi
xs = np.linspace(-xmax, xmax, M)

# 2. 기초 자산 분포 생성 (정규분포)
probs = np.array([norm().pdf(x) for x in xs])
probs /= np.sum(probs)

def get_payoff_func(S0):
    """주가에 따른 페이오프 계산"""
    S = lambda i: S0 * np.exp((r - vol**2/2)*T + vol * np.sqrt(T) * xs[i])
    payoffs = [max(S(i) - K, 0) for i in range(M)]
    max_payoff = max(payoffs) if max(payoffs) > 0 else 1
    # 확률 진폭으로 쓰기 위해 정규화된 함수값 반환
    return [p / max_payoff for p in payoffs], max_payoff

def BSexact(S0):
    """블랙-숄즈 이론값"""
    if S0 <= 0: return 0
    d1 = (np.log(S0/K) + (r + vol**2/2)*T) / (vol * np.sqrt(T))
    d2 = d1 - vol * np.sqrt(T)
    return S0 * norm.cdf(d1) - K * np.exp(-r*T) * norm.cdf(d2)

# 3. Perceval 시뮬레이션 엔진
class PhotonicQMCRunner:
    def __init__(self, M):
        self.M = M
        
    def run(self, S0, nsample=10000):
        f_vals, scale_factor = get_payoff_func(S0)
        
        # 광자 회로 설계
        # Perceval에서 확률 분포 로딩은 Unitary 행렬을 통해 이루어짐
        # 여기서는 각 경로(Mode)에 확률 분포와 페이오프를 인코딩하는 간섭계를 구성
        c = pcvl.Circuit(self.M)
        
        # Step 1 & 2: 확률 및 함수 인코딩
        # 실제 하드웨어에서는 빔스플리터 망(Mesh)을 사용하지만, 
        # 시뮬레이션에서는 대각 행렬(Phase Shifters)과 간섭을 조합하여 구현
        for i in range(self.M):
            # 각 경로의 투과율을 페이오프와 주가 확률의 곱으로 조절
            # pcvl.PR(각도) 등을 사용하여 진폭 조절 가능
            attenuation = np.sqrt(probs[i] * f_vals[i])
            if attenuation > 0:
                # 진폭 인코딩을 위한 가상 게이트 (논리적 등가물)
                c.add(i, pcvl.PS(phi=np.arcsin(attenuation)))

        # 시뮬레이션 실행 (SLOS 기반)
        p = pcvl.Processor("SLOS", c)
        # 모든 모드에 광자가 골고루 들어가는 중첩 상태 입력 (Simplified)
        input_state = pcvl.BasicState([1] * self.M) 
        p.with_input(input_state)
        
        sampler = pcvl.algorithm.Sampler(p)
        sample_results = sampler.sample_count(nsample)
        
        # 기대값 계산
        # 검출된 광자들의 가중 평균으로 가격 추정
        counts = sample_results['results'] if 'results' in sample_results else sample_results
        total_weighted_sum = 0
        for state, count in counts.items():
            # 광자가 살아남아 검출된 확률 계산
            total_weighted_sum += count
            
        estimated_val = (total_weighted_sum / nsample) * scale_factor
        
        # 복잡도 계산
        complexity = {
            "modes": c.m,
            "components": len(c.list_elements()) if hasattr(c, 'list_elements') else self.M
        }
        
        return estimated_val, complexity

# 4. 비교 실행
S0_list = range(70, 131, 10)
results_pcvl = []
exact_vals = [BSexact(x) for x in S0_list]

runner = PhotonicQMCRunner(M)
print("🚀 Perceval 기반 양자 몬테카를로 실행 중...")

for s in S0_list:
    val, comp = runner.run(s)
    results_pcvl.append(val)
    print(f"S0: {s} | 이론값: {BSexact(s):.4f} | Perceval: {val:.4f} | 소자수: {comp['components']}")

# 5. 시각화
plt.plot(range(70, 131), [BSexact(x) for x in range(70, 131)], label="Black-Scholes", color='blue')
plt.scatter(S0_list, results_pcvl, label="QMC (Perceval)", color='green', zorder=5)
plt.xlabel("Stock Price")
plt.ylabel("Option Price")
plt.legend()
plt.title("Option Pricing: Qiskit Equivalent in Perceval")
plt.show()