# test_uqi_executor_pasqal.py — PCS (Pasqal Cloud Services) executor 단위 테스트

import os
import sys
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))


# ─────────────────────────────────────────────────────────────
# Mock 환경 (자격증명 가짜값)
# ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _mock_env(monkeypatch):
    monkeypatch.setenv("PASQAL_USERNAME",  "user@example.com")
    monkeypatch.setenv("PASQAL_PASSWORD",  "test-password")
    monkeypatch.setenv("PASQAL_PROJECT_ID", "00000000-0000-0000-0000-000000000000")


# ─────────────────────────────────────────────────────────────
# device 매핑
# ─────────────────────────────────────────────────────────────

def test_TC001_device_map_keys():
    from uqi_executor_pasqal import _PCS_DEVICE_MAP
    assert "pasqal_fresnel" in _PCS_DEVICE_MAP
    assert "pasqal_fresnel_can1" in _PCS_DEVICE_MAP
    assert "pasqal_emu_fresnel" in _PCS_DEVICE_MAP
    assert "pasqal_emu_free" in _PCS_DEVICE_MAP


def test_TC002_device_map_real_qpu_no_emulator():
    from uqi_executor_pasqal import _PCS_DEVICE_MAP
    # 실 QPU 는 emulator None
    assert _PCS_DEVICE_MAP["pasqal_fresnel"]      == ("FRESNEL", None)
    assert _PCS_DEVICE_MAP["pasqal_fresnel_can1"] == ("FRESNEL_CAN1", None)


def test_TC003_device_map_emu_fresnel():
    from uqi_executor_pasqal import _PCS_DEVICE_MAP
    # EMU_FRESNEL: base device FRESNEL + emulator flag
    assert _PCS_DEVICE_MAP["pasqal_emu_fresnel"] == ("FRESNEL", "EMU_FRESNEL")


def test_TC004_device_map_emu_free():
    from uqi_executor_pasqal import _PCS_DEVICE_MAP
    # EMU_FREE: 무료 emulator (작은 회로용)
    assert _PCS_DEVICE_MAP["pasqal_emu_free"] == ("FRESNEL", "EMU_FREE")


# ─────────────────────────────────────────────────────────────
# 자격증명 가드
# ─────────────────────────────────────────────────────────────

def test_TC010_required_env_all_present():
    from uqi_executor_pasqal import UQIExecutorPasqal
    u, p, pid = UQIExecutorPasqal._required_env()
    assert u and p and pid


def test_TC011_required_env_missing_username(monkeypatch):
    from uqi_executor_pasqal import UQIExecutorPasqal
    monkeypatch.delenv("PASQAL_USERNAME", raising=False)
    with pytest.raises(RuntimeError, match="PASQAL_USERNAME"):
        UQIExecutorPasqal._required_env()


