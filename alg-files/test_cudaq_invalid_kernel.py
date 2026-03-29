# test_cudaq_invalid_kernel.py
# 비정상 입력 테스트: CUDAQ 커널 내 고전 처리 코드 삽입
# - 커널 내 print() → NVQC 컴파일 오류
# - 커널 내 Python 리스트 조작 → 고전/양자 혼용 오류

import cudaq
import numpy as np

@cudaq.kernel
def invalid_kernel(n: int):
    q = cudaq.qvector(n)
    h(q[0])

    # 커널 내 고전 처리 — 컴파일 오류 유발
    print(f"qubit count: {n}")  # 커널 내 print 불가

    result_list = []               # Python 리스트 생성 불가
    result_list.append(n)         # append 불가

    for i in range(n - 1):
        cx(q[i], q[i + 1])

counts = cudaq.sample(invalid_kernel, 4)
print(counts)
