# test_uqi_calibration.py

import os
import sys
import json
import pytest
import tempfile
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from uqi_calibration import UQICalibration, CALIBRATION_TTL


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _make_cal(data=None, tmp_file=None):
    """임시 파일 기반 UQICalibration 생성"""
    if tmp_file is None:
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.json',
                                        delete=False)
        if data:
            json.dump(data, f)
        f.close()
        tmp_file = f.name
    with patch("uqi_calibration.load_dotenv"):
        cal = UQICalibration(calibration_file=tmp_file)
    return cal, tmp_file


def _fresh_timestamp() -> str:
    return datetime.now().isoformat()


def _expired_timestamp(hours=25) -> str:
    return (datetime.now() - timedelta(hours=hours)).isoformat()


def _base_entry(vendor="ibm", expired=False) -> dict:
    ts = _expired_timestamp() if expired else _fresh_timestamp()
    return {
        "vendor": vendor,
        "num_qubits": 5,
        "avg_t1_ms": 100.0,
        "avg_t2_ms": 80.0,
        "avg_1q_error": 0.001,
        "avg_2q_error": 0.01,
        "avg_ro_error": 0.02,
        "avg_1q_ns": 50.0,
        "avg_2q_ns": 300.0,
        "basis_gates": ["cx", "rz", "x"],
        "coupling_map": [[0, 1], [1, 2]],
        "last_updated": ts,
    }


# ─────────────────────────────────────────────────────────────
# TC-01x: __init__ / _load
# ─────────────────────────────────────────────────────────────

class TestInitAndLoad:

    def test_TC011_empty_file_loads_empty_dict(self):
        cal, tmp = _make_cal()
        try:
            assert cal.data == {}
        finally:
            os.unlink(tmp)

    def test_TC012_existing_data_loaded(self):
        data = {"ibm_fez": _base_entry()}
        cal, tmp = _make_cal(data)
        try:
            assert "ibm_fez" in cal.data
        finally:
            os.unlink(tmp)

    def test_TC013_corrupt_file_loads_empty_dict(self):
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        f.write("NOT VALID JSON {{{{")
        f.close()
        with patch("uqi_calibration.load_dotenv"):
            cal = UQICalibration(calibration_file=f.name)
        try:
            assert cal.data == {}
        finally:
            os.unlink(f.name)

    def test_TC014_nonexistent_file_loads_empty_dict(self):
        with patch("uqi_calibration.load_dotenv"):
            cal = UQICalibration(calibration_file="/nonexistent/path.json")
        assert cal.data == {}

    def test_TC015_calibration_file_stored(self):
        cal, tmp = _make_cal()
        try:
            assert cal.calibration_file == tmp
        finally:
            os.unlink(tmp)


# ─────────────────────────────────────────────────────────────
# TC-02x: _save
# ─────────────────────────────────────────────────────────────

class TestSave:

    def test_TC021_save_and_reload(self):
        cal, tmp = _make_cal()
        try:
            cal.data["ibm_fez"] = _base_entry()
            cal._save()
            with open(tmp, 'r') as f:
                loaded = json.load(f)
            assert "ibm_fez" in loaded
        finally:
            os.unlink(tmp)

    def test_TC022_save_overwrites_existing(self):
        data = {"ibm_fez": _base_entry()}
        cal, tmp = _make_cal(data)
        try:
            cal.data["ibm_fez"]["num_qubits"] = 99
            cal._save()
            with open(tmp, 'r') as f:
                loaded = json.load(f)
            assert loaded["ibm_fez"]["num_qubits"] == 99
        finally:
            os.unlink(tmp)


# ─────────────────────────────────────────────────────────────
# TC-03x: _detect_vendor
# ─────────────────────────────────────────────────────────────

class TestDetectVendor:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.cal, self.tmp = _make_cal()
        yield
        os.unlink(self.tmp)

    def test_TC031_ibm_detected(self):
        assert self.cal._detect_vendor("ibm_fez") == "ibm"

    def test_TC032_iqm_detected(self):
        assert self.cal._detect_vendor("iqm_garnet") == "iqm"

    def test_TC033_ionq_detected(self):
        assert self.cal._detect_vendor("ionq_forte1") == "ionq"

    def test_TC034_rigetti_detected(self):
        assert self.cal._detect_vendor("rigetti_ankaa3") == "rigetti"

    def test_TC035_ankaa_detected_as_rigetti(self):
        assert self.cal._detect_vendor("ankaa_system") == "rigetti"

    def test_TC036_quera_detected(self):
        assert self.cal._detect_vendor("quera_aquila") == "quera"

    def test_TC037_aquila_detected_as_quera(self):
        assert self.cal._detect_vendor("aquila_device") == "quera"

    def test_TC038_quandela_sim_detected(self):
        assert self.cal._detect_vendor("sim:ascella") == "quandela"

    def test_TC039_quandela_qpu_detected(self):
        assert self.cal._detect_vendor("qpu:belenos") == "quandela"

    def test_TC03A_unknown_vendor(self):
        assert self.cal._detect_vendor("unknown_device") == "unknown"


