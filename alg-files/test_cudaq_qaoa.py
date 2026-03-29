import cudaq
from cudaq import spin
import numpy as np

cudaq.set_target("qpp-cpu")

n_qubits = 8
n_layers = 3

@cudaq.kernel
def qaoa_kernel(gammas: list[float], betas: list[float], n: int, p: int):
    q = cudaq.qvector(n)
    # 초기 superposition
    h(q)
    for layer in range(p):
        # Cost layer (ZZ interactions)
        for i in range(n - 1):
            cx(q[i], q[i + 1])
            rz(2.0 * gammas[layer], q[i + 1])
            cx(q[i], q[i + 1])
        # Mixer layer
        for i in range(n):
            rx(2.0 * betas[layer], q[i])

gammas = [0.3, 0.5, 0.7]
betas  = [0.4, 0.6, 0.8]

counts = cudaq.sample(qaoa_kernel, gammas, betas, n_qubits, n_layers, shots_count=512)
