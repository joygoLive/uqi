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