# ─────────────────────────────────────────────────────────────
# TC-04x: _is_expired
# ─────────────────────────────────────────────────────────────

class TestIsExpired:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.cal, self.tmp = _make_cal()
        yield
        os.unlink(self.tmp)

    def test_TC041_no_entry_returns_true(self):
        assert self.cal._is_expired("nonexistent_qpu") is True

    def test_TC042_no_last_updated_returns_true(self):
        self.cal.data["ibm_fez"] = {"vendor": "ibm"}
        assert self.cal._is_expired("ibm_fez") is True

    def test_TC043_invalid_timestamp_returns_true(self):
        self.cal.data["ibm_fez"] = {"last_updated": "NOT_A_DATE"}
        assert self.cal._is_expired("ibm_fez") is True

    def test_TC044_fresh_ibm_not_expired(self):
        self.cal.data["ibm_fez"] = _base_entry(vendor="ibm", expired=False)
        assert self.cal._is_expired("ibm_fez") is False

    def test_TC045_expired_ibm_returns_true(self):
        self.cal.data["ibm_fez"] = _base_entry(vendor="ibm", expired=True)
        assert self.cal._is_expired("ibm_fez") is True

    def test_TC046_iqm_ttl_12h_fresh(self):
        ts = (datetime.now() - timedelta(hours=11)).isoformat()
        self.cal.data["iqm_garnet"] = {"vendor": "iqm", "last_updated": ts}
        assert self.cal._is_expired("iqm_garnet") is False

    def test_TC047_iqm_ttl_12h_expired(self):
        ts = (datetime.now() - timedelta(hours=13)).isoformat()
        self.cal.data["iqm_garnet"] = {"vendor": "iqm", "last_updated": ts}
        assert self.cal._is_expired("iqm_garnet") is True

    def test_TC048_quandela_ttl_72h_fresh(self):
        ts = (datetime.now() - timedelta(hours=71)).isoformat()
        self.cal.data["qpu:belenos"] = {"vendor": "quandela", "last_updated": ts}
        assert self.cal._is_expired("qpu:belenos") is False

    def test_TC049_unknown_vendor_uses_24h_default(self):
        ts = (datetime.now() - timedelta(hours=25)).isoformat()
        self.cal.data["custom_qpu"] = {"vendor": "unknown", "last_updated": ts}
        assert self.cal._is_expired("custom_qpu") is True


# ─────────────────────────────────────────────────────────────
# TC-05x: get
# ─────────────────────────────────────────────────────────────

class TestGet:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.cal, self.tmp = _make_cal()
        yield
        os.unlink(self.tmp)

    def test_TC051_fresh_data_no_sync(self):
        self.cal.data["ibm_fez"] = _base_entry(expired=False)
        with patch.object(self.cal, "sync") as mock_sync:
            self.cal.get("ibm_fez")
            mock_sync.assert_not_called()

    def test_TC052_expired_data_triggers_sync(self):
        self.cal.data["ibm_fez"] = _base_entry(expired=True)
        with patch.object(self.cal, "sync") as mock_sync:
            self.cal.get("ibm_fez")
            mock_sync.assert_called_once_with("ibm_fez")

    def test_TC053_missing_data_triggers_sync(self):
        with patch.object(self.cal, "sync") as mock_sync:
            self.cal.get("ibm_fez")
            mock_sync.assert_called_once_with("ibm_fez")

    def test_TC054_force_sync_always_syncs(self):
        self.cal.data["ibm_fez"] = _base_entry(expired=False)
        with patch.object(self.cal, "sync") as mock_sync:
            self.cal.get("ibm_fez", force_sync=True)
            mock_sync.assert_called_once_with("ibm_fez")

    def test_TC055_returns_data_dict(self):
        self.cal.data["ibm_fez"] = _base_entry(expired=False)
        result = self.cal.get("ibm_fez")
        assert isinstance(result, dict)

    def test_TC056_returns_empty_dict_when_no_data(self):
        with patch.object(self.cal, "sync"):
            result = self.cal.get("nonexistent_qpu")
            assert result == {}


