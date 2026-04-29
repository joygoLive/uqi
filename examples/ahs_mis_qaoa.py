"""AHS (Analog Hamiltonian Simulation) — MIS / QAOA 샘플 알고리즘.

이 파일은 두 가지 변형을 같이 보여준다:
  1. ``ahs_program`` — Braket AHS (QuEra Aquila 용)  → ``from braket.ahs import``
  2. ``seq``         — pulser Sequence (Pasqal Fresnel 용) → ``import pulser``

UQI 의 AHS executor 가 algorithm_file 의 최상위 변수를 참조해 회로 객체를
추출하므로, 둘 다 정의해 두면 동일 .py 파일을 두 QPU 모두에 제출 가능하다.

문제: 1D unit-disk graph (5 atoms) 의 Maximum Independent Set (MIS).
    → Rydberg blockade radius 안의 인접 atom 은 동시에 |r⟩ 가 될 수 없음.
    → MIS = 비인접 atom 들의 최대 부분집합.
"""

# ─────────────────────────────────────────────────────────
# 공통: 5-atom 1D 체인 (간격 7 µm — Aquila/Fresnel 모두 호환 범위)
# ─────────────────────────────────────────────────────────

ATOM_SPACING_M  = 7e-6   # 7 µm
N_ATOMS         = 5
TOTAL_TIME_S    = 4e-6   # 4 µs
RABI_MAX_RAD_S  = 2 * 3.141592653589793 * 4e6   # 2π × 4 MHz


# ─────────────────────────────────────────────────────────
# (1) Braket AHS — QuEra Aquila
# ─────────────────────────────────────────────────────────
try:
    from braket.ahs import (
        AnalogHamiltonianSimulation, AtomArrangement, Hamiltonian,
        DrivingField, Field,
    )
    from braket.timings.time_series import TimeSeries

    # 1D atom register
    register = AtomArrangement()
    for i in range(N_ATOMS):
        register.add([i * ATOM_SPACING_M, 0.0])

    # Adiabatic Rabi sweep (0 → max → 0) + detuning sweep (-Δ → +Δ)
    times       = [0.0, 0.5e-6, 3.5e-6, TOTAL_TIME_S]
    amplitude   = TimeSeries()
    detuning    = TimeSeries()
    phase       = TimeSeries()
    for t, amp, det in zip(
        times,
        [0.0, RABI_MAX_RAD_S, RABI_MAX_RAD_S, 0.0],
        [-RABI_MAX_RAD_S, -RABI_MAX_RAD_S, RABI_MAX_RAD_S, RABI_MAX_RAD_S],
    ):
        amplitude.put(t, amp)
        detuning.put(t, det)
        phase.put(t, 0.0)

    drive = DrivingField(amplitude=amplitude, detuning=detuning, phase=phase)
    hamiltonian = Hamiltonian([drive])

    ahs_program = AnalogHamiltonianSimulation(register=register, hamiltonian=hamiltonian)
except Exception as _e:
    # braket sdk 미설치 환경 — pulser 변형만 제공
    ahs_program = None
    _braket_ahs_error = str(_e)


# ─────────────────────────────────────────────────────────
# (2) Pulser Sequence — Pasqal Fresnel
# ─────────────────────────────────────────────────────────
try:
    import pulser
    from pulser import Register, Sequence, Pulse
    from pulser.devices import AnalogDevice
    from pulser.waveforms import RampWaveform

    coords = {f"q{i}": (i * ATOM_SPACING_M * 1e6, 0.0) for i in range(N_ATOMS)}  # µm
    pulser_register = Register(coords)

    # AnalogDevice 는 Fresnel 호환 디바이스 prototype — register 검증 + Rydberg 채널 보유
    seq = Sequence(pulser_register, AnalogDevice)
    seq.declare_channel("ising", "rydberg_global")

    duration_ns = int(TOTAL_TIME_S * 1e9)
    rabi_max_2pi_mhz = RABI_MAX_RAD_S / (2 * 3.141592653589793) / 1e6

    rise_pulse = Pulse(
        amplitude=RampWaveform(500, 0.0, rabi_max_2pi_mhz),
        detuning =RampWaveform(500, -rabi_max_2pi_mhz, -rabi_max_2pi_mhz),
        phase=0.0,
    )
    plateau_pulse = Pulse(
        amplitude=RampWaveform(duration_ns - 1000, rabi_max_2pi_mhz, rabi_max_2pi_mhz),
        detuning =RampWaveform(duration_ns - 1000, -rabi_max_2pi_mhz,  rabi_max_2pi_mhz),
        phase=0.0,
    )
    fall_pulse = Pulse(
        amplitude=RampWaveform(500, rabi_max_2pi_mhz, 0.0),
        detuning =RampWaveform(500, rabi_max_2pi_mhz, rabi_max_2pi_mhz),
        phase=0.0,
    )
    seq.add(rise_pulse,    "ising")
    seq.add(plateau_pulse, "ising")
    seq.add(fall_pulse,    "ising")
except Exception as _e:
    seq = None
    _pulser_error = str(_e)
