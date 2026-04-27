# test_uqi_executor_braket.py — Braket executor + 가용성 체크 단위 테스트

import os
import sys
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))


# ─────────────────────────────────────────────────────────────
# 테스트 환경: AWS/IonQ ARN 환경변수 mock
# ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _mock_env(monkeypatch):
    """모든 테스트에서 AWS 환경변수 자동 설정"""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID",      "test-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY",  "test-secret")
    monkeypatch.setenv("IONQ_FORTE_ARN",
                       "arn:aws:braket:us-east-1::device/qpu/ionq/Forte-1")
    monkeypatch.setenv("RIGETTI_CEPHEUS_ARN",
                       "arn:aws:braket:us-west-1::device/qpu/rigetti/Cepheus-1-108Q")
    monkeypatch.setenv("QuEra_Aquila_ARN",
                       "arn:aws:braket:us-east-1::device/qpu/quera/Aquila")


# ─────────────────────────────────────────────────────────────
# _matches_day helper
# ─────────────────────────────────────────────────────────────

def test_TC001_matches_day_everyday():
    from uqi_executor_braket import _matches_day
    for wd in range(7):
        assert _matches_day("ExecutionDay.EVERYDAY", wd) is True

def test_TC002_matches_day_weekdays():
    from uqi_executor_braket import _matches_day
    for wd in range(5):
        assert _matches_day("WEEKDAYS", wd) is True
    for wd in (5, 6):
        assert _matches_day("WEEKDAYS", wd) is False

def test_TC003_matches_day_weekend():
    from uqi_executor_braket import _matches_day
    for wd in range(5):
        assert _matches_day("WEEKEND", wd) is False
    for wd in (5, 6):
        assert _matches_day("WEEKEND", wd) is True

def test_TC004_matches_day_specific():
    from uqi_executor_braket import _matches_day
    assert _matches_day("MONDAY", 0) is True
    assert _matches_day("MONDAY", 1) is False
    assert _matches_day("ExecutionDay.SUNDAY", 6) is True
    assert _matches_day("ExecutionDay.SUNDAY", 0) is False


# ─────────────────────────────────────────────────────────────
# 매핑 무결성
# ─────────────────────────────────────────────────────────────

def test_TC010_braket_qpu_map_keys():
    from uqi_executor_braket import _BRAKET_QPU_MAP
    assert "ionq_forte1" in _BRAKET_QPU_MAP
    assert "rigetti_cepheus" in _BRAKET_QPU_MAP
    # retire된 항목 제거 확인
    assert "ionq_aria1" not in _BRAKET_QPU_MAP
    assert "rigetti_ankaa3" not in _BRAKET_QPU_MAP

def test_TC011_braket_sim_map_keys():
    from uqi_executor_braket import _BRAKET_SIM_MAP
    assert "braket_sv1" in _BRAKET_SIM_MAP
    assert "braket_dm1" in _BRAKET_SIM_MAP
    assert "braket_tn1" in _BRAKET_SIM_MAP

def test_TC012_quera_in_other_map():
    from uqi_executor_braket import _BRAKET_OTHER_MAP
    assert "quera_aquila" in _BRAKET_OTHER_MAP

def test_TC013_braket_qpu_map_region():
    from uqi_executor_braket import _BRAKET_QPU_MAP
    assert _BRAKET_QPU_MAP["ionq_forte1"][1] == "us-east-1"
    assert _BRAKET_QPU_MAP["rigetti_cepheus"][1] == "us-west-1"


# ─────────────────────────────────────────────────────────────
# UQIExecutorBraket._resolve_device
# ─────────────────────────────────────────────────────────────

class _MockExtractor:
    tapes = sessions = circuits = observables = {}

class _MockConverter:
    extractor = _MockExtractor()
    qir_results = qasm_results = {}