def test_TC012_required_env_missing_password(monkeypatch):
    from uqi_executor_pasqal import UQIExecutorPasqal
    monkeypatch.delenv("PASQAL_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="PASQAL_PASSWORD"):
        UQIExecutorPasqal._required_env()


def test_TC013_required_env_missing_project_id(monkeypatch):
    from uqi_executor_pasqal import UQIExecutorPasqal
    monkeypatch.delenv("PASQAL_PROJECT_ID", raising=False)
    with pytest.raises(RuntimeError, match="PASQAL_PROJECT_ID"):
        UQIExecutorPasqal._required_env()


# ─────────────────────────────────────────────────────────────
# _resolve_device
# ─────────────────────────────────────────────────────────────

def test_TC020_resolve_device_real():
    from uqi_executor_pasqal import UQIExecutorPasqal
    assert UQIExecutorPasqal._resolve_device("pasqal_fresnel")      == ("FRESNEL", None)
    assert UQIExecutorPasqal._resolve_device("pasqal_fresnel_can1") == ("FRESNEL_CAN1", None)


def test_TC021_resolve_device_emu():
    from uqi_executor_pasqal import UQIExecutorPasqal
    assert UQIExecutorPasqal._resolve_device("pasqal_emu_fresnel") == ("FRESNEL", "EMU_FRESNEL")


def test_TC022_resolve_device_unknown_raises():
    from uqi_executor_pasqal import UQIExecutorPasqal
    with pytest.raises(RuntimeError, match="Unknown PCS qpu_name"):
        UQIExecutorPasqal._resolve_device("bogus_qpu")


# ─────────────────────────────────────────────────────────────
# Job counts 정규화
# ─────────────────────────────────────────────────────────────

def test_TC030_job_counts_dict_form():
    from uqi_executor_pasqal import UQIExecutorPasqal
    job = MagicMock()
    job.result = {"00": 510, "11": 514}
    out = UQIExecutorPasqal._job_counts(job)
    assert out == {"00": 510, "11": 514}


def test_TC031_job_counts_list_form():
    from uqi_executor_pasqal import UQIExecutorPasqal
    job = MagicMock()
    # 일부 SDK 버전: list[dict] 로 옴
    job.result = [{"counts": {"01": 100, "10": 100}}]
    out = UQIExecutorPasqal._job_counts(job)
    assert out == {"01": 100, "10": 100}


def test_TC032_job_counts_none():
    from uqi_executor_pasqal import UQIExecutorPasqal
    job = MagicMock()
    job.result = None
    assert UQIExecutorPasqal._job_counts(job) is None


def test_TC033_job_counts_int_keys_stringified():
    """SDK 가 int bitstring key 로 줄 경우 str 로 정규화."""
    from uqi_executor_pasqal import UQIExecutorPasqal
    job = MagicMock()
    job.result = {0: 10, 3: 20}
    out = UQIExecutorPasqal._job_counts(job)
    assert out == {"0": 10, "3": 20}


# ─────────────────────────────────────────────────────────────
# fetch_job_status — mock SDK
# ─────────────────────────────────────────────────────────────

def _mock_batch(status, jobs=None):
    b = MagicMock()
    b.status = status
    b.jobs = jobs or []
    b.ordered_jobs = jobs or []
    b.created_at = "2026-05-12T00:00:00Z"
    b.start_datetime = "2026-05-12T00:01:00Z"
    b.end_datetime   = "2026-05-12T00:02:30Z"
    return b


def test_TC040_fetch_status_done():
    from uqi_executor_pasqal import UQIExecutorPasqal
    job = MagicMock(); job.result = {"00": 512, "11": 512}
    batch = _mock_batch("DONE", jobs=[job])
    with patch("uqi_executor_pasqal.UQIExecutorPasqal._new_sdk_or_raise") as m:
        m.return_value.get_batch.return_value = batch
        out = UQIExecutorPasqal.fetch_job_status("batch-1")
    assert out["done"] is True
    assert out["status"] == "DONE"
    assert out["counts"] == {"00": 512, "11": 512}


def test_TC041_fetch_status_pending_running():
    from uqi_executor_pasqal import UQIExecutorPasqal
    for s in ("PENDING", "RUNNING", "PAUSED"):
        batch = _mock_batch(s)
        with patch("uqi_executor_pasqal.UQIExecutorPasqal._new_sdk_or_raise") as m:
            m.return_value.get_batch.return_value = batch
            out = UQIExecutorPasqal.fetch_job_status("b")
        assert out["done"] is False
        assert out["cancelled"] is False
        assert out.get("cloud_failed") is None


def test_TC042_fetch_status_canceled():
    from uqi_executor_pasqal import UQIExecutorPasqal
    for s in ("CANCELED", "CANCELLED"):
        batch = _mock_batch(s)
        with patch("uqi_executor_pasqal.UQIExecutorPasqal._new_sdk_or_raise") as m:
            m.return_value.get_batch.return_value = batch
            out = UQIExecutorPasqal.fetch_job_status("b")
        assert out["cancelled"] is True


def test_TC043_fetch_status_error_with_message():
    from uqi_executor_pasqal import UQIExecutorPasqal
    job = MagicMock(); job.errors = ["device unavailable"]
    batch = _mock_batch("ERROR", jobs=[job])
    with patch("uqi_executor_pasqal.UQIExecutorPasqal._new_sdk_or_raise") as m:
        m.return_value.get_batch.return_value = batch
        out = UQIExecutorPasqal.fetch_job_status("b")
    assert out["cloud_failed"] is True
    assert "device unavailable" in (out["error"] or "")


def test_TC044_fetch_status_sdk_failure():
    from uqi_executor_pasqal import UQIExecutorPasqal
    with patch("uqi_executor_pasqal.UQIExecutorPasqal._new_sdk_or_raise",
               side_effect=Exception("auth failed")):
        out = UQIExecutorPasqal.fetch_job_status("b")
    assert out["done"] is False
    assert "auth failed" in out["error"]


# ─────────────────────────────────────────────────────────────
# fetch_job_timing
# ─────────────────────────────────────────────────────────────

def test_TC050_fetch_timing():
    from uqi_executor_pasqal import UQIExecutorPasqal
    batch = _mock_batch("DONE")
    with patch("uqi_executor_pasqal.UQIExecutorPasqal._new_sdk_or_raise") as m:
        m.return_value.get_batch.return_value = batch
        out = UQIExecutorPasqal.fetch_job_timing("b")
    assert out["submitted_at"] == "2026-05-12T00:00:00Z"
    assert out["started_at"]   == "2026-05-12T00:01:00Z"
    assert out["ended_at"]     == "2026-05-12T00:02:30Z"
    assert out["error"] is None


# ─────────────────────────────────────────────────────────────
# cancel_job
# ─────────────────────────────────────────────────────────────

def test_TC060_cancel_ok():
    from uqi_executor_pasqal import UQIExecutorPasqal
    sdk = MagicMock()
    with patch("uqi_executor_pasqal.UQIExecutorPasqal._new_sdk_or_raise", return_value=sdk):
        out = UQIExecutorPasqal.cancel_job("b")
    assert out["ok"] is True
    sdk.cancel_batch.assert_called_once_with("b")


def test_TC061_cancel_failed():
    from uqi_executor_pasqal import UQIExecutorPasqal
    sdk = MagicMock(); sdk.cancel_batch.side_effect = Exception("already done")
    with patch("uqi_executor_pasqal.UQIExecutorPasqal._new_sdk_or_raise", return_value=sdk):
        out = UQIExecutorPasqal.cancel_job("b")
    assert out["ok"] is False
    assert "already done" in out["error"]


# ─────────────────────────────────────────────────────────────
# _submit_single_ahs — emulator path (calibrated layout swap 안 함)
# ─────────────────────────────────────────────────────────────

def _make_seq_mock():
    seq = MagicMock()
    seq.to_abstract_repr.return_value = '{"sequence_builder":"mock"}'
    return seq


def test_TC070_submit_emulator_skips_layout_swap(tmp_path):
    """EMU_FRESNEL 제출 시 fetch_available_devices 호출되지 않아야 함."""
    from uqi_executor_pasqal import UQIExecutorPasqal

    algo = tmp_path / "algo.py"
    algo.write_text("# dummy")

    batch = MagicMock(); batch.id = "batch-emu-123"
    sdk = MagicMock(); sdk.create_batch.return_value = batch

    seq = _make_seq_mock()

    ex = UQIExecutorPasqal(converter=None, shots=128)
    with patch("uqi_executor_azure.UQIExecutorAzure._extract_pulser_sequence",
               return_value=seq), \
         patch.object(ex, "_get_sdk", return_value=sdk), \
         patch.object(ex, "_get_cloud") as mock_cloud:
        out = ex._submit_single_ahs("algo", str(algo), backend_name="pasqal_emu_fresnel")

    assert out["ok"] is True
    assert out["batch_id"] == "batch-emu-123"
    assert out["via"] == "pcs-pulser"
    assert out["backend"].startswith("pcs:EMU_FRESNEL")
    # emulator 경로에서는 cloud (device fetch) 호출 X
    mock_cloud.assert_not_called()
    # create_batch 호출 시 emulator 인자 들어갔는지
    assert sdk.create_batch.called
    kwargs = sdk.create_batch.call_args.kwargs
    assert "emulator" in kwargs
    assert kwargs["jobs"][0]["runs"] == 128


def test_TC071_submit_real_qpu_tries_layout_swap(tmp_path):
    """실 QPU 제출 시 fetch_available_devices 통해 calibrated swap 시도."""
    from uqi_executor_pasqal import UQIExecutorPasqal

    algo = tmp_path / "algo.py"
    algo.write_text("# dummy")

    batch = MagicMock(); batch.id = "batch-real-1"
    sdk = MagicMock(); sdk.create_batch.return_value = batch

    seq = _make_seq_mock()

    # PasqalCloud mock — device 가 calibrated layout 있다고 가정
    real_device = MagicMock()
    real_device.pre_calibrated_layouts = ["layout0"]
    cloud = MagicMock()
    cloud.fetch_available_devices.return_value = {"FRESNEL": real_device}

    ex = UQIExecutorPasqal(converter=None, shots=200)
    with patch("uqi_executor_azure.UQIExecutorAzure._extract_pulser_sequence",
               return_value=seq), \
         patch("uqi_executor_azure.UQIExecutorAzure._adapt_sequence_to_device",
               return_value=seq) as adapt, \
         patch.object(ex, "_get_sdk", return_value=sdk), \
         patch.object(ex, "_get_cloud", return_value=cloud):
        out = ex._submit_single_ahs("algo", str(algo), backend_name="pasqal_fresnel")

    assert out["ok"] is True
    assert out["batch_id"] == "batch-real-1"
    assert out["backend"] == "pcs:FRESNEL"
    adapt.assert_called_once()
    # 실 QPU 제출은 emulator 키 None
    kwargs = sdk.create_batch.call_args.kwargs
    assert "emulator" not in kwargs or kwargs.get("emulator") is None


def test_TC072_submit_failure_returns_error(tmp_path):
    from uqi_executor_pasqal import UQIExecutorPasqal

    algo = tmp_path / "algo.py"; algo.write_text("# dummy")
    seq = _make_seq_mock()
    sdk = MagicMock(); sdk.create_batch.side_effect = Exception("quota exceeded")

    ex = UQIExecutorPasqal(converter=None, shots=10)
    with patch("uqi_executor_azure.UQIExecutorAzure._extract_pulser_sequence",
               return_value=seq), \
         patch.object(ex, "_get_sdk", return_value=sdk):
        out = ex._submit_single_ahs("algo", str(algo), backend_name="pasqal_emu_fresnel")

    assert out["ok"] is False
    assert "quota exceeded" in (out["error"] or "")
