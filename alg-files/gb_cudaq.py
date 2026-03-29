# gb_cudaq.py
# GPU Benchmark - CUDAQ nvidia target (GPU 가속 확인용)
# 20큐비트 — CPU/GPU 비교에 적합한 크기

import cudaq
import numpy as np

n_qubits = 20
angles = [float(np.pi / (2 ** (i % 6) + 1)) for i in range(n_qubits)]

@cudaq.kernel
def sv_circuit(angles: list[float], n: int):
    q = cudaq.qvector(n)
    for i in range(n):
        h(q[i])
    for i in range(n - 1):
        x.ctrl(q[i], q[i + 1])
    for i in range(n):
        rz(angles[i], q[i])
        ry(angles[(i + 1) % n], q[i])
    for i in range(n - 1, 0, -1):
        x.ctrl(q[i], q[i - 1])
    for i in range(n):
        rx(angles[(i + 2) % n], q[i])
    mz(q)

result = cudaq.sample(sv_circuit, angles, n_qubits, shots_count=1024)
counts = {bits: result.count(bits) for bits in result}
top = max(counts, key=counts.get)
print(f"[CUDAQ] {n_qubits}q top: |{top[:8]}...⟩  count={counts[top]}")
print(f"[CUDAQ] Unique states: {len(counts)}")
