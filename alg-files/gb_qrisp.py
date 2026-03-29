# gb_qrisp.py
# GPU Benchmark - Qrisp → AerSimulator statevector GPU
# 24큐비트 QAOA — Qrisp는 내부적으로 AerSimulator 사용

import numpy as np
from qrisp import QuantumVariable, h, rz, cx, rx

n_qubits = 24
p = 6
gammas = np.random.uniform(0, np.pi, p)
betas  = np.random.uniform(0, np.pi / 2, p)

qv = QuantumVariable(n_qubits)
h(qv)

# 조밀한 엣지 구성으로 entanglement 극대화
edges = [(i, (i + 1) % n_qubits) for i in range(n_qubits)]
edges += [(i, (i + 2) % n_qubits) for i in range(n_qubits)]
edges += [(i, (i + 3) % n_qubits) for i in range(0, n_qubits, 2)]

for layer in range(p):
    for u, v in edges:
        cx(qv[u], qv[v])
        rz(2 * gammas[layer], qv[v])
        cx(qv[u], qv[v])
    for i in range(n_qubits):
        rx(2 * betas[layer], qv[i])

result = qv.get_measurement()
top = max(result, key=result.get)
print(f"[Qrisp] QAOA {n_qubits}q top: |{top}⟩  prob={result[top]:.6f}")
print(f"[Qrisp] Unique states: {len(result)}")
