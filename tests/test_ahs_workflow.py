# test_ahs_workflow.py — AHS (Analog Hamiltonian Simulation) 통합 검증
#
# 검증 대상:
#   1. uqi_extractor 가 'Braket-AHS' / 'Pulser' framework 인식
#   2. _FRAMEWORK_QPU_MAP 에 두 framework 추가됨 (analog QPU 만 노출)
#   3. _resolve_qpu 가 AHS 코드 + auto/wrong_qpu → 적절한 analog QPU 로 보정
#   4. _analyze_ahs 헬퍼 — atom_count / register_dimension / total_duration_ns
#   5. _qpu_submit_ahs (confirmed=False) — 분석/예상 분기

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


_BRAKET_AHS_SAMPLE = '''
from braket.ahs import (
    AnalogHamiltonianSimulation, AtomArrangement, Hamiltonian, DrivingField,
)
from braket.timings.time_series import TimeSeries
register = AtomArrangement()
for i in range(3):
    register.add([i * 5e-6, 0.0])
amp = TimeSeries(); det = TimeSeries(); ph = TimeSeries()
for t, a, d in [(0, 0, -1e7), (1e-6, 1e7, -1e7), (3e-6, 1e7, 1e7), (4e-6, 0, 1e7)]:
    amp.put(t, a); det.put(t, d); ph.put(t, 0)
drive = DrivingField(amplitude=amp, detuning=det, phase=ph)
ahs_program = AnalogHamiltonianSimulation(
    register=register,
    hamiltonian=Hamiltonian([drive]),
)
'''

_PULSER_SAMPLE = '''
from pulser import Register, Sequence, Pulse
from pulser.devices import AnalogDevice
from pulser.waveforms import RampWaveform
register = Register({f"q{i}": (i * 7.0, 0.0) for i in range(3)})
seq = Sequence(register, AnalogDevice)
seq.declare_channel("ising", "rydberg_global")
seq.add(Pulse(
    amplitude=RampWaveform(500, 0.0, 5.0),
    detuning =RampWaveform(500, -5.0, -5.0),
    phase=0.0,
), "ising")
seq.add(Pulse(
    amplitude=RampWaveform(2500, 5.0, 5.0),
    detuning =RampWaveform(2500, -5.0, 5.0),
    phase=0.0,
), "ising")
seq.add(Pulse(
    amplitude=RampWaveform(500, 5.0, 0.0),
    detuning =RampWaveform(500, 5.0, 5.0),
    phase=0.0,
), "ising")
'''


def _write(content):
    fd, path = tempfile.mkstemp(suffix=".py", text=True)
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return path


def _clean(path):
    try: os.unlink(path)
    except Exception: pass


# ─────────────────────────────────────────────────────────
# Framework 인식 (uqi_extractor)
# ─────────────────────────────────────────────────────────

def test_TC500_detect_braket_ahs_framework():
    from uqi_extractor import UQIExtractor
    path = _write(_BRAKET_AHS_SAMPLE)
    try:
        ext = UQIExtractor(path)
        fw  = ext.detect_framework()
        assert fw == "Braket-AHS"
    finally:
        _clean(path)


def test_TC501_detect_pulser_framework():
    from uqi_extractor import UQIExtractor
    path = _write(_PULSER_SAMPLE)
    try:
        ext = UQIExtractor(path)
        fw  = ext.detect_framework()
        assert fw == "Pulser"
    finally:
        _clean(path)


def test_TC502_braket_ahs_has_priority_over_qiskit():
    """braket.ahs + qiskit import 동시 존재 시 Braket-AHS 가 우선 매칭"""
    from uqi_extractor import UQIExtractor
    src = "import qiskit\n" + _BRAKET_AHS_SAMPLE
    path = _write(src)
    try:
        ext = UQIExtractor(path)
        fw  = ext.detect_framework()
        assert fw == "Braket-AHS", "AHS 패턴이 우선순위에서 위에 있어야 함"
    finally:
        _clean(path)


# ─────────────────────────────────────────────────────────
# _FRAMEWORK_QPU_MAP 매핑 검증
# ─────────────────────────────────────────────────────────

def test_TC510_framework_map_braket_ahs_quera_only():
    from mcp_server import _FRAMEWORK_QPU_MAP
    m = _FRAMEWORK_QPU_MAP["Braket-AHS"]
    assert m["qpus"]    == ["quera_aquila"]
    assert m["default"] == "quera_aquila"


def test_TC511_framework_map_pulser_pasqal_only():
    from mcp_server import _FRAMEWORK_QPU_MAP
    m = _FRAMEWORK_QPU_MAP["Pulser"]
    assert "pasqal_fresnel"      in m["qpus"]
    assert "pasqal_fresnel_can1" in m["qpus"]
    assert m["default"]          == "pasqal_fresnel"


def test_TC512_resolve_qpu_braket_ahs_auto_to_quera():
    from mcp_server import _resolve_qpu
    path = _write(_BRAKET_AHS_SAMPLE)
    try:
        result = _resolve_qpu(path, "auto")
        assert result == "quera_aquila"
    finally:
        _clean(path)


def test_TC513_resolve_qpu_pulser_auto_to_pasqal():
    from mcp_server import _resolve_qpu
    path = _write(_PULSER_SAMPLE)
    try:
        result = _resolve_qpu(path, "auto")
        assert result == "pasqal_fresnel"
    finally:
        _clean(path)