# ─────────────────────────────────────────────────────────────
# TC-06x: sync
# ─────────────────────────────────────────────────────────────

class TestSync:

    @pytest.fixture(autouse=True)
    def setup(self):
        UQICalibration._SYNC_CACHE.clear()
        self.cal, self.tmp = _make_cal()
        yield
        os.unlink(self.tmp)
        UQICalibration._SYNC_CACHE.clear()

    def test_TC061_unknown_vendor_returns_false(self):
        result = self.cal.sync("unknown_device_xyz")
        assert result is False

    def test_TC062_ibm_routes_to_sync_ibm(self):
        with patch.object(self.cal, "_sync_ibm", return_value=True) as m, \
             patch.object(self.cal, "_append_history"), \
             patch.object(self.cal, "_save"):
            self.cal.sync("ibm_fez")
            m.assert_called_once_with("ibm_fez")

    def test_TC063_iqm_routes_to_sync_iqm(self):
        with patch.object(self.cal, "_sync_iqm", return_value=True) as m, \
             patch.object(self.cal, "_append_history"), \
             patch.object(self.cal, "_save"):
            self.cal.sync("iqm_garnet")
            m.assert_called_once_with("iqm_garnet")

    def test_TC064_ionq_routes_to_sync_ionq(self):
        with patch.object(self.cal, "_sync_ionq", return_value=True) as m, \
             patch.object(self.cal, "_append_history"), \
             patch.object(self.cal, "_save"):
            self.cal.sync("ionq_forte1")
            m.assert_called_once_with("ionq_forte1")

    def test_TC065_rigetti_routes_to_sync_rigetti(self):
        with patch.object(self.cal, "_sync_rigetti", return_value=True) as m, \
             patch.object(self.cal, "_append_history"), \
             patch.object(self.cal, "_save"):
            self.cal.sync("rigetti_ankaa3")
            m.assert_called_once_with("rigetti_ankaa3")

    def test_TC066_quera_routes_to_sync_quera(self):
        with patch.object(self.cal, "_sync_quera", return_value=True) as m, \
             patch.object(self.cal, "_append_history"), \
             patch.object(self.cal, "_save"):
            self.cal.sync("quera_aquila")
            m.assert_called_once_with("quera_aquila")

    def test_TC067_quandela_routes_to_sync_quandela(self):
        with patch.object(self.cal, "_sync_quandela", return_value=True) as m, \
             patch.object(self.cal, "_append_history"), \
             patch.object(self.cal, "_save"):
            self.cal.sync("sim:ascella")
            m.assert_called_once_with("sim:ascella")

    def test_TC068_sync_cache_prevents_repeat_within_1h(self):
        with patch.object(self.cal, "_sync_ibm", return_value=True) as m, \
             patch.object(self.cal, "_append_history"), \
             patch.object(self.cal, "_save"):
            self.cal.sync("ibm_fez")
            self.cal.sync("ibm_fez")
            m.assert_called_once()

    def test_TC069_failed_sync_returns_false(self):
        with patch.object(self.cal, "_sync_ibm", return_value=False):
            result = self.cal.sync("ibm_fez")
            assert result is False

    def test_TC06A_exception_in_sync_returns_false(self):
        with patch.object(self.cal, "_sync_ibm", side_effect=Exception("fail")):
            result = self.cal.sync("ibm_fez")
            assert result is False


# ─────────────────────────────────────────────────────────────
# TC-07x: _append_history / get_history
# ─────────────────────────────────────────────────────────────

class TestHistory:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.cal, self.tmp = _make_cal()
        yield
        os.unlink(self.tmp)

    def test_TC071_empty_entry_no_history(self):
        self.cal._append_history("ibm_fez")
        assert self.cal.get_history("ibm_fez") == []

    def test_TC072_snapshot_added_to_history(self):
        self.cal.data["ibm_fez"] = _base_entry()
        self.cal._append_history("ibm_fez")
        history = self.cal.get_history("ibm_fez")
        assert len(history) == 1

    def test_TC073_multiple_snapshots_accumulated(self):
        self.cal.data["ibm_fez"] = _base_entry()
        self.cal._append_history("ibm_fez")
        self.cal._append_history("ibm_fez")
        assert len(self.cal.get_history("ibm_fez")) == 2

    def test_TC074_history_capped_at_2160(self):
        self.cal.data["ibm_fez"] = _base_entry()
        history_key = "ibm_fez__history"
        self.cal.data[history_key] = [{"snapshot": i} for i in range(2160)]
        self.cal._append_history("ibm_fez")
        assert len(self.cal.data[history_key]) == 2160

    def test_TC075_get_history_empty_when_no_history(self):
        assert self.cal.get_history("nonexistent_qpu") == []

    def test_TC076_history_key_separate_from_main_data(self):
        self.cal.data["ibm_fez"] = _base_entry()
        self.cal._append_history("ibm_fez")
        assert "ibm_fez__history" in self.cal.data
        assert "ibm_fez" in self.cal.data


