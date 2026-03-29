# Qrisp — Quantum Phase Estimation 7큐비트
# counting: 6큐비트, target: 1큐비트
from qrisp import QuantumVariable, h, x, swap, p, cx, measure
import numpy as np

def iqft(qv, n):
    for i in range(n//2):
        swap(qv[i], qv[n-1-i])
    for i in range(n):
        h(qv[i])
        for j in range(i+1, n):
            p(-np.pi / 2**(j-i), qv[j])

n_count = 6
counting = QuantumVariable(n_count)
target   = QuantumVariable(1)

# 초기화
x(target[0])
h(counting)

# controlled-T 반복 (phase = pi/4 → 고유값 추정)
for i in range(n_count):
    reps = 2**i
    for _ in range(reps):
        # T gate = P(pi/4)
        p(np.pi/4, target[0])

# inverse QFT
iqft(counting, n_count)

result = counting.get_measurement()
print("QPE result:", result)
