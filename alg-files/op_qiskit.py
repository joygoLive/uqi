import numpy as np
from qiskit import QuantumCircuit, transpile
# 1. 일반 회로 라이브러리에서 로드
from qiskit.circuit.library import LinearAmplitudeFunction

# 2. 파이낸스 전용 라이브러리에서 로드
from qiskit_finance.circuit.library import LogNormalDistribution
from qiskit_aer import AerSimulator
from qiskit.visualization import plot_histogram

# 1. 시뮬레이터 초기화
simulator = AerSimulator()

# 2. 금융 모델 파라미터 (시뮬레이션이므로 큐비트를 조금 늘려봅니다)
num_uncertainty_qubits = 3  # 자산 가격 분포 큐비트
S = 2.0                     # 현재 자산 가격
vol = 0.4                   # 변동성 (40%)
r = 0.05                    # 이자율 (5%)
T = 40 / 365                # 만기 (일 단위)
strike_price = 1.9          # 행사가격

# 로그-정규 분포 파라미터 계산
mu = ((np.log(S) + (r - 0.5 * vol**2) * T))
sigma = vol * np.sqrt(T)
low, high = 0, 4

# 3. 확률 분포 회로 생성 (P)
uncertainty_model = LogNormalDistribution(
    num_uncertainty_qubits, mu=mu, sigma=sigma**2, bounds=(low, high)
)

# 4. 페이오프 함수 회로 생성 (f)
# 유러피언 콜 옵션: max(0, S - K)
european_call_objective = LinearAmplitudeFunction(
    num_uncertainty_qubits,
    [0, 1], [0, 0],
    domain=(low, high), image=(0, high - strike_price),
    breakpoints=[low, strike_price],
    rescaling_factor=0.25
)

# 5. 전체 회로 조립
num_qubits = european_call_objective.num_qubits
qc = QuantumCircuit(num_qubits)
qc.append(uncertainty_model, range(num_uncertainty_qubits))
qc.append(european_call_objective, range(num_qubits))

# 측정 추가 (시뮬레이션 결과를 보기 위함)
qc.measure_all()

# 6. 트랜스파일 및 실행
# 시뮬레이터이므로 복잡한 최적화 대신 기본 게이트로 분해(Decompose) 확인
compiled_circuit = transpile(qc, simulator)
job = simulator.run(compiled_circuit, shots=2048)
result = job.result()
counts = result.get_counts()

# 7. 결과 분석
print(f"회로 큐비트 수: {qc.num_qubits}")
print(f"회로 깊이(Depth): {qc.depth()}")
print(f"사용된 게이트 종류: {qc.count_ops()}")
print("\n측정 결과 요약 (상위 5개):")
sorted_counts = sorted(counts.items(), key=lambda x: x[1], reverse=True)
for state, count in sorted_counts[:5]:
    print(f"State: {state}, Count: {count}")