# test_uqi_noise.py

import os
import sys
import pytest
import numpy as np
from unittest.mock import MagicMock, patch
from qiskit import QuantumCircuit

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from uqi_noise import (
    build_noise_model,
    build_noise_model_ibm,
    build_noise_model_iqm,
    build_noise_model_from_calibration,
    UQINoise,
)


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _bell_circuit() -> QuantumCircuit:
    qc = QuantumCircuit(2, 2)
    qc.h(0)
    qc.cx(0, 1)
    qc.measure([0, 1], [0, 1])
    return qc


def _base_calibration() -> dict:
    return {
        "coupling_map": [[0, 1], [1, 2], [2, 3], [3, 4]],
        "basis_gates": ["cx", "rz", "x", "measure"],
        "num_qubits": 5,
        "avg_1q_error": 0.001,
        "avg_2q_error": 0.01,
        "avg_ro_error": 0.02,
        "avg_1q_ns": 50.0,
        "avg_2q_ns": 300.0,
        "avg_t1_ms": 100.0,
        "avg_t2_ms": 80.0,
    }


def _make_noise(qpu_name="test_qpu", calibration=None):
    """UQINoise 인스턴스 생성 (build_noise_model mock)"""
    mock_nm = MagicMock()
    with patch("uqi_noise.build_noise_model",
               return_value=(mock_nm, None, "calibration")):
        return UQINoise(qpu_name, calibration or _base_calibration())


# ─────────────────────────────────────────────────────────────
# TC-01x: build_noise_model_ibm
# ─────────────────────────────────────────────────────────────

class TestBuildNoiseModelIBM:

    def test_TC011_unknown_qpu_returns_none(self):
        nm, backend = build_noise_model_ibm("unknown_qpu")
        assert nm is None
        assert backend is None

    def test_TC012_known_qpu_name_in_fake_map(self):
        # ibm_fez는 fake_map에 있음 — import 실패해도 None 반환 (예외 없음)
        try:
            nm, backend = build_noise_model_ibm("ibm_fez")
            # 성공 또는 실패 모두 tuple 반환
            assert isinstance(nm, object)
        except Exception:
            pytest.fail("build_noise_model_ibm이 예외를 발생시켜선 안 됨")

    def test_TC013_returns_tuple(self):
        result = build_noise_model_ibm("unknown_qpu")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_TC014_import_failure_returns_none(self):
        with patch("qiskit_ibm_runtime.fake_provider.FakeFez",
                   side_effect=Exception("load fail")):
            nm, backend = build_noise_model_ibm("ibm_fez")
            assert nm is None
            assert backend is None


# ─────────────────────────────────────────────────────────────
# TC-02x: build_noise_model_iqm
# ─────────────────────────────────────────────────────────────

class TestBuildNoiseModelIQM:

    def test_TC021_unknown_qpu_returns_none(self):
        nm, backend = build_noise_model_iqm("unknown_qpu")
        assert nm is None
        assert backend is None

    def test_TC022_returns_tuple(self):
        result = build_noise_model_iqm("unknown_qpu")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_TC023_import_failure_returns_none(self):
        with patch.dict("sys.modules", {
            "iqm.qiskit_iqm.fake_backends": None,
            "iqm.qiskit_iqm.fake_backends.fake_garnet": None,
        }):
            nm, backend = build_noise_model_iqm("iqm_garnet")
            assert nm is None
            assert backend is None

    def test_TC024_known_iqm_name_no_exception(self):
        try:
            nm, backend = build_noise_model_iqm("iqm_garnet")
            assert isinstance(nm, object)
        except Exception:
            pytest.fail("build_noise_model_iqm이 예외를 발생시켜선 안 됨")


# ─────────────────────────────────────────────────────────────
# TC-03x: build_noise_model_from_calibration
# ─────────────────────────────────────────────────────────────

