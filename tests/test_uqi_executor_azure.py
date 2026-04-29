# test_uqi_executor_azure.py — Azure Quantum executor 단위 테스트

import os
import sys
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))


# ─────────────────────────────────────────────────────────────
# Mock 환경
# ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _mock_env(monkeypatch):
    monkeypatch.setenv("AZURE_TENANT_ID",                "test-tenant")
    monkeypatch.setenv("AZURE_CLIENT_ID",                "test-client")
    monkeypatch.setenv("AZURE_CLIENT_SECRET",            "test-secret")
    monkeypatch.setenv("AZURE_QUANTUM_SUBSCRIPTION_ID",  "00000000-0000-0000-0000-000000000000")
    monkeypatch.setenv("AZURE_QUANTUM_RESOURCE_GROUP",   "uqi")
    monkeypatch.setenv("AZURE_QUANTUM_WORKSPACE",        "orientom")
    monkeypatch.setenv("AZURE_QUANTUM_LOCATION",         "westus")


# ─────────────────────────────────────────────────────────────
# 매핑 무결성
# ─────────────────────────────────────────────────────────────

def test_TC001_target_map_pasqal_only():
    """정책: Pasqal Fresnel 실 QPU만, 시뮬레이터/Quantinuum 제외"""
    from uqi_executor_azure import _AZURE_TARGET_MAP
    assert "pasqal_fresnel" in _AZURE_TARGET_MAP
    assert "pasqal_fresnel_can1" in _AZURE_TARGET_MAP
    # 시뮬레이터/Quantinuum 제외 확인
    assert "pasqal_emu_tn" not in _AZURE_TARGET_MAP
    assert "pasqal_emu_mps" not in _AZURE_TARGET_MAP
    assert "pasqal_emu_sv" not in _AZURE_TARGET_MAP
    assert "quantinuum_h2_1sc" not in _AZURE_TARGET_MAP
    assert "quantinuum_h2_1e" not in _AZURE_TARGET_MAP

def test_TC002_target_map_correct_format():
    from uqi_executor_azure import _AZURE_TARGET_MAP
    assert _AZURE_TARGET_MAP["pasqal_fresnel"] == "pasqal.qpu.fresnel"
    assert _AZURE_TARGET_MAP["pasqal_fresnel_can1"] == "pasqal.qpu.fresnel-can1"


# ─────────────────────────────────────────────────────────────
# UQIExecutorAzure._resolve_target
# ─────────────────────────────────────────────────────────────

class _MockExtractor:
    tapes = sessions = circuits = observables = {}

class _MockConverter:
    extractor = _MockExtractor()
    qir_results = qasm_results = {}


def test_TC010_resolve_target_pasqal():
    from uqi_executor_azure import UQIExecutorAzure
    ex = UQIExecutorAzure(_MockConverter(), shots=10)
    assert ex._resolve_target("pasqal_fresnel") == "pasqal.qpu.fresnel"

def test_TC011_resolve_target_passthrough():
    """Azure target 형식 ('xxx.yyy.zzz') 직접 입력도 허용"""
    from uqi_executor_azure import UQIExecutorAzure
    ex = UQIExecutorAzure(_MockConverter(), shots=10)
    assert ex._resolve_target("ionq.simulator") == "ionq.simulator"

def test_TC012_resolve_target_unknown_raises():
    from uqi_executor_azure import UQIExecutorAzure
    ex = UQIExecutorAzure(_MockConverter(), shots=10)
    with pytest.raises(RuntimeError):
        ex._resolve_target("unknown_target")


# ─────────────────────────────────────────────────────────────
# check_device_availability_azure — 매핑 없는 경우
# ─────────────────────────────────────────────────────────────

def test_TC020_availability_unknown_target():
    from uqi_executor_azure import check_device_availability_azure
    r = check_device_availability_azure("unknown_qpu")
    assert "Azure target 매핑 없음" in r["message"]
    assert r["available_now"] is None


# ─────────────────────────────────────────────────────────────
# check_device_availability_azure — Mock된 Azure SDK 호출
# ─────────────────────────────────────────────────────────────

def test_TC030_availability_available_status():
    """Available 상태 처리 검증"""
    fake_target = MagicMock()
    fake_target.current_availability = "TargetAvailability.AVAILABLE"
    fake_target.average_queue_time = 0
    fake_target.input_data_format = "pasqal.pulser.v1"

    fake_ws = MagicMock()
    fake_ws.get_targets = MagicMock(return_value=fake_target)

    with patch("azure.quantum.Workspace", return_value=fake_ws), \
         patch("azure.identity.ClientSecretCredential"):
        from uqi_executor_azure import check_device_availability_azure
        r = check_device_availability_azure("pasqal_fresnel")

    assert r["device_status"] == "AVAILABLE"
    assert r["available_now"] is True
    assert "가용" in r["message"]

