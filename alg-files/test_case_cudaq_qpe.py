
# CUDAQ — QPE (Quantum Phase Estimation) 4큐비트
import cudaq
import numpy as np

@cudaq.kernel
def qpe_kernel(n_counting: int):
    # n_counting 개의 counting 큐비트 + 1개 target 큐비트
    q = cudaq.qvector(n_counting + 1)
    target = q[n_counting]

    # target 큐비트 초기화
    x(target)

    # counting 큐비트 Hadamard
    for i in range(n_counting):
        h(q[i])

    # controlled-U 적용 (U = T gate, phase = pi/4)
    for i in range(n_counting):
        for _ in range(2**i):
            t.ctrl(q[i], target)

    # inverse QFT
    for i in range(n_counting // 2):
        swap(q[i], q[n_counting - 1 - i])
    for i in range(n_counting):
        h(q[i])
        for j in range(i):
            r1.ctrl(-np.pi / (2**(i-j)), q[j], q[i])

counts = cudaq.sample(qpe_kernel, 4, shots_count=1024)
print("QPE counts:", counts)
