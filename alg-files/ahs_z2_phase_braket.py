"""AHS 예제 #4 — Z₂ symmetry-broken phase (1D, Braket AHS · QuEra Aquila 전용)

1D Rydberg 체인의 Z₂ phase (|01010...⟩ 또는 |10101...⟩) adiabatic 준비.
Bernien et al. (Nature 2017) — neutral-atom Hamiltonian simulation 의 정석 예제.

이 파일은 QuEra Aquila 전용 (`from braket.ahs import` 만). pulser 미사용 →
extractor 가 'Braket-AHS' framework 만 감지 → Target QPU 셀렉터에
quera_aquila 만 자동 노출.

References:
  Bernien et al., "Probing many-body dynamics on a 51-atom quantum simulator",
  Nature 551, 579 (2017).
"""

from braket.ahs import (
    AnalogHamiltonianSimulation, AtomArrangement, Hamiltonian, DrivingField,
)
from braket.timings.time_series import TimeSeries


N_ATOMS       = 9               # 홀수 atom (Z₂ symmetry breaking 명확)
SPACING_M     = 5.5e-6          # 5.5 µm — Rydberg blockade 인접 atom 만 적용
TOTAL_TIME_S  = 4e-6
RABI_MAX      = 2 * 3.141592653589793 * 5e6   # 2π × 5 MHz


# 1D 체인
register = AtomArrangement()
for i in range(N_ATOMS):
    register.add([i * SPACING_M, 0.0])


# Adiabatic sweep:
#   Δ: -Δ_max → +Δ_max  (ground state |0...0⟩ → |0101...⟩ 의 Z₂ symmetry-broken)
#   Ω: 0 → Ω_max → 0     (smooth start/end)
times       = [0.0, 0.5e-6, 3.5e-6, TOTAL_TIME_S]
amplitude   = TimeSeries()
detuning    = TimeSeries()
phase       = TimeSeries()
for t, a, d in zip(times,
                   [0.0,        RABI_MAX, RABI_MAX, 0.0],
                   [-RABI_MAX, -RABI_MAX, RABI_MAX, RABI_MAX]):
    amplitude.put(t, a)
    detuning.put(t, d)
    phase.put(t, 0.0)


drive       = DrivingField(amplitude=amplitude, detuning=detuning, phase=phase)
hamiltonian = Hamiltonian([drive])

ahs_program = AnalogHamiltonianSimulation(
    register=register,
    hamiltonian=hamiltonian,
)
