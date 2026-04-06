# test_uqi_optimizer.py

import os
import sys
import math
import pytest
import numpy as np
from unittest.mock import MagicMock, patch
from qiskit import QuantumCircuit
from qiskit.circuit import Parameter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from uqi_optimizer import (
    analyze_circuit,
    select_opt_engine,
    select_map_engine,
    _build_coupling_map,
    _check_t2_depth,
    COMBINATIONS,
    UQIOptimizer,
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


def _rx_circuit(theta=0.5) -> QuantumCircuit:
    qc = QuantumCircuit(2, 2)
    qc.rx(theta, 0)
    qc.rz(theta, 1)
    qc.cx(0, 1)
    qc.measure([0, 1], [0, 1])
    return qc


def _parameterized_circuit() -> QuantumCircuit:
    theta = Parameter("theta")
    qc = QuantumCircuit(2, 2)
    qc.rx(theta, 0)
    qc.cx(0, 1)
    qc.measure([0, 1], [0, 1])
    return qc


def _t_gate_circuit() -> QuantumCircuit:
    """T 게이트 비율 높은 소규모 회로"""
    qc = QuantumCircuit(3, 3)
    for _ in range(10):
        qc.t(0)
        qc.t(1)
        qc.t(2)
    qc.cx(0, 1)
    qc.measure([0, 1, 2], [0, 1, 2])
    return qc


def _base_calibration(n=5) -> dict:
    edges = [[i, i+1] for i in range(n-1)]
    return {
        "coupling_map": edges,
        "basis_gates": ["cx", "rz", "x", "measure"],
        "num_qubits": n,
        "avg_1q_error": 0.001,
        "avg_2q_error": 0.01,
        "avg_ro_error": 0.02,
        "avg_1q_ns": 50.0,
        "avg_2q_ns": 300.0,
        "avg_t2_ms": 100.0,
    }


# ─────────────────────────────────────────────────────────────
# TC-01x: analyze_circuit
# ─────────────────────────────────────────────────────────────

class TestAnalyzeCircuit:

    def test_TC011_returns_dict_with_required_keys(self):
        result = analyze_circuit(_bell_circuit())
        required = {"num_qubits", "total_gates", "depth", "t_ratio",
                    "two_q_ratio", "pauli_ratio", "rot_ratio",
                    "is_parameterized", "ops"}
        assert required <= set(result.keys())

    def test_TC012_bell_circuit_gate_count(self):
        result = analyze_circuit(_bell_circuit())
        # h(1) + cx(1) + measure(2) = 4
        assert result["total_gates"] == 4

    def test_TC013_bell_circuit_cx_in_ops(self):
        result = analyze_circuit(_bell_circuit())
        assert result["ops"].get("cx", 0) == 1

    def test_TC014_two_q_ratio_bell(self):
        result = analyze_circuit(_bell_circuit())
        # cx 1개 / 전체 4개 = 0.25
        assert abs(result["two_q_ratio"] - 0.25) < 1e-9

    def test_TC015_not_parameterized(self):
        result = analyze_circuit(_bell_circuit())
        assert result["is_parameterized"] is False

    def test_TC016_parameterized_detected(self):
        result = analyze_circuit(_parameterized_circuit())
        assert result["is_parameterized"] is True

    def test_TC017_t_ratio_computed(self):
        qc = _t_gate_circuit()
        result = analyze_circuit(qc)
        assert result["t_ratio"] > 0

    def test_TC018_rot_ratio_computed(self):
        result = analyze_circuit(_rx_circuit())
        assert result["rot_ratio"] > 0

    def test_TC019_depth_positive(self):
        result = analyze_circuit(_bell_circuit())
        assert result["depth"] > 0

    def test_TC01A_empty_circuit_total_gates_zero(self):
        qc = QuantumCircuit(1)
        result = analyze_circuit(qc)
        assert result["total_gates"] == 0

    def test_TC01B_empty_circuit_ratios_zero(self):
        qc = QuantumCircuit(1)
        result = analyze_circuit(qc)
        assert result["t_ratio"] == 0.0
        assert result["two_q_ratio"] == 0.0

    def test_TC01C_active_qubits_counted(self):
        # 3큐비트 회로에서 1큐비트만 사용
        qc = QuantumCircuit(3, 1)
        qc.h(0)
        qc.measure(0, 0)
        result = analyze_circuit(qc)
        assert result["num_qubits"] == 1

    def test_TC01D_pauli_ratio_computed(self):
        qc = QuantumCircuit(2, 2)
        qc.x(0)
        qc.x(1)
        qc.cx(0, 1)
        qc.measure([0, 1], [0, 1])
        result = analyze_circuit(qc)
        assert result["pauli_ratio"] > 0


# ─────────────────────────────────────────────────────────────
# TC-02x: select_opt_engine
# ─────────────────────────────────────────────────────────────

class TestSelectOptEngine:

    def test_TC021_prefer_speed_returns_qiskit_l3(self):
        profile = {"total_gates": 100, "num_qubits": 5,
                   "t_ratio": 0.0, "is_parameterized": False}
        assert select_opt_engine(profile, prefer_speed=True) == "qiskit_l3"

    def test_TC022_parameterized_returns_qiskit_l3(self):
        profile = {"total_gates": 100, "num_qubits": 5,
                   "t_ratio": 0.0, "is_parameterized": True}
        assert select_opt_engine(profile) == "qiskit_l3"

    def test_TC023_large_gates_returns_qiskit_l3(self):
        profile = {"total_gates": 15000, "num_qubits": 5,
                   "t_ratio": 0.0, "is_parameterized": False}
        assert select_opt_engine(profile) == "qiskit_l3"

    def test_TC024_large_qubits_returns_qiskit_l3(self):
        profile = {"total_gates": 100, "num_qubits": 25,
                   "t_ratio": 0.0, "is_parameterized": False}
        assert select_opt_engine(profile) == "qiskit_l3"

    def test_TC025_high_t_ratio_small_circuit_returns_quizx(self):
        profile = {"total_gates": 100, "num_qubits": 10,
                   "t_ratio": 0.5, "is_parameterized": False}
        assert select_opt_engine(profile) == "quizx"

    def test_TC026_medium_circuit_returns_tket(self):
        profile = {"total_gates": 1000, "num_qubits": 10,
                   "t_ratio": 0.0, "is_parameterized": False}
        assert select_opt_engine(profile) == "tket"

    def test_TC027_t_ratio_threshold_boundary(self):
        # t_ratio 정확히 0.3 → quizx 조건 미충족 (> 0.3 이어야 함)
        profile = {"total_gates": 100, "num_qubits": 10,
                   "t_ratio": 0.3, "is_parameterized": False}
        assert select_opt_engine(profile) == "tket"

    def test_TC028_quizx_gate_limit_boundary(self):
        # total > 500 → quizx 조건 미충족
        profile = {"total_gates": 501, "num_qubits": 10,
                   "t_ratio": 0.5, "is_parameterized": False}
        assert select_opt_engine(profile) == "tket"

    def test_TC029_quizx_qubit_limit_boundary(self):
        # num_qubits=16 > 15 → quizx 조건 미충족, total<=5000 → tket
        profile = {"total_gates": 100, "num_qubits": 16,
                   "t_ratio": 0.5, "is_parameterized": False}
        assert select_opt_engine(profile) == "tket"

    def test_TC02A_quizx_qubit_limit_large(self):
        # num_qubits=21 > 20 → qiskit_l3
        profile = {"total_gates": 100, "num_qubits": 21,
                   "t_ratio": 0.5, "is_parameterized": False}
        assert select_opt_engine(profile) == "qiskit_l3"


# ─────────────────────────────────────────────────────────────
# TC-03x: select_map_engine
# ─────────────────────────────────────────────────────────────

class TestSelectMapEngine:

    def test_TC031_prefer_speed_returns_sabre(self):
        profile = {"total_gates": 100}
        assert select_map_engine(profile, prefer_speed=True) == "qiskit_sabre"

    def test_TC032_large_circuit_returns_sabre(self):
        profile = {"total_gates": 6000}
        assert select_map_engine(profile) == "qiskit_sabre"

    def test_TC033_small_circuit_returns_sabre(self):
        # 현재 구현상 항상 sabre
        profile = {"total_gates": 100}
        assert select_map_engine(profile) == "qiskit_sabre"

    def test_TC034_boundary_5000_gates(self):
        profile = {"total_gates": 5000}
        assert select_map_engine(profile) == "qiskit_sabre"


# ─────────────────────────────────────────────────────────────
# TC-04x: _build_coupling_map
# ─────────────────────────────────────────────────────────────

class TestBuildCouplingMap:

    def test_TC041_none_when_no_coupling_map(self):
        result = _build_coupling_map({}, "ibm_fez")
        assert result is None

    def test_TC042_all_to_all_with_num_qubits(self):
        cal = {"coupling_map": "all_to_all", "num_qubits": 3}
        cm = _build_coupling_map(cal, "ibm_fez")
        assert cm is not None
        edges = list(cm.get_edges())
        # 3큐비트 all_to_all: 6 방향 엣지
        assert len(edges) == 6

    def test_TC043_all_to_all_no_qubits_returns_none(self):
        cal = {"coupling_map": "all_to_all", "num_qubits": 0}
        result = _build_coupling_map(cal, "ibm_fez")
        assert result is None

    def test_TC044_list_coupling_map(self):
        cal = {"coupling_map": [[0, 1], [1, 2], [2, 0]]}
        cm = _build_coupling_map(cal, "ibm_fez")
        assert cm is not None

    def test_TC045_empty_list_returns_none(self):
        cal = {"coupling_map": []}
        result = _build_coupling_map(cal, "ibm_fez")
        assert result is None

    def test_TC046_iqm_string_qubit_names_converted(self):
        cal = {"coupling_map": [["QB1", "QB2"], ["QB2", "QB3"]]}
        cm = _build_coupling_map(cal, "iqm_garnet")
        assert cm is not None
        edges = list(cm.get_edges())
        # QB1→0, QB2→1, QB3→2
        assert (0, 1) in edges
        assert (1, 2) in edges

    def test_TC047_iqm_natural_sort_order(self):
        cal = {"coupling_map": [["QB10", "QB2"], ["QB2", "QB1"]]}
        cm = _build_coupling_map(cal, "iqm_garnet")
        assert cm is not None

    def test_TC048_non_iqm_string_qubit_not_converted(self):
        # IQM이 아닌 QPU에서 문자열 qubit → 그대로 전달 (오류 없이 처리)
        cal = {"coupling_map": [[0, 1], [1, 2]]}
        cm = _build_coupling_map(cal, "ibm_fez")
        assert cm is not None


# ─────────────────────────────────────────────────────────────
# TC-05x: _check_t2_depth
# ─────────────────────────────────────────────────────────────

class TestCheckT2Depth:

    def test_TC051_no_t2_returns_true(self):
        qc = _bell_circuit()
        assert _check_t2_depth(qc, {}) is True

    def test_TC052_no_q2_ns_returns_true(self):
        qc = _bell_circuit()
        assert _check_t2_depth(qc, {"avg_t2_ms": 100.0}) is True

    def test_TC053_within_t2_returns_true(self):
        qc = _bell_circuit()
        # depth ≈ 3, q2_ns=300 → est=900ns << t2=100ms=1e8ns
        cal = {"avg_t2_ms": 100.0, "avg_2q_ns": 300.0}
        assert _check_t2_depth(qc, cal) is True

    def test_TC054_exceeds_t2_returns_false(self):
        # 깊이 큰 회로 생성
        qc = QuantumCircuit(2, 2)
        for _ in range(1000):
            qc.cx(0, 1)
        qc.measure([0, 1], [0, 1])
        # t2 = 0.001ms = 1000ns, q2_ns = 300 → est = 300*1001 >> 1000ns
        cal = {"avg_t2_ms": 0.001, "avg_2q_ns": 300.0}
        assert _check_t2_depth(qc, cal) is False

    def test_TC055_boundary_exactly_at_t2_returns_true(self):
        qc = QuantumCircuit(1)
        # depth=0, est=0 ≤ t2
        cal = {"avg_t2_ms": 0.001, "avg_2q_ns": 300.0}
        assert _check_t2_depth(qc, cal) is True


# ─────────────────────────────────────────────────────────────
# TC-06x: COMBINATIONS 상수
# ─────────────────────────────────────────────────────────────

class TestCombinations:

    def test_TC061_required_combinations_exist(self):
        for key in ["qiskit+sabre", "tket+sabre", "quizx+sabre",
                    "appx+sabre", "qiskit+tket", "tket+tket"]:
            assert key in COMBINATIONS

    def test_TC062_each_combination_is_tuple_of_two(self):
        for key, val in COMBINATIONS.items():
            assert isinstance(val, tuple)
            assert len(val) == 2

    def test_TC063_qiskit_sabre_engines(self):
        opt, map_ = COMBINATIONS["qiskit+sabre"]
        assert opt == "qiskit_l3"
        assert map_ == "qiskit_sabre"

    def test_TC064_appx_sabre_engines(self):
        opt, map_ = COMBINATIONS["appx+sabre"]
        assert opt == "qiskit_l3_appx"
        assert map_ == "qiskit_sabre"

    def test_TC065_tket_sabre_engines(self):
        opt, map_ = COMBINATIONS["tket+sabre"]
        assert opt == "tket"
        assert map_ == "qiskit_sabre"


# ─────────────────────────────────────────────────────────────
# TC-07x: UQIOptimizer 초기화
# ─────────────────────────────────────────────────────────────

class TestUQIOptimizerInit:

    def test_TC071_calibration_stored(self):
        cal = _base_calibration()
        opt = UQIOptimizer(cal)
        assert opt.calibration is cal

    def test_TC072_empty_calibration_accepted(self):
        opt = UQIOptimizer({})
        assert opt.calibration == {}

    def test_TC073_instance_created(self):
        opt = UQIOptimizer(_base_calibration())
        assert isinstance(opt, UQIOptimizer)


# ─────────────────────────────────────────────────────────────
# TC-08x: collect_metadata
# ─────────────────────────────────────────────────────────────

class TestCollectMetadata:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.opt = UQIOptimizer(_base_calibration())

    def test_TC081_required_keys_present(self):
        result = {
            "profile": {"num_qubits": 2, "total_gates": 4, "depth": 3,
                        "t_ratio": 0.0, "two_q_ratio": 0.25,
                        "pauli_ratio": 0.0, "rot_ratio": 0.0},
            "combination": "qiskit+sabre",
            "opt_engine": "qiskit_l3",
            "map_engine": "qiskit_sabre",
            "gate_reduction": 0.1,
            "depth_reduction": 0.05,
            "opt1_gates": 3,
            "opt1_depth": 2,
            "opt_time_sec": 0.5,
            "map_time_sec": 0.3,
            "total_time_sec": 0.8,
            "equivalent": True,
            "t2_ok": True,
            "ok": True,
        }
        meta = self.opt.collect_metadata("test_circuit", result, "ibm_fez")
        required = {
            "circuit_name", "algorithm_file", "qpu_name", "combination", "opt_engine",
            "map_engine", "num_qubits", "orig_gates", "orig_depth",
            "gate_reduction", "depth_reduction", "ok", "timestamp"
        }
        assert required <= set(meta.keys())

    def test_TC082_circuit_name_stored(self):
        meta = self.opt.collect_metadata("my_circuit", {"profile": {}}, "ibm_fez")
        assert meta["circuit_name"] == "my_circuit"

    def test_TC083_qpu_name_stored(self):
        meta = self.opt.collect_metadata("c", {"profile": {}}, "iqm_garnet")
        assert meta["qpu_name"] == "iqm_garnet"

    def test_TC084_ok_value_propagated(self):
        meta = self.opt.collect_metadata("c", {"profile": {}, "ok": False}, "ibm_fez")
        assert meta["ok"] is False

    def test_TC085_timestamp_format(self):
        import re
        meta = self.opt.collect_metadata("c", {"profile": {}}, "ibm_fez")
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", meta["timestamp"])

    def test_TC086_missing_profile_fields_return_none(self):
        meta = self.opt.collect_metadata("c", {"profile": {}}, "ibm_fez")
        assert meta["num_qubits"] is None
        assert meta["orig_gates"] is None

    def test_TC087_algorithm_file_stored(self):
        meta = self.opt.collect_metadata("c", {"profile": {}}, "ibm_fez",
                                         algorithm_file="alg-files/test.py")
        assert meta["algorithm_file"] == "alg-files/test.py"

    def test_TC088_algorithm_file_defaults_to_empty(self):
        meta = self.opt.collect_metadata("c", {"profile": {}}, "ibm_fez")
        assert meta["algorithm_file"] == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v",
                 "--cov=uqi_optimizer", "--cov-report=term-missing"])