# ─────────────────────────────────────────────────────────────
# TC-08x: get_transpile_params
# ─────────────────────────────────────────────────────────────

class TestGetTranspileParams:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.cal, self.tmp = _make_cal()
        yield
        os.unlink(self.tmp)

    def test_TC081_required_keys_present(self):
        self.cal.data["ibm_fez"] = _base_entry(expired=False)
        params = self.cal.get_transpile_params("ibm_fez")
        required = {"num_qubits", "basis_gates", "coupling_map",
                    "avg_1q_error", "avg_2q_error", "avg_ro_error",
                    "avg_t1_ms", "avg_t2_ms", "avg_1q_ns",
                    "avg_2q_ns", "last_updated"}
        assert required <= set(params.keys())

    def test_TC082_empty_data_returns_empty_dict(self):
        with patch.object(self.cal, "sync"):
            params = self.cal.get_transpile_params("nonexistent_qpu")
            assert params == {}

    def test_TC083_values_match_calibration_data(self):
        entry = _base_entry(expired=False)
        self.cal.data["ibm_fez"] = entry
        params = self.cal.get_transpile_params("ibm_fez")
        assert params["num_qubits"] == entry["num_qubits"]
        assert params["basis_gates"] == entry["basis_gates"]
        assert params["avg_1q_error"] == entry["avg_1q_error"]


# ─────────────────────────────────────────────────────────────
# TC-09x: print_summary
# ─────────────────────────────────────────────────────────────

class TestPrintSummary:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.cal, self.tmp = _make_cal()
        yield
        os.unlink(self.tmp)

    def test_TC091_no_data_no_exception(self, capsys):
        self.cal.print_summary("nonexistent_qpu")
        out = capsys.readouterr().out
        assert "데이터 없음" in out

    def test_TC092_ibm_summary_shows_qubits(self, capsys):
        self.cal.data["ibm_fez"] = _base_entry(vendor="ibm")
        self.cal.print_summary("ibm_fez")
        out = capsys.readouterr().out
        assert "큐비트 수" in out

    def test_TC093_quandela_summary_shows_modes(self, capsys):
        self.cal.data["qpu:belenos"] = {
            "vendor": "quandela",
            "last_updated": _fresh_timestamp(),
            "max_mode_count": 24,
            "max_photon_count": 6,
            "avg_transmittance": 0.06,
            "avg_hom": 0.92,
        }
        self.cal.print_summary("qpu:belenos")
        out = capsys.readouterr().out
        assert "모드 수" in out

    def test_TC094_qpu_name_shown_in_output(self, capsys):
        self.cal.data["ibm_fez"] = _base_entry(vendor="ibm")
        self.cal.print_summary("ibm_fez")
        out = capsys.readouterr().out
        assert "ibm_fez" in out

    def test_TC095_t1_t2_shown_for_non_quandela(self, capsys):
        self.cal.data["iqm_garnet"] = _base_entry(vendor="iqm")
        self.cal.print_summary("iqm_garnet")
        out = capsys.readouterr().out
        assert "T1" in out
        assert "T2" in out


# ─────────────────────────────────────────────────────────────
# TC-10x: CALIBRATION_TTL 상수
# ─────────────────────────────────────────────────────────────

class TestCalibrationTTL:

    def test_TC101_ibm_ttl_24h(self):
        assert CALIBRATION_TTL["ibm"] == timedelta(hours=24)

    def test_TC102_iqm_ttl_12h(self):
        assert CALIBRATION_TTL["iqm"] == timedelta(hours=12)

    def test_TC103_quandela_ttl_72h(self):
        assert CALIBRATION_TTL["quandela"] == timedelta(hours=72)

    def test_TC104_all_vendors_present(self):
        for vendor in ["ibm", "iqm", "ionq", "rigetti", "quera", "quandela"]:
            assert vendor in CALIBRATION_TTL


if __name__ == "__main__":
    pytest.main([__file__, "-v",
                 "--cov=uqi_calibration", "--cov-report=term-missing"])