def test_TC020_resolve_device_ionq():
    from uqi_executor_braket import UQIExecutorBraket
    ex = UQIExecutorBraket(_MockConverter(), shots=10)
    arn, region = ex._resolve_device("ionq_forte1")
    assert "ionq" in arn.lower()
    assert region == "us-east-1"

def test_TC021_resolve_device_cepheus():
    from uqi_executor_braket import UQIExecutorBraket
    ex = UQIExecutorBraket(_MockConverter(), shots=10)
    arn, region = ex._resolve_device("rigetti_cepheus")
    assert "Cepheus" in arn or "cepheus" in arn.lower()
    assert region == "us-west-1"

def test_TC022_resolve_device_simulator():
    from uqi_executor_braket import UQIExecutorBraket
    ex = UQIExecutorBraket(_MockConverter(), shots=10)
    arn, region = ex._resolve_device("braket_sv1")
    assert "simulator" in arn.lower()

def test_TC023_resolve_device_arn_passthrough():
    from uqi_executor_braket import UQIExecutorBraket
    ex = UQIExecutorBraket(_MockConverter(), shots=10)
    test_arn = "arn:aws:braket:us-west-2::device/qpu/test/T1"
    arn, region = ex._resolve_device(test_arn)
    assert arn == test_arn
    assert region == "us-west-2"

def test_TC024_resolve_device_unknown_raises():
    from uqi_executor_braket import UQIExecutorBraket
    ex = UQIExecutorBraket(_MockConverter(), shots=10)
    with pytest.raises(RuntimeError):
        ex._resolve_device("totally_unknown_qpu")


# ─────────────────────────────────────────────────────────────
# check_device_availability — non-Braket / 시뮬레이터
# ─────────────────────────────────────────────────────────────

def test_TC030_availability_non_braket():
    from uqi_executor_braket import check_device_availability
    r = check_device_availability("ibm_fez")
    assert "Non-Braket" in r["message"]
    assert r["available_now"] is None

def test_TC031_availability_quandela():
    from uqi_executor_braket import check_device_availability
    r = check_device_availability("qpu:ascella")
    assert "Non-Braket" in r["message"]

def test_TC032_availability_simulator_alwaysavailable():
    from uqi_executor_braket import check_device_availability
    r = check_device_availability("braket_sv1")
    assert r["available_now"] is True
    assert "24/7" in r["message"]


# ─────────────────────────────────────────────────────────────
# check_device_availability — RETIRED status (mock)
# ─────────────────────────────────────────────────────────────

def test_TC040_availability_retired_status(monkeypatch):
    from uqi_executor_braket import _BRAKET_QPU_MAP

    # ionq_forte1을 mock하기 위해 일시 매핑 추가
    fake_map = dict(_BRAKET_QPU_MAP)
    fake_map["test_retired"] = ("IONQ_FORTE_ARN", "us-east-1")
    monkeypatch.setattr("uqi_executor_braket._BRAKET_QPU_MAP", fake_map)

    # AwsDevice mock — RETIRED status
    fake_device = MagicMock()
    fake_device.status = "RETIRED"

    with patch("braket.aws.AwsDevice", return_value=fake_device), \
         patch("braket.aws.AwsSession"), \
         patch("boto3.Session"):
        from uqi_executor_braket import check_device_availability
        r = check_device_availability("test_retired")

    assert r["device_status"] == "RETIRED"
    assert r["available_now"] is False
    assert "RETIRED" in r["message"]


# ─────────────────────────────────────────────────────────────
# check_device_availability — ARN env var 누락
# ─────────────────────────────────────────────────────────────

def test_TC050_availability_missing_env_var(monkeypatch):
    """ARN 환경변수 없으면 안내 메시지"""
    monkeypatch.delenv("IONQ_FORTE_ARN", raising=False)
    from uqi_executor_braket import check_device_availability
    r = check_device_availability("ionq_forte1")
    assert "ARN" in r["message"] or "ARN" in str(r["warnings"])