class TestBuildNoiseModelFromCalibration:

    def test_TC031_returns_noise_model_object(self):
        from qiskit_aer.noise import NoiseModel
        nm = build_noise_model_from_calibration(_base_calibration(), "ibm_fez")
        assert isinstance(nm, NoiseModel)

    def test_TC032_empty_calibration_no_exception(self):
        from qiskit_aer.noise import NoiseModel
        nm = build_noise_model_from_calibration({}, "ibm_fez")
        assert isinstance(nm, NoiseModel)

    def test_TC033_no_basis_gates_no_exception(self):
        cal = {"avg_1q_error": 0.001, "avg_2q_error": 0.01}
        from qiskit_aer.noise import NoiseModel
        nm = build_noise_model_from_calibration(cal, "ibm_fez")
        assert isinstance(nm, NoiseModel)

    def test_TC034_with_ro_error(self):
        cal = {
            "basis_gates": ["x", "measure"],
            "avg_ro_error": 0.05,
        }
        from qiskit_aer.noise import NoiseModel
        nm = build_noise_model_from_calibration(cal, "ibm_fez")
        assert isinstance(nm, NoiseModel)

    def test_TC035_with_thermal_relaxation(self):
        cal = {
            "basis_gates": ["x"],
            "avg_1q_error": 0.001,
            "avg_t1_ms": 100.0,
            "avg_t2_ms": 80.0,
            "avg_1q_ns": 50.0,
        }
        from qiskit_aer.noise import NoiseModel
        nm = build_noise_model_from_calibration(cal, "ibm_fez")
        assert isinstance(nm, NoiseModel)

    def test_TC036_two_q_gate_included(self):
        cal = {
            "basis_gates": ["cx"],
            "avg_2q_error": 0.01,
        }
        from qiskit_aer.noise import NoiseModel
        nm = build_noise_model_from_calibration(cal, "ibm_fez")
        assert isinstance(nm, NoiseModel)

    def test_TC037_skip_gates_not_added(self):
        # measure/reset/barrier 등은 스킵되어야 함 (예외 없이)
        cal = {"basis_gates": ["measure", "reset", "barrier", "x"]}
        from qiskit_aer.noise import NoiseModel
        nm = build_noise_model_from_calibration(cal, "ibm_fez")
        assert isinstance(nm, NoiseModel)


# ─────────────────────────────────────────────────────────────
# TC-04x: build_noise_model (라우팅)
# ─────────────────────────────────────────────────────────────

class TestBuildNoiseModel:

    def test_TC041_returns_three_tuple(self):
        result = build_noise_model("unknown_qpu", None)
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_TC042_unknown_qpu_no_calibration_returns_none(self):
        nm, backend, source = build_noise_model("unknown_qpu", None)
        assert nm is None
        assert backend is None
        assert source is None

    def test_TC043_ibm_qpu_routes_to_ibm(self):
        mock_nm = MagicMock()
        with patch("uqi_noise.build_noise_model_ibm",
                   return_value=(mock_nm, MagicMock())) as m:
            nm, backend, source = build_noise_model("ibm_fez", None)
            m.assert_called_once_with("ibm_fez")
            assert source == "ibm_fake"

    def test_TC044_ibm_fake_fail_falls_back_to_calibration(self):
        with patch("uqi_noise.build_noise_model_ibm", return_value=(None, None)):
            nm, backend, source = build_noise_model("ibm_fez", _base_calibration())
            assert source == "calibration"

    def test_TC045_iqm_qpu_routes_to_iqm(self):
        mock_nm = MagicMock()
        with patch("uqi_noise.build_noise_model_iqm",
                   return_value=(mock_nm, MagicMock())) as m:
            nm, backend, source = build_noise_model("iqm_garnet", None)
            m.assert_called_once_with("iqm_garnet", None)
            assert source == "iqm_fake"

    def test_TC046_iqm_fake_fail_falls_back_to_calibration(self):
        with patch("uqi_noise.build_noise_model_iqm", return_value=(None, None)):
            nm, backend, source = build_noise_model("iqm_garnet", _base_calibration())
            assert source == "calibration"

    def test_TC047_calibration_only_returns_calibration_source(self):
        nm, backend, source = build_noise_model("custom_qpu", _base_calibration())
        assert source == "calibration"
        assert nm is not None
        assert backend is None

    def test_TC048_ibm_in_name_triggers_ibm_path(self):
        with patch("uqi_noise.build_noise_model_ibm",
                   return_value=(None, None)) as m:
            build_noise_model("ibm_torino", None)
            m.assert_called_once()

    def test_TC049_iqm_in_name_triggers_iqm_path(self):
        with patch("uqi_noise.build_noise_model_iqm",
                   return_value=(None, None)) as m:
            build_noise_model("iqm_adonis", None)
            m.assert_called_once()


# ─────────────────────────────────────────────────────────────
# TC-05x: UQINoise.__init__
# ─────────────────────────────────────────────────────────────

class TestUQINoiseInit:

    def test_TC051_qpu_name_stored(self):
        noise = _make_noise("ibm_fez")
        assert noise.qpu_name == "ibm_fez"

    def test_TC052_calibration_stored(self):
        cal = _base_calibration()
        noise = _make_noise("ibm_fez", cal)
        assert noise.calibration == cal

    def test_TC053_empty_calibration_defaults_to_empty_dict(self):
        with patch("uqi_noise.build_noise_model",
                   return_value=(None, None, None)):
            noise = UQINoise("unknown_qpu", None)
            assert noise.calibration == {}

    def test_TC054_noise_model_set(self):
        noise = _make_noise()
        assert noise.noise_model is not None

    def test_TC055_source_set(self):
        noise = _make_noise()
        assert noise.source == "calibration"


# ─────────────────────────────────────────────────────────────
# TC-06x: UQINoise.simulate
# ─────────────────────────────────────────────────────────────

