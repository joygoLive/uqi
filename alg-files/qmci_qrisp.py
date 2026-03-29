from qrisp import *


def function(qf):
    return qf * qf


def distribution(qf):
    h(qf)


qf_x = QuantumFloat(3, -3)
qf_y = QuantumFloat(6, -6)

qbl = QuantumBool()

@auto_uncompute
def state_function(qf_x, qf_y, qbl):

    distribution(qf_x)
    h(qf_y)

    with qf_y < function(qf_x):
        x(qbl)

a = IQAE([qf_x, qf_y, qbl], state_function, eps=0.1, alpha=0.01)

print(a)

N = 8
print(sum((i / N) ** 2 for i in range(N)) / N)