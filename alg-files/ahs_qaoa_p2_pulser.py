"""AHS 예제 #3 — QAOA p=2 (4-atom 1D, Pasqal pulser 전용)

QAOA 의 mixer/cost 를 두 번 (p=2) 적용 — adiabatic 보다 깊은 회로.
4 atom 1D 체인의 antiferromagnetic ground state 추정.

이 파일은 Pasqal Fresnel 전용 (`from pulser import` 만). braket.ahs 미사용 →
extractor 가 'Pulser' framework 만 감지 → Target QPU 셀렉터에 pasqal_fresnel
계열만 자동 노출.
"""

from pulser import Register, Sequence, Pulse
from pulser.devices import AnalogDevice
from pulser.waveforms import ConstantWaveform, RampWaveform


N_ATOMS  = 4
SPACING  = 7.0   # µm
RABI_MHZ = 5.0   # 2π × 5 MHz


coords = {f"q{i}": (i * SPACING, 0.0) for i in range(N_ATOMS)}
register = Register(coords)
seq = Sequence(register, AnalogDevice)
seq.declare_channel("ising", "rydberg_global")


# QAOA p=2 — cost (detuning) + mixer (Rabi) 교대 적용
def _layer(t_cost_ns: int, t_mixer_ns: int):
    # cost: detuning on, amplitude=0 (대각 phase 진화)
    seq.add(Pulse(
        amplitude=ConstantWaveform(t_cost_ns,  0.0),
        detuning =ConstantWaveform(t_cost_ns, -RABI_MHZ),
        phase=0.0,
    ), "ising")
    # mixer: amplitude on, detuning=0 (X-basis rotation)
    seq.add(Pulse(
        amplitude=ConstantWaveform(t_mixer_ns, RABI_MHZ),
        detuning =ConstantWaveform(t_mixer_ns, 0.0),
        phase=0.0,
    ), "ising")


# p=2 layers — durations (ns) 는 prototype 값 (variational tuning 대상)
_layer(t_cost_ns=400, t_mixer_ns=300)
_layer(t_cost_ns=300, t_mixer_ns=200)