class TestUQINoiseSimulate:

    def test_TC061_no_noise_model_raises(self):
        with patch("uqi_noise.build_noise_model",
                   return_value=(None, None, None)):
            noise = UQINoise("unknown_qpu")
            with pytest.raises(ValueError, match="노이즈 모델 없음"):
                noise.simulate(_bell_circuit())

    def test_TC062_unsupported_sdk_raises(self):
        noise = _make_noise()
        with pytest.raises(ValueError, match="미지원 SDK"):
            noise.simulate(_bell_circuit(), sdk="unsupported_sdk")

    def test_TC063_cudaq_without_kernel_file_raises(self):
        noise = _make_noise()
        with pytest.raises(ValueError, match="kernel_file"):
            noise.simulate(_bell_circuit(), sdk="cudaq")

    def test_TC064_qiskit_sdk_calls_qiskit_simulate(self):
        noise = _make_noise()
        with patch("uqi_noise.simulate_with_noise_qiskit",
                   return_value={"00": 512, "11": 512}) as m:
            result = noise.simulate(_bell_circuit(), sdk="qiskit")
            m.assert_called_once()
            assert result == {"00": 512, "11": 512}

    def test_TC065_pennylane_sdk_calls_pennylane_simulate(self):
        noise = _make_noise()
        with patch("uqi_noise.simulate_with_noise_pennylane",
                   return_value={"00": 600, "11": 424}) as m:
            result = noise.simulate(_bell_circuit(), sdk="pennylane")
            m.assert_called_once()

    def test_TC066_cudaq_with_kernel_file_calls_cudaq_simulate(self):
        noise = _make_noise()
        with patch("uqi_noise.simulate_with_noise_cudaq",
                   return_value={"00": 700, "11": 324}) as m:
            result = noise.simulate(_bell_circuit(), sdk="cudaq",
                                    kernel_file="/tmp/kernel.py")
            m.assert_called_once()


# ─────────────────────────────────────────────────────────────
# TC-07x: UQINoise.compare
# ─────────────────────────────────────────────────────────────

class TestUQINoiseCompare:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.noise = _make_noise()

    def test_TC071_result_dict_keys(self):
        counts_a = {"00": 500, "11": 500}
        counts_b = {"00": 480, "11": 520}
        result = self.noise.compare(counts_a, counts_b)
        assert {"tvd", "fidelity", "dominant_a", "dominant_b",
                "label_a", "label_b"} <= set(result.keys())

    def test_TC072_identical_counts_tvd_zero(self):
        counts = {"00": 500, "11": 500}
        result = self.noise.compare(counts, counts.copy())
        assert result["tvd"] == 0.0

    def test_TC073_identical_counts_fidelity_one(self):
        counts = {"00": 500, "11": 500}
        result = self.noise.compare(counts, counts.copy())
        assert abs(result["fidelity"] - 1.0) < 1e-3

    def test_TC074_completely_different_tvd_one(self):
        counts_a = {"00": 1000}
        counts_b = {"11": 1000}
        result = self.noise.compare(counts_a, counts_b)
        assert abs(result["tvd"] - 1.0) < 1e-9

    def test_TC075_completely_different_fidelity_zero(self):
        counts_a = {"00": 1000}
        counts_b = {"11": 1000}
        result = self.noise.compare(counts_a, counts_b)
        assert result["fidelity"] == 0.0

    def test_TC076_tvd_range_0_to_1(self):
        counts_a = {"00": 600, "11": 400}
        counts_b = {"00": 400, "11": 600}
        result = self.noise.compare(counts_a, counts_b)
        assert 0.0 <= result["tvd"] <= 1.0

    def test_TC077_fidelity_range_0_to_1(self):
        counts_a = {"00": 600, "11": 400}
        counts_b = {"00": 400, "11": 600}
        result = self.noise.compare(counts_a, counts_b)
        assert 0.0 <= result["fidelity"] <= 1.0

    def test_TC078_custom_labels_stored(self):
        counts = {"00": 500, "11": 500}
        result = self.noise.compare(counts, counts.copy(),
                                    label_a="ideal", label_b="noise")
        assert result["label_a"] == "ideal"
        assert result["label_b"] == "noise"

    def test_TC079_bitstring_whitespace_normalized(self):
        counts_a = {"0 0": 500, "1 1": 500}
        counts_b = {"00": 500, "11": 500}
        result = self.noise.compare(counts_a, counts_b)
        assert result["tvd"] == 0.0

    def test_TC07A_dominant_key_correct(self):
        counts_a = {"00": 800, "11": 200}
        counts_b = {"00": 700, "11": 300}
        result = self.noise.compare(counts_a, counts_b)
        assert result["dominant_a"] == "00"
        assert result["dominant_b"] == "00"

    def test_TC07B_tvd_rounded_to_4_decimal(self):
        counts_a = {"00": 333, "11": 667}
        counts_b = {"00": 500, "11": 500}
        result = self.noise.compare(counts_a, counts_b)
        assert len(str(result["tvd"]).split(".")[-1]) <= 4


if __name__ == "__main__":
    pytest.main([__file__, "-v",
                 "--cov=uqi_noise", "--cov-report=term-missing"])