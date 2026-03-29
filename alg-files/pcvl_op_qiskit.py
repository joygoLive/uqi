"""
Multi-Asset Quantum Finance (Qiskit)
Perceval pcvl_op.py와 논리적으로 등가인 Qiskit 구현

Perceval 대응:
  - BS(theta) + single photon dual-rail  →  RY(theta) + 1 qubit
  - |1,0> → |0>, |0,1> → |1> (상승)
  - Asset A, B 독립 실행 후 결합 확률 계산

Usage:
  python qmc_qiskit_multiasset.py              # 로컬 시뮬레이터
  python qc_executor.py -f qmc_qiskit_multiasset.py  # IBM/IQM QPU
"""

import numpy as np
from qiskit import QuantumCircuit
from qiskit.primitives import StatevectorSampler


def build_asset_circuit(theta: float) -> QuantumCircuit:
    """
    단일 자산 확률 추정 회로.
    Perceval BS(theta) + BasicState([1,0]) 와 등가:
      RY(2*theta) 적용 후 측정 → |1> 확률 = sin²(theta)
    """
    qc = QuantumCircuit(1, 1)
    qc.ry(2 * theta, 0)
    qc.measure(0, 0)
    return qc


def run_asset(sampler: StatevectorSampler, theta: float, shots: int) -> float:
    """회로 실행 후 |1> (자산 상승) 확률 반환"""
    qc = build_asset_circuit(theta)
    job = sampler.run([(qc,)], shots=shots)
    result = job.result()[0]

    counts = result.data.c.get_counts()
    up_count = counts.get('1', 0)
    return up_count / shots


def run_simulation(p_a: float = 0.7, p_b: float = 0.5, shots: int = 10000):
    theta_a = np.arcsin(np.sqrt(p_a))
    theta_b = np.arcsin(np.sqrt(p_b))

    sampler = StatevectorSampler()

    print("   Asset A 실행 중...")
    prob_a = run_asset(sampler, theta_a, shots)

    print("   Asset B 실행 중...")
    prob_b = run_asset(sampler, theta_b, shots)

    price = prob_a * prob_b
    return price, prob_a, prob_b


if __name__ == "__main__":
    P_A, P_B = 0.7, 0.5
    P_THEORY = P_A * P_B
    SHOTS = 10000

    price, prob_a, prob_b = run_simulation(p_a=P_A, p_b=P_B, shots=SHOTS)

    print("\n" + "=" * 45)
    print(f"📊 QISKIT 다중 자산 결과")
    print("-" * 45)
    print(f"1. 알고리즘 정확도 분석")
    print(f"   - Asset A 측정 확률 : {prob_a:.4f}  (이론: {P_A:.4f})")
    print(f"   - Asset B 측정 확률 : {prob_b:.4f}  (이론: {P_B:.4f})")
    print(f"   - 결합 확률 (A×B)  : {price:.4f}  (이론: {P_THEORY:.4f})")
    print(f"   - 절대 오차        : {abs(P_THEORY - price):.4f}")
    print("-" * 45)
    print(f"2. 하드웨어 자원 복잡도")
    print(f"   - 큐비트 수        : 1 (per asset)")
    print(f"   - 게이트 수        : 1 RY (per asset)")
    print(f"   - 측정 횟수        : 2 (Asset A + B)")
    print("=" * 45)