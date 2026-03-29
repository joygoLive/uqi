# qrisp_multi_session_test.py
# 멀티 세션 케이스 검증용
# 케이스 A: 서브루틴 분리 (메인 + 서브 세션)
# 케이스 B: 독립 세션 병렬 (동일 크기)

from qrisp import QuantumSession, QuantumVariable, QuantumFloat
import numpy as np

# 케이스 A: 서브루틴 세션
qs_sub = QuantumSession()
qv_sub = QuantumFloat(3, qs=qs_sub)
qv_sub[:] = 3  # 서브루틴: 값 인코딩

# 케이스 B: 메인 세션 (더 큰 세션)
qs_main = QuantumSession()
qv_main = QuantumFloat(5, qs=qs_main)
qv_main[:] = 7

# 측정 (두 세션 모두 측정에 기여)
result_sub  = qv_sub.get_measurement()
result_main = qv_main.get_measurement()

print(f"sub  result: {result_sub}")
print(f"main result: {result_main}")