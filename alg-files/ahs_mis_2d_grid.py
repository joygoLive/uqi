"""AHS 예제 #2 — 2D 격자 (3×3) MIS

3×3 unit-disk 격자 위 9 atom 의 Maximum Independent Set.
Rydberg blockade 가 격자의 인접 atom 쌍을 동시 |r⟩ 가 못 되게 하므로,
adiabatic sweep 후 측정 분포의 peak 가 MIS 비트열에 집중된다.

  q0 — q1 — q2
   |    |    |
  q3 — q4 — q5
   |    |    |
  q6 — q7 — q8

이 파일도 Braket-AHS (ahs_program) + pulser (seq) 둘 다 정의 — 두 QPU 모두 호환.
"""

GRID_N        = 3                   # 3×3
SPACING_M     = 6e-6                # 6 µm — Aquila/Fresnel 둘 다 호환
TOTAL_TIME_S  = 4e-6
RABI_MAX      = 2 * 3.141592653589793 * 4e6   # 2π × 4 MHz


# ── (1) Braket AHS — QuEra Aquila ──
try:
    from braket.ahs import (
        AnalogHamiltonianSimulation, AtomArrangement, Hamiltonian, DrivingField,
    )
    from braket.timings.time_series import TimeSeries

    register = AtomArrangement()
    for r in range(GRID_N):
        for c in range(GRID_N):
            register.add([c * SPACING_M, r * SPACING_M])

    times = [0.0, 0.5e-6, 3.5e-6, TOTAL_TIME_S]
    amp, det, ph = TimeSeries(), TimeSeries(), TimeSeries()
    for t, a, d in zip(times,
                       [0.0, RABI_MAX, RABI_MAX, 0.0],
                       [-RABI_MAX, -RABI_MAX, RABI_MAX, RABI_MAX]):
        amp.put(t, a); det.put(t, d); ph.put(t, 0.0)
    drive = DrivingField(amplitude=amp, detuning=det, phase=ph)
    ahs_program = AnalogHamiltonianSimulation(
        register=register,
        hamiltonian=Hamiltonian([drive]),
    )
except Exception:
    ahs_program = None


# ── (2) Pulser — Pasqal Fresnel ──
try:
    from pulser import Register, Sequence, Pulse
    from pulser.devices import AnalogDevice
    from pulser.waveforms import RampWaveform

    coords = {
        f"q{r*GRID_N+c}": (c * SPACING_M * 1e6, r * SPACING_M * 1e6)   # µm
        for r in range(GRID_N) for c in range(GRID_N)
    }
    pulser_register = Register(coords)
    seq = Sequence(pulser_register, AnalogDevice)
    seq.declare_channel("ising", "rydberg_global")

    duration_ns = int(TOTAL_TIME_S * 1e9)
    rabi_2pi_mhz = RABI_MAX / (2 * 3.141592653589793) / 1e6

    seq.add(Pulse(
        amplitude=RampWaveform(500, 0.0, rabi_2pi_mhz),
        detuning =RampWaveform(500, -rabi_2pi_mhz, -rabi_2pi_mhz), phase=0.0,
    ), "ising")
    seq.add(Pulse(
        amplitude=RampWaveform(duration_ns - 1000, rabi_2pi_mhz, rabi_2pi_mhz),
        detuning =RampWaveform(duration_ns - 1000, -rabi_2pi_mhz, rabi_2pi_mhz), phase=0.0,
    ), "ising")
    seq.add(Pulse(
        amplitude=RampWaveform(500, rabi_2pi_mhz, 0.0),
        detuning =RampWaveform(500, rabi_2pi_mhz, rabi_2pi_mhz), phase=0.0,
    ), "ising")
except Exception:
    seq = None
