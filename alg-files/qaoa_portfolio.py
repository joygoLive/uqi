import cudaq
from cudaq import spin
from typing import List
import numpy as np

cudaq.set_target("nvidia")

n_assets = 4
budget = 2
q = 0.5
penalty = 10.0

mu = np.array([0.7, 0.5, 0.3, 0.1])
sigma = np.array([
    [0.09, 0.01, 0.02, 0.00],
    [0.01, 0.06, 0.01, 0.00],
    [0.02, 0.01, 0.04, 0.00],
    [0.00, 0.00, 0.00, 0.01]
])

pairs = [(i, j) for i in range(n_assets) for j in range(i+1, n_assets)]

z_coeffs = []
for i in range(n_assets):
    coeff = mu[i] / 2
    for j in range(n_assets):
        coeff -= q * sigma[i][j] / 2
    z_coeffs.append(coeff)

zz_coeffs = []
for i, j in pairs:
    zz_coeffs.append(q * sigma[i][j] / 4 + penalty / 2)

def build_hamiltonian():
    h = spin.z(0) * 0.0
    for i in range(n_assets):
        h += z_coeffs[i] * spin.z(i)
    for idx, (i, j) in enumerate(pairs):
        h += zz_coeffs[idx] * spin.z(i) * spin.z(j)
    return h

hamiltonian = build_hamiltonian()

layer_count = 5
# 파라미터: 레이어당 n_pairs(ZZ) + n_assets(Z단일) + n_assets(mixer) = 6+4+4 = 14
parameter_count = layer_count * (len(pairs) + n_assets + n_assets)

@cudaq.kernel
def kernel_qaoa(layer_count: int, thetas: List[float]):
    qvector = cudaq.qvector(4)
    h(qvector)
    for layer in range(layer_count):
        offset = layer * 14

        # ZZ 항: 각 엣지 개별 gamma
        x.ctrl(qvector[0], qvector[1])
        rz(2.0 * thetas[offset + 0], qvector[1])
        x.ctrl(qvector[0], qvector[1])

        x.ctrl(qvector[0], qvector[2])
        rz(2.0 * thetas[offset + 1], qvector[2])
        x.ctrl(qvector[0], qvector[2])

        x.ctrl(qvector[0], qvector[3])
        rz(2.0 * thetas[offset + 2], qvector[3])
        x.ctrl(qvector[0], qvector[3])

        x.ctrl(qvector[1], qvector[2])
        rz(2.0 * thetas[offset + 3], qvector[2])
        x.ctrl(qvector[1], qvector[2])

        x.ctrl(qvector[1], qvector[3])
        rz(2.0 * thetas[offset + 4], qvector[3])
        x.ctrl(qvector[1], qvector[3])

        x.ctrl(qvector[2], qvector[3])
        rz(2.0 * thetas[offset + 5], qvector[3])
        x.ctrl(qvector[2], qvector[3])

        # Z 단일 항: 각 큐비트 개별 rz
        rz(2.0 * thetas[offset + 6], qvector[0])
        rz(2.0 * thetas[offset + 7], qvector[1])
        rz(2.0 * thetas[offset + 8], qvector[2])
        rz(2.0 * thetas[offset + 9], qvector[3])

        # Mixer: 각 큐비트 개별 rx
        rx(2.0 * thetas[offset + 10], qvector[0])
        rx(2.0 * thetas[offset + 11], qvector[1])
        rx(2.0 * thetas[offset + 12], qvector[2])
        rx(2.0 * thetas[offset + 13], qvector[3])

def objective(parameters):
    return cudaq.observe(kernel_qaoa, hamiltonian, layer_count, parameters).expectation()

best_expectation = float('inf')
best_params = None

for seed in range(30):
    cudaq.set_random_seed(seed)
    np.random.seed(seed)
    optimizer = cudaq.optimizers.COBYLA()
    optimizer.initial_parameters = np.random.uniform(-np.pi/2, np.pi/2, parameter_count)
    try:
        exp, params = optimizer.optimize(dimensions=parameter_count, function=objective)
        if exp < best_expectation:
            best_expectation = exp
            best_params = params
            print(f"  [seed={seed}] E={exp:.4f}")
    except:
        pass

print(f"\nOptimal value = {best_expectation:.4f}")
print(f"브루트포스 최솟값 = -10.3725")

counts = cudaq.sample(kernel_qaoa, layer_count, best_params, shots_count=5000)
print("\n측정 결과 (상위 5개):")
for bits, count in sorted(counts.items(), key=lambda x: -x[1])[:5]:
    selected = [j for j, b in enumerate(bits) if b == '1']
    n_selected = len(selected)
    ret = sum(mu[j] for j in selected)
    risk = sum(sigma[i][j] for i in selected for j in selected)
    print(f"  {bits}: {count}회 | 선택자산{selected} | 수익률={ret:.2f} | 리스크={risk:.4f} | 예산충족={'O' if n_selected==budget else 'X'}")