def test_TC031_availability_degraded():
    """Degraded 상태는 보수적으로 차단"""
    fake_target = MagicMock()
    fake_target.current_availability = "TargetAvailability.DEGRADED"
    fake_target.average_queue_time = 0
    fake_target.input_data_format = "pasqal.pulser.v1"

    fake_ws = MagicMock()
    fake_ws.get_targets = MagicMock(return_value=fake_target)

    with patch("azure.quantum.Workspace", return_value=fake_ws), \
         patch("azure.identity.ClientSecretCredential"):
        from uqi_executor_azure import check_device_availability_azure
        r = check_device_availability_azure("pasqal_fresnel")

    assert r["device_status"] == "DEGRADED"
    assert r["available_now"] is False
    assert "Degraded" in r["message"]

def test_TC032_availability_pulser_warning():
    """Pulser 입력 형식이면 경고 표시 (Qiskit gate 비호환)"""
    fake_target = MagicMock()
    fake_target.current_availability = "TargetAvailability.AVAILABLE"
    fake_target.average_queue_time = 0
    fake_target.input_data_format = "pasqal.pulser.v1"

    fake_ws = MagicMock()
    fake_ws.get_targets = MagicMock(return_value=fake_target)

    with patch("azure.quantum.Workspace", return_value=fake_ws), \
         patch("azure.identity.ClientSecretCredential"):
        from uqi_executor_azure import check_device_availability_azure
        r = check_device_availability_azure("pasqal_fresnel")

    pulser_warning = any("Pulser" in w or "pulser" in w for w in r["warnings"])
    assert pulser_warning

def test_TC033_availability_queue_time_extracted():
    """큐 대기시간 정확히 추출"""
    fake_target = MagicMock()
    fake_target.current_availability = "TargetAvailability.AVAILABLE"
    fake_target.average_queue_time = 1234
    fake_target.input_data_format = "pasqal.pulser.v1"

    fake_ws = MagicMock()
    fake_ws.get_targets = MagicMock(return_value=fake_target)

    with patch("azure.quantum.Workspace", return_value=fake_ws), \
         patch("azure.identity.ClientSecretCredential"):
        from uqi_executor_azure import check_device_availability_azure
        r = check_device_availability_azure("pasqal_fresnel")

    assert r["average_queue_time_sec"] == 1234


# ─────────────────────────────────────────────────────────────
# UQIExecutorAzure 인스턴스
# ─────────────────────────────────────────────────────────────

def test_TC040_executor_instantiate():
    from uqi_executor_azure import UQIExecutorAzure
    ex = UQIExecutorAzure(_MockConverter(), shots=100)
    assert ex.shots == 100
    assert ex.results == {}

def test_TC041_executor_default_shots():
    from uqi_executor_azure import UQIExecutorAzure
    ex = UQIExecutorAzure(_MockConverter())
    assert ex.shots == 1024


# ─────────────────────────────────────────────────────────────
# _adapt_sequence_to_device — backend 자동 layout 매핑
# (사용자 algorithm 파일 미수정 — Fresnel(-CAN1) 호환 register 자동 생성)
# ─────────────────────────────────────────────────────────────

def _build_pulser_seq():
    """3-atom 1D Sequence (좌표 dict register, AnalogDevice prototype)."""
    from pulser import Register, Sequence, Pulse
    from pulser.devices import AnalogDevice
    from pulser.waveforms import RampWaveform
    coords = {f"q{i}": (i * 7.0, 0.0) for i in range(3)}
    seq = Sequence(Register(coords), AnalogDevice)
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
    return seq


def test_TC050_adapt_sequence_preserves_operations():
    """_adapt_sequence_to_device — operations 개수와 종류 보존"""
    import json as _json
    from pulser.devices import AnalogDevice
    from uqi_executor_azure import UQIExecutorAzure
    seq     = _build_pulser_seq()
    new_seq = UQIExecutorAzure._adapt_sequence_to_device(seq, AnalogDevice)
    a = _json.loads(seq.to_abstract_repr())
    b = _json.loads(new_seq.to_abstract_repr())
    # operations 개수 동일
    assert len(a.get("operations", [])) == len(b.get("operations", []))
    # 모두 pulse op
    assert all(op.get("op") == "pulse" for op in b.get("operations", []))
    # 채널 매핑 동일
    assert a.get("channels") == b.get("channels")