def test_TC514_resolve_qpu_pulser_with_wrong_qpu_corrects():
    """Pulser 파일에 ibm_fez 선택 → pasqal_fresnel 자동 보정"""
    from mcp_server import _resolve_qpu
    path = _write(_PULSER_SAMPLE)
    try:
        result = _resolve_qpu(path, "ibm_fez")
        assert result == "pasqal_fresnel"
    finally:
        _clean(path)


# ─────────────────────────────────────────────────────────
# _analyze_ahs 메트릭 추출
# ─────────────────────────────────────────────────────────

def test_TC520_analyze_ahs_braket_extracts_atom_count():
    from mcp_server import _analyze_ahs
    path = _write(_BRAKET_AHS_SAMPLE)
    try:
        result = _analyze_ahs(path, "quera_aquila", "Braket-AHS")
        ahs = result["circuits"]["ahs_main"]["ahs"]
        assert ahs["atom_count"] == 3
        assert ahs["register_dimension"] in ("1D", "2D")
    finally:
        _clean(path)


def test_TC521_analyze_ahs_pulser_extracts_metrics():
    from mcp_server import _analyze_ahs
    path = _write(_PULSER_SAMPLE)
    try:
        result = _analyze_ahs(path, "pasqal_fresnel", "Pulser")
        ahs = result["circuits"]["ahs_main"]["ahs"]
        assert ahs["atom_count"] == 3
        assert ahs["total_duration_ns"] is not None
        assert ahs["total_duration_ns"] > 0
    finally:
        _clean(path)


# ─────────────────────────────────────────────────────────
# Executor _extract_*_program — 회로 객체 추출
# ─────────────────────────────────────────────────────────

def test_TC530_braket_executor_extracts_ahs_program():
    from uqi_executor_braket import UQIExecutorBraket
    path = _write(_BRAKET_AHS_SAMPLE)
    try:
        prog = UQIExecutorBraket._extract_ahs_program(path)
        assert prog is not None
        # AnalogHamiltonianSimulation 인스턴스
        from braket.ahs import AnalogHamiltonianSimulation
        assert isinstance(prog, AnalogHamiltonianSimulation)
    finally:
        _clean(path)


def test_TC531_azure_executor_extracts_pulser_sequence():
    from uqi_executor_azure import UQIExecutorAzure
    path = _write(_PULSER_SAMPLE)
    try:
        seq = UQIExecutorAzure._extract_pulser_sequence(path)
        assert seq is not None
        from pulser import Sequence
        assert isinstance(seq, Sequence)
    finally:
        _clean(path)


def test_TC532_braket_executor_missing_program_raises():
    from uqi_executor_braket import UQIExecutorBraket
    path = _write("from braket.ahs import AnalogHamiltonianSimulation\nx = 42\n")
    try:
        with pytest.raises(RuntimeError, match="ahs_program"):
            UQIExecutorBraket._extract_ahs_program(path)
    finally:
        _clean(path)


# ─────────────────────────────────────────────────────────
# _validate_ahs — Optimization step 의 AHS 대체 (device constraint)
# ─────────────────────────────────────────────────────────

def test_TC540_validate_ahs_braket_pass():
    """3-atom 1D Braket AHS — 모든 device constraint 통과"""
    import json as _json
    from mcp_server import _validate_ahs
    path = _write(_BRAKET_AHS_SAMPLE)
    try:
        result = _json.loads(_validate_ahs(path, "quera_aquila"))
        assert result["ok"] is True
        assert result["analog"] is True
        assert result["framework"] == "Braket-AHS"
        # 메트릭 확인
        m = result["metrics"]
        assert m["atom_count"] == 3
        assert m["min_spacing_um"] == 5.0   # 5e-6 m → 5.0 µm
        # checks
        names = [c["name"] for c in result["checks"]]
        assert "atom_count" in names
        assert "min_spacing" in names
    finally:
        _clean(path)


def test_TC541_validate_ahs_pulser_pass():
    """3-atom 1D Pulser — duration/build 통과"""
    import json as _json
    from mcp_server import _validate_ahs
    path = _write(_PULSER_SAMPLE)
    try:
        result = _json.loads(_validate_ahs(path, "pasqal_fresnel"))
        assert result["framework"] == "Pulser"
        assert result["analog"] is True
        m = result["metrics"]
        assert m["atom_count"] == 3
        assert m["duration_ns"] is not None and m["duration_ns"] > 0
    finally:
        _clean(path)


def test_TC542_validate_ahs_too_many_atoms_fails():
    """device max_atoms 초과 — atom_count check fail"""
    import json as _json
    from mcp_server import _validate_ahs
    # 1000-atom 체인 (max 100/256 초과)
    src = '''
from braket.ahs import (AnalogHamiltonianSimulation, AtomArrangement,
                        Hamiltonian, DrivingField)
from braket.timings.time_series import TimeSeries
register = AtomArrangement()
for i in range(1000):
    register.add([i * 5e-6, 0.0])
amp = TimeSeries(); det = TimeSeries(); ph = TimeSeries()
for t, a, d in [(0,0,-1e7),(1e-6,1e7,-1e7),(3e-6,1e7,1e7),(4e-6,0,1e7)]:
    amp.put(t,a); det.put(t,d); ph.put(t,0)
ahs_program = AnalogHamiltonianSimulation(
    register=register,
    hamiltonian=Hamiltonian([DrivingField(amplitude=amp, detuning=det, phase=ph)]),
)
'''
    path = _write(src)
    try:
        result = _json.loads(_validate_ahs(path, "quera_aquila"))
        # 1000 > 256 → atom_count check fail
        atom_check = next(c for c in result["checks"] if c["name"] == "atom_count")
        assert atom_check["passed"] is False
        assert result["ok"] is False
    finally:
        _clean(path)
