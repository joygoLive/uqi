from qrisp import QuantumVariable, h, rz, cx, rx
import networkx as nx
G = nx.erdos_renyi_graph(8, 0.6, seed=42)
qv = QuantumVariable(8)
p = 3
gammas = [0.3, 0.5, 0.7]
betas  = [0.4, 0.6, 0.8]
h(qv)
for layer in range(p):
    for u, v in G.edges():
        cx(qv[u], qv[v])
        rz(2 * gammas[layer], qv[v])
        cx(qv[u], qv[v])
    for i in range(8):
        rx(2 * betas[layer], qv[i])