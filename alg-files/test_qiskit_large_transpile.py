# test_qiskit_large_transpile.py
# IBM transpile 후 가상 큐비트 팽창 유발용 테스트 파일
# 156q 가상 할당 재현 목적: 깊은 depth + 많은 2Q 게이트

from qiskit import QuantumCircuit
from qiskit.circuit.library import QFT
import numpy as np

# 20큐비트 QFT — transpile 후 가상 큐비트 대량 할당 유발
qc = QuantumCircuit(20)
qft = QFT(20, do_swaps=True)
qc.compose(qft, inplace=True)
qc.measure_all()

# 두 번째 회로: 랜덤 깊은 레이어
qc2 = QuantumCircuit(15)
for i in range(10):
    for j in range(14):
        qc2.cx(j, (j+1) % 15)
    for j in range(15):
        qc2.rz(np.pi / (i+1), j)
qc2.measure_all()