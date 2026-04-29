"""AHS minimal validation — 가장 저렴한 비용으로 전체 path 검증용.

3-atom 1D 체인, 1 µs duration. Pasqal Fresnel(-CAN1) free quota / QuEra Aquila
최소 비용 (3 atoms + 100 shots ≈ $1.30) 으로 submit/poll/result 흐름 1회 검증.
"""

# 공통 파라미터
N_ATOMS = 3
SPACING_M = 7e-6              # 7 µm
DURATION_S = 1e-6             # 1 µs (최소)
RABI_2PI_MHZ = 2.0            # 2π × 2 MHz (Aquila max amplitude 15.8 Mrad/s ≈ 2.5 MHz × 2π)


# ── (1) Braket AHS — QuEra Aquila ──
try:
    from braket.ahs import (
        AnalogHamiltonianSimulation, AtomArrangement, Hamiltonian, DrivingField,
    )
    from braket.timings.time_series import TimeSeries

    register = AtomArrangement()
    for i in range(N_ATOMS):
        register.add([i * SPACING_M, 0.0])

    rabi_rad_s = 2 * 3.141592653589793 * RABI_2PI_MHZ * 1e6
    times = [0.0, 0.1e-6, 0.9e-6, DURATION_S]
    amp, det, ph = TimeSeries(), TimeSeries(), TimeSeries()
    for t, a, d in zip(times,
                       [0.0, rabi_rad_s, rabi_rad_s, 0.0],
                       [-rabi_rad_s, -rabi_rad_s, rabi_rad_s, rabi_rad_s]):
        amp.put(t, a); det.put(t, d); ph.put(t, 0.0)
    drive = DrivingField(amplitude=amp, detuning=det, phase=ph)

    ahs_program = AnalogHamiltonianSimulation(
        register=register,
        hamiltonian=Hamiltonian([drive]),
    )
except Exception:
    ahs_program = None


# ── (2) Pulser — Pasqal Fresnel(-CAN1) ──
try:
    from pulser import Register, Sequence, Pulse
    from pulser.devices import AnalogDevice
    from pulser.waveforms import RampWaveform

    coords = {f"q{i}": (i * SPACING_M * 1e6, 0.0) for i in range(N_ATOMS)}
    pulser_register = Register(coords)
    seq = Sequence(pulser_register, AnalogDevice)
    seq.declare_channel("ising", "rydberg_global")

    duration_ns = int(DURATION_S * 1e9)
    seq.add(Pulse(
        amplitude=RampWaveform(100, 0.0, RABI_2PI_MHZ),
        detuning =RampWaveform(100, -RABI_2PI_MHZ, -RABI_2PI_MHZ),
        phase=0.0,
    ), "ising")
    seq.add(Pulse(
        amplitude=RampWaveform(duration_ns - 200, RABI_2PI_MHZ, RABI_2PI_MHZ),
        detuning =RampWaveform(duration_ns - 200, -RABI_2PI_MHZ, RABI_2PI_MHZ),
        phase=0.0,
    ), "ising")
    seq.add(Pulse(
        amplitude=RampWaveform(100, RABI_2PI_MHZ, 0.0),
        detuning =RampWaveform(100, RABI_2PI_MHZ, RABI_2PI_MHZ),
        phase=0.0,
    ), "ising")
except Exception:
    seq = None
