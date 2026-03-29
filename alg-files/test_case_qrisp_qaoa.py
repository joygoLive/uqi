
# Qrisp — QAOA MaxCut (5노드 그래프)
from qrisp import QuantumVariable, h, cx, rz, rx, measure
from qrisp.qaoa import QAOAProblem, RZZGate, RX_mixer, create_maxcut_cl_cost_function, create_maxcut_cost_operator
import numpy as np

# 5노드 그래프
edges = [(0,1),(1,2),(2,3),(3,4),(4,0),(0,2)]

def maxcut_cost_op(edges):
    def cost_op(qv, gamma):
        for u, v in edges:
            cx(qv[u], qv[v])
            rz(2*gamma, qv[v])
            cx(qv[u], qv[v])
    return cost_op

def mixer_op(qv, beta):
    for i in range(len(qv)):
        rx(2*beta, qv[i])

n = 5
qv = QuantumVariable(n)
h(qv)

gamma, beta = 0.5, 0.3
maxcut_cost_op(edges)(qv, gamma)
mixer_op(qv, beta)

result = qv.get_measurement()
print("QAOA result:", result)
