# test_uqi_qec.py

import os
import sys
import pytest
import numpy as np
from unittest.mock import MagicMock, patch
from qiskit import QuantumCircuit

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from uqi_qec import (
    analyze_qec_necessity,
    encode_bit_flip,
    encode_phase_flip,
    measure_overhead,
    QEC_CODES,
    UQIQEC,
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


def _simple_circuit(n=1) -> QuantumCircuit:
    qc = QuantumCircuit(n)
    qc.h(0)
    return qc


def _noise_cmp(fidelity=0.98, tvd=0.02) -> dict:
    return {"fidelity": fidelity, "tvd": tvd}


def _base_calibration() -> dict:
    return {
        "avg_t2_ms": 100.0,
        "avg_2q_ns": 300.0,
    }


# ─────────────────────────────────────────────────────────────
# TC-01x: analyze_qec_necessity
# ─────────────────────────────────────────────────────────────

class TestAnalyzeQecNecessity:

    def test_TC011_result_has_required_keys(self):
        result = analyze_qec_necessity(_noise_cmp())
        assert {"necessity", "fidelity", "tvd", "t2_ratio",
                "reasons", "recommended_codes"} <= set(result.keys())

    def test_TC012_high_fidelity_unnecessary(self):
        result = analyze_qec_necessity(_noise_cmp(fidelity=0.995))
        assert result["necessity"] == "unnecessary"

    def test_TC013_mid_fidelity_recommended(self):
        result = analyze_qec_necessity(_noise_cmp(fidelity=0.97))
        assert result["necessity"] == "recommended"

    def test_TC014_low_fidelity_required(self):
        result = analyze_qec_necessity(_noise_cmp(fidelity=0.90))
        assert result["necessity"] == "required"

    def test_TC015_fidelity_boundary_0_99_unnecessary(self):
        result = analyze_qec_necessity(_noise_cmp(fidelity=0.99))
        assert result["necessity"] == "unnecessary"

    def test_TC016_fidelity_boundary_0_95_recommended(self):
        result = analyze_qec_necessity(_noise_cmp(fidelity=0.95))
        assert result["necessity"] == "recommended"

    def test_TC017_high_tvd_upgrades_unnecessary_to_recommended(self):
        result = analyze_qec_necessity(_noise_cmp(fidelity=0.995, tvd=0.1))
        assert result["necessity"] == "recommended"

    def test_TC018_low_tvd_no_upgrade(self):
        result = analyze_qec_necessity(_noise_cmp(fidelity=0.995, tvd=0.03))
        assert result["necessity"] == "unnecessary"

    def test_TC019_tvd_above_threshold_adds_bit_flip(self):
        result = analyze_qec_necessity(_noise_cmp(fidelity=0.995, tvd=0.1))
        assert "bit_flip" in result["recommended_codes"]

    def test_TC01A_mid_fidelity_recommends_bit_phase_flip(self):
        result = analyze_qec_necessity(_noise_cmp(fidelity=0.97))
        assert "bit_flip" in result["recommended_codes"]
        assert "phase_flip" in result["recommended_codes"]

    def test_TC01B_low_fidelity_recommends_shor_steane(self):
        result = analyze_qec_necessity(_noise_cmp(fidelity=0.90))
        assert "shor" in result["recommended_codes"]
        assert "steane" in result["recommended_codes"]

    def test_TC01C_no_t2_ratio_without_calibration(self):
        result = analyze_qec_necessity(_noise_cmp())
        assert result["t2_ratio"] is None

    def test_TC01D_t2_ratio_computed_with_calibration(self):
        qc = QuantumCircuit(2)
        qc.cx(0, 1)  # depth=1
        cal = _base_calibration()
        result = analyze_qec_necessity(_noise_cmp(), calibration=cal, qc=qc)
        assert result["t2_ratio"] is not None
        assert result["t2_ratio"] > 0

    def test_TC01E_high_t2_ratio_forces_required(self):
        # depth 크게 → t2_ratio > 10 유도
        qc = QuantumCircuit(2)
        for _ in range(5000):
            qc.cx(0, 1)
        cal = {"avg_t2_ms": 0.001, "avg_2q_ns": 300.0}
        result = analyze_qec_necessity(_noise_cmp(fidelity=0.995), calibration=cal, qc=qc)
        assert result["necessity"] == "required"

    def test_TC01F_large_circuit_removes_steane_shor(self):
        qc = QuantumCircuit(11)
        for i in range(10):
            qc.cx(i, i+1 if i+1 < 11 else 0)
        result = analyze_qec_necessity(_noise_cmp(fidelity=0.90), qc=qc)
        assert "steane" not in result["recommended_codes"]
        assert "shor" not in result["recommended_codes"]

    def test_TC01G_large_circuit_required_adds_surface(self):
        qc = QuantumCircuit(11)
        for i in range(10):
            qc.cx(i, i+1 if i+1 < 11 else 0)
        result = analyze_qec_necessity(_noise_cmp(fidelity=0.90), qc=qc)
        assert "surface" in result["recommended_codes"]

    def test_TC01H_high_t_ratio_adds_steane(self):
        qc = QuantumCircuit(2)
        for _ in range(10):
            qc.t(0)
        qc.cx(0, 1)
        result = analyze_qec_necessity(_noise_cmp(fidelity=0.97), qc=qc)
        assert "steane" in result["recommended_codes"]

    def test_TC01I_reasons_list_not_empty(self):
        result = analyze_qec_necessity(_noise_cmp())
        assert len(result["reasons"]) >= 1

    def test_TC01J_no_duplicate_codes(self):
        result = analyze_qec_necessity(_noise_cmp(fidelity=0.90, tvd=0.1))
        codes = result["recommended_codes"]
        assert len(codes) == len(set(codes))


# ─────────────────────────────────────────────────────────────
# TC-02x: encode_bit_flip
# ─────────────────────────────────────────────────────────────

class TestEncodeBitFlip:

    def test_TC021_output_is_quantum_circuit(self):
        qc = _simple_circuit(1)
        result = encode_bit_flip(qc)
        assert isinstance(result, QuantumCircuit)

    def test_TC022_qubit_count_tripled(self):
        qc = _simple_circuit(2)
        result = encode_bit_flip(qc)
        assert result.num_qubits >= 6  # 2*3 + ancilla

    def test_TC023_single_qubit_encoded(self):
        qc = _simple_circuit(1)
        result = encode_bit_flip(qc)
        assert result.num_qubits >= 3

    def test_TC024_has_classical_register(self):
        qc = _simple_circuit(1)
        result = encode_bit_flip(qc)
        assert len(result.cregs) >= 1

    def test_TC025_has_cx_gates(self):
        qc = _simple_circuit(1)
        result = encode_bit_flip(qc)
        ops = result.count_ops()
        assert ops.get("cx", 0) > 0

    def test_TC026_has_measure_gates(self):
        qc = _simple_circuit(1)
        result = encode_bit_flip(qc)
        ops = result.count_ops()
        assert ops.get("measure", 0) > 0

    def test_TC027_barrier_and_measure_skipped_from_original(self):
        qc = QuantumCircuit(1, 1)
        qc.h(0)
        qc.measure(0, 0)
        # measure는 스킵되어야 함 (예외 없이 처리)
        result = encode_bit_flip(qc)
        assert isinstance(result, QuantumCircuit)

    def test_TC028_two_qubit_circuit_encoded(self):
        qc = QuantumCircuit(2)
        qc.h(0)
        qc.cx(0, 1)
        result = encode_bit_flip(qc)
        assert result.num_qubits >= 6


# ─────────────────────────────────────────────────────────────
# TC-03x: encode_phase_flip
# ─────────────────────────────────────────────────────────────

class TestEncodePhaseFlip:

    def test_TC031_output_is_quantum_circuit(self):
        qc = _simple_circuit(1)
        result = encode_phase_flip(qc)
        assert isinstance(result, QuantumCircuit)

    def test_TC032_qubit_count_tripled(self):
        qc = _simple_circuit(2)
        result = encode_phase_flip(qc)
        assert result.num_qubits == 6

    def test_TC033_has_h_gates(self):
        qc = _simple_circuit(1)
        result = encode_phase_flip(qc)
        ops = result.count_ops()
        assert ops.get("h", 0) > 0

    def test_TC034_has_measure_gates(self):
        qc = _simple_circuit(1)
        result = encode_phase_flip(qc)
        ops = result.count_ops()
        assert ops.get("measure", 0) > 0

    def test_TC035_has_classical_register(self):
        qc = _simple_circuit(1)
        result = encode_phase_flip(qc)
        assert len(result.cregs) >= 1

    def test_TC036_barrier_measure_skipped(self):
        qc = QuantumCircuit(1, 1)
        qc.h(0)
        qc.measure(0, 0)
        result = encode_phase_flip(qc)
        assert isinstance(result, QuantumCircuit)


# ─────────────────────────────────────────────────────────────
# TC-04x: measure_overhead
# ─────────────────────────────────────────────────────────────

class TestMeasureOverhead:

    def test_TC041_result_has_required_keys(self):
        qc_orig = _simple_circuit(1)
        qc_enc  = encode_bit_flip(qc_orig)
        result  = measure_overhead(qc_orig, qc_enc)
        assert {"orig_qubits", "enc_qubits", "qubit_overhead",
                "orig_gates", "enc_gates", "gate_overhead",
                "orig_depth", "enc_depth", "depth_overhead"} <= set(result.keys())

    def test_TC042_qubit_overhead_correct(self):
        qc_orig = _simple_circuit(1)
        qc_enc  = encode_bit_flip(qc_orig)
        result  = measure_overhead(qc_orig, qc_enc)
        assert result["orig_qubits"] == 1
        assert result["enc_qubits"] >= 3

    def test_TC043_gate_overhead_positive(self):
        qc_orig = _simple_circuit(1)
        qc_enc  = encode_bit_flip(qc_orig)
        result  = measure_overhead(qc_orig, qc_enc)
        assert result["gate_overhead"] > 1.0

    def test_TC044_depth_overhead_positive(self):
        qc_orig = _simple_circuit(1)
        qc_enc  = encode_bit_flip(qc_orig)
        result  = measure_overhead(qc_orig, qc_enc)
        assert result["depth_overhead"] > 0

    def test_TC045_phase_flip_overhead_correct(self):
        qc_orig = _simple_circuit(2)
        qc_enc  = encode_phase_flip(qc_orig)
        result  = measure_overhead(qc_orig, qc_enc)
        assert result["qubit_overhead"] == 3.0


# ─────────────────────────────────────────────────────────────
# TC-05x: QEC_CODES 상수
# ─────────────────────────────────────────────────────────────

class TestQecCodes:

    def test_TC051_required_codes_present(self):
        for code in ["bit_flip", "phase_flip", "shor", "steane", "surface"]:
            assert code in QEC_CODES

    def test_TC052_each_code_has_required_keys(self):
        for code, info in QEC_CODES.items():
            assert {"name", "qubits", "description",
                    "overhead", "implemented"} <= set(info.keys())

    def test_TC053_bit_flip_implemented(self):
        assert QEC_CODES["bit_flip"]["implemented"] is True

    def test_TC054_phase_flip_implemented(self):
        assert QEC_CODES["phase_flip"]["implemented"] is True

    def test_TC055_shor_not_implemented(self):
        assert QEC_CODES["shor"]["implemented"] is False

    def test_TC056_steane_not_implemented(self):
        assert QEC_CODES["steane"]["implemented"] is False

    def test_TC057_surface_not_implemented(self):
        assert QEC_CODES["surface"]["implemented"] is False

    def test_TC058_bit_flip_qubits_3(self):
        assert QEC_CODES["bit_flip"]["qubits"] == 3

    def test_TC059_phase_flip_qubits_3(self):
        assert QEC_CODES["phase_flip"]["qubits"] == 3

    def test_TC05A_shor_qubits_9(self):
        assert QEC_CODES["shor"]["qubits"] == 9

    def test_TC05B_steane_qubits_7(self):
        assert QEC_CODES["steane"]["qubits"] == 7


# ─────────────────────────────────────────────────────────────
# TC-06x: UQIQEC.__init__
# ─────────────────────────────────────────────────────────────

class TestUQIQECInit:

    def test_TC061_empty_calibration_defaults(self):
        qec = UQIQEC()
        assert qec.calibration == {}

    def test_TC062_calibration_stored(self):
        cal = _base_calibration()
        qec = UQIQEC(calibration=cal)
        assert qec.calibration is cal

    def test_TC063_none_calibration_defaults_to_empty(self):
        qec = UQIQEC(calibration=None)
        assert qec.calibration == {}


# ─────────────────────────────────────────────────────────────
# TC-07x: UQIQEC.encode
# ─────────────────────────────────────────────────────────────

class TestUQIQECEncode:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.qec = UQIQEC()

    def test_TC071_bit_flip_returns_circuit(self):
        qc = _simple_circuit(1)
        result = self.qec.encode(qc, "bit_flip")
        assert isinstance(result, QuantumCircuit)

    def test_TC072_phase_flip_returns_circuit(self):
        qc = _simple_circuit(1)
        result = self.qec.encode(qc, "phase_flip")
        assert isinstance(result, QuantumCircuit)

    def test_TC073_unimplemented_code_raises(self):
        qc = _simple_circuit(1)
        with pytest.raises(NotImplementedError):
            self.qec.encode(qc, "shor")

    def test_TC074_unknown_code_raises(self):
        qc = _simple_circuit(1)
        with pytest.raises((NotImplementedError, ValueError)):
            self.qec.encode(qc, "unknown_code")

    def test_TC075_steane_raises_not_implemented(self):
        qc = _simple_circuit(1)
        with pytest.raises(NotImplementedError):
            self.qec.encode(qc, "steane")

    def test_TC076_surface_raises_not_implemented(self):
        qc = _simple_circuit(1)
        with pytest.raises(NotImplementedError):
            self.qec.encode(qc, "surface")

    def test_TC077_bit_flip_qubit_count_tripled(self):
        qc = _simple_circuit(2)
        result = self.qec.encode(qc, "bit_flip")
        assert result.num_qubits >= 6

    def test_TC078_measurement_removed_before_encode(self):
        # 측정이 있는 회로도 예외 없이 처리
        qc = _bell_circuit()
        result = self.qec.encode(qc, "bit_flip")
        assert isinstance(result, QuantumCircuit)


# ─────────────────────────────────────────────────────────────
# TC-08x: UQIQEC.recommend
# ─────────────────────────────────────────────────────────────

class TestUQIQECRecommend:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.qec = UQIQEC()

    def test_TC081_unnecessary_returns_none(self):
        qc = _simple_circuit(1)
        result = self.qec.recommend(qc, _noise_cmp(fidelity=0.995))
        assert result == "none"

    def test_TC082_recommended_returns_implemented_code(self):
        qc = _simple_circuit(1)
        result = self.qec.recommend(qc, _noise_cmp(fidelity=0.97))
        assert result in ["bit_flip", "phase_flip"]

    def test_TC083_required_returns_implemented_code(self):
        qc = _simple_circuit(1)
        result = self.qec.recommend(qc, _noise_cmp(fidelity=0.90))
        assert result in ["bit_flip", "phase_flip"]

    def test_TC084_returns_string(self):
        qc = _simple_circuit(1)
        result = self.qec.recommend(qc, _noise_cmp(fidelity=0.90))
        assert isinstance(result, str)

    def test_TC085_no_implemented_codes_returns_first_or_none(self):
        # 모든 코드가 미구현인 경우 → 첫번째 코드 또는 none 반환
        qc = QuantumCircuit(11)
        for i in range(10):
            qc.cx(i, i+1 if i+1 < 11 else 0)
        result = self.qec.recommend(qc, _noise_cmp(fidelity=0.90))
        assert isinstance(result, str)


if __name__ == "__main__":
    pytest.main([__file__, "-v",
                 "--cov=uqi_qec", "--cov-report=term-missing"])