"""
Metriq Benchmark: LR-QAOA (Linear Ramp QAOA)
Weighted Max-Cut 문제를 선형 램프 파라미터 스케줄로 풀어
근사비(approximation ratio)를 측정하는 벤치마크.
"""
import numpy as np
from qiskit import QuantumCircuit, transpile
from qiskit_aer import AerSimulator

n_qubits = 8
p_layers = 4  # QAOA depth
shots = 4096

rng = np.random.default_rng(42)

# --- 랜덤 가중치 그래프 생성 ---
edges = []
weights = {}
for i in range(n_qubits):
    for j in range(i + 1, n_qubits):
        if rng.random() < 0.4:  # 40% 연결 확률
            w = rng.uniform(0.5, 2.0)
            edges.append((i, j))
            weights[(i, j)] = w

# 엣지가 없으면 최소 체인 구조 보장
if not edges:
    for i in range(n_qubits - 1):
        edges.append((i, i + 1))
        weights[(i, i + 1)] = 1.0

# --- Max-Cut 비용 함수 (클래식) ---
def maxcut_cost(bitstring, edges, weights):
    cost = 0
    for (i, j) in edges:
        bi = int(bitstring[n_qubits - 1 - i])
        bj = int(bitstring[n_qubits - 1 - j])
        if bi != bj:
            cost += weights[(i, j)]
    return cost

# --- 클래식 최적해 (brute force, 소규모) ---
best_cost = 0
for x in range(2**n_qubits):
    bs = format(x, f'0{n_qubits}b')
    c = maxcut_cost(bs, edges, weights)
    if c > best_cost:
        best_cost = c

# --- LR-QAOA 회로 (선형 램프 스케줄) ---
qc = QuantumCircuit(n_qubits, n_qubits)

# 초기 |+>^n
for q in range(n_qubits):
    qc.h(q)

# 선형 램프: gamma = (layer+1)/p * pi/4, beta = (1 - (layer+1)/p) * pi/4
for layer in range(p_layers):
    t = (layer + 1) / p_layers
    gamma = t * np.pi / 4
    beta = (1 - t) * np.pi / 4

    # Cost unitary: exp(-i * gamma * C)
    for (i, j) in edges:
        w = weights[(i, j)]
        qc.cx(i, j)
        qc.rz(2 * gamma * w, j)
        qc.cx(i, j)

    # Mixer unitary: exp(-i * beta * B)
    for q in range(n_qubits):
        qc.rx(2 * beta, q)

qc.measure(range(n_qubits), range(n_qubits))

# --- 실행 ---
sim = AerSimulator(method='statevector')
qc_t = transpile(qc, sim, optimization_level=1)
result = sim.run(qc_t, shots=shots).result()
counts = result.get_counts()

# 기대 비용 계산
total = sum(counts.values())
expected_cost = 0
best_sampled_cost = 0
best_sampled_bs = None
for bs, cnt in counts.items():
    c = maxcut_cost(bs, edges, weights)
    expected_cost += c * cnt
    if c > best_sampled_cost:
        best_sampled_cost = c
        best_sampled_bs = bs
expected_cost /= total

approx_ratio = expected_cost / best_cost if best_cost > 0 else 0
optimal_prob = 0
for bs, cnt in counts.items():
    if maxcut_cost(bs, edges, weights) == best_cost:
        optimal_prob += cnt
optimal_prob /= total

print(f"[LR-QAOA] n_qubits={n_qubits}, p_layers={p_layers}, edges={len(edges)}")
print(f"[LR-QAOA] Classical optimum: {best_cost:.4f}")
print(f"[LR-QAOA] Expected cost (QAOA): {expected_cost:.4f}")
print(f"[LR-QAOA] Approximation ratio: {approx_ratio:.4f}")
print(f"[LR-QAOA] Optimal sampling probability: {optimal_prob:.4f}")
print(f"[LR-QAOA] Circuit depth: {qc_t.depth()}, Gates: {sum(qc_t.count_ops().values())}")