def test_TC051_adapt_sequence_uses_layout_register():
    """_adapt_sequence_to_device — 새 register 가 layout-based (trap idx)"""
    import json as _json
    from pulser.devices import AnalogDevice
    from uqi_executor_azure import UQIExecutorAzure
    seq     = _build_pulser_seq()
    new_seq = UQIExecutorAzure._adapt_sequence_to_device(seq, AnalogDevice)
    abr = _json.loads(new_seq.to_abstract_repr())
    # abstract_repr 최상위에 layout 항목 존재 (좌표 dict register 와 다름)
    assert abr.get("layout") is not None
    # 새 register 의 qubit 개수 동일
    assert len(new_seq.register.qubits) == len(seq.register.qubits)


def test_TC052_adapt_sequence_nearest_trap_unique():
    """_adapt_sequence_to_device — 각 atom 이 서로 다른 trap 으로 매핑됨"""
    from pulser.devices import AnalogDevice
    from uqi_executor_azure import UQIExecutorAzure
    seq      = _build_pulser_seq()
    new_seq  = UQIExecutorAzure._adapt_sequence_to_device(seq, AnalogDevice)
    # 새 register 좌표들이 모두 distinct (AbstractArray → float tuple)
    new_coords = [(float(c[0]), float(c[1]))
                  for c in new_seq.register.qubits.values()]
    assert len(set(new_coords)) == len(new_coords), \
        "각 사용자 atom 은 고유 trap 으로 매핑되어야 함"


def test_TC053_submit_ahs_no_pasqal_creds_skips_swap(monkeypatch):
    """_submit_single_ahs — PASQAL_* 환경변수 없으면 PasqalCloud import 시도 X"""
    from uqi_executor_azure import UQIExecutorAzure
    # PASQAL_* env 클리어
    for k in ("PASQAL_USERNAME", "PASQAL_PASSWORD", "PASQAL_PROJECT_ID"):
        monkeypatch.delenv(k, raising=False)
    ex = UQIExecutorAzure(_MockConverter(), shots=10)

    # _extract_pulser_sequence + Pasqal target 모킹
    with patch.object(UQIExecutorAzure, "_extract_pulser_sequence",
                      return_value=MagicMock(to_abstract_repr=lambda: '{"x":1}')), \
         patch("uqi_executor_azure.UQIExecutorAzure._get_workspace",
               return_value=MagicMock()), \
         patch("azure.quantum.target.pasqal.Pasqal") as mock_pasqal_cls, \
         patch("azure.quantum.target.pasqal.InputParams"):
        mock_target = MagicMock()
        mock_job = MagicMock(); mock_job.id = "fake-job-id"
        mock_target.submit.return_value = mock_job
        mock_pasqal_cls.return_value = mock_target

        r = ex._submit_single_ahs("test", "/tmp/dummy.py", "pasqal_fresnel")

    # PasqalCloud 분기 skip → ok=True 도달 (실 API 미호출)
    assert r["ok"] is True
    assert r["job_id"] == "fake-job-id"


def test_TC054_submit_ahs_swap_failure_falls_back(monkeypatch):
    """_submit_single_ahs — Pasqal device swap 실패 시 prototype Sequence 그대로 진행"""
    from uqi_executor_azure import UQIExecutorAzure
    monkeypatch.setenv("PASQAL_USERNAME",   "u")
    monkeypatch.setenv("PASQAL_PASSWORD",   "p")
    monkeypatch.setenv("PASQAL_PROJECT_ID", "pid")
    ex = UQIExecutorAzure(_MockConverter(), shots=10)

    fake_seq = MagicMock(to_abstract_repr=lambda: '{"x":1}')

    # PasqalCloud import 자체는 성공하지만 fetch 가 raise → except 분기
    fake_cloud_mod = MagicMock()
    fake_cloud_mod.PasqalCloud.side_effect = RuntimeError("auth fail")

    with patch.dict("sys.modules", {"pulser_pasqal": fake_cloud_mod}), \
         patch.object(UQIExecutorAzure, "_extract_pulser_sequence",
                      return_value=fake_seq), \
         patch("uqi_executor_azure.UQIExecutorAzure._get_workspace",
               return_value=MagicMock()), \
         patch("azure.quantum.target.pasqal.Pasqal") as mock_pasqal_cls, \
         patch("azure.quantum.target.pasqal.InputParams"):
        mock_target = MagicMock()
        mock_job = MagicMock(); mock_job.id = "fb-job-id"
        mock_target.submit.return_value = mock_job
        mock_pasqal_cls.return_value = mock_target

        r = ex._submit_single_ahs("test", "/tmp/dummy.py", "pasqal_fresnel_can1")

    # swap 실패해도 submit 자체는 prototype seq 로 진행
    assert r["ok"] is True
    assert r["job_id"] == "fb-job-id"
