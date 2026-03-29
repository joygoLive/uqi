# test_invalid_qasm.py
# 비정상 입력 테스트: Qiskit QASM 변환 실패 유발
# - QuantumCircuit을 sampler.run()에 직접 넘기지 않고
#   불완전한 상태(measure 없음 + barrier만)로 제출
# - 극단적으로 큰 파라미터 (overflow 유발)
# - 동일 큐비트에 중복 측정 (QASM 유효성 오류)

from qiskit import QuantumCircuit
from qiskit.circuit import Parameter
from qiskit_aer.primitives import Sampler
import numpy as np

theta = Parameter('θ')

# 회로 1: measure 없이 barrier만 — QASM 변환 시 빈 측정 오류
qc_no_measure = QuantumCircuit(4)
qc_no_measure.h(0)
qc_no_measure.cx(0, 1)
qc_no_measure.barrier()
# measure 의도적으로 생략

# 회로 2: 동일 큐비트 중복 측정 — QASM 유효성 오류
qc_double_measure = QuantumCircuit(3, 3)
qc_double_measure.h(range(3))
qc_double_measure.measure(0, 0)
qc_double_measure.measure(0, 0)  # 중복 측정
qc_double_measure.measure(1, 1)
qc_double_measure.measure(2, 2)

# 회로 3: 파라미터 미바인딩 상태로 실행 시도
qc_unbound = QuantumCircuit(2)
qc_unbound.rx(theta, 0)
qc_unbound.cx(0, 1)
qc_unbound.measure_all()

sampler = Sampler()

# 파라미터 미바인딩 상태로 실행 — TypeError 유발
job = sampler.run([qc_no_measure, qc_double_measure, qc_unbound])
result = job.result()
print(result)
