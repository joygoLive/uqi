# test_uqi_qir_converter.py

import os
import sys
import pytest
from unittest.mock import MagicMock, patch
from qiskit import QuantumCircuit

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from uqi_qir_converter import UQIQIRConverter


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _make_extractor(framework=None, circuits=None, perceval_circuits=None, frameworks=None):
    ext = MagicMock()
    ext.framework = framework
    # frameworks: 복수 framework 지원 (None이면 framework 단일 항목으로 설정)
    ext.frameworks = frameworks if frameworks is not None else ([framework] if framework else [])
    ext.circuits = circuits or {}
    ext.perceval_circuits = perceval_circuits or {}
    return ext


def _make_converter(framework=None, circuits=None, perceval_circuits=None):
    ext = _make_extractor(framework, circuits, perceval_circuits)
    return UQIQIRConverter(ext), ext


SIMPLE_QASM = """\
OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
h q[0];
cx q[0], q[1];
measure q[0] -> c[0];
measure q[1] -> c[1];
"""


# ─────────────────────────────────────────────────────────────
# TC-01x: 초기화
# ─────────────────────────────────────────────────────────────

class TestInitialState:

    def test_TC011_extractor_stored(self):
        ext = _make_extractor()
        conv = UQIQIRConverter(ext)
        assert conv.extractor is ext

    def test_TC012_qasm_results_empty(self):
        conv, _ = _make_converter()
        assert conv.qasm_results == {}

    def test_TC013_qir_results_empty(self):
        conv, _ = _make_converter()
        assert conv.qir_results == {}

    def test_TC014_errors_empty(self):
        conv, _ = _make_converter()
        assert conv.errors == {}


# ─────────────────────────────────────────────────────────────
# TC-02x: convert_all — framework 분기
# ─────────────────────────────────────────────────────────────

class TestConvertAll:

    def test_TC021_pennylane_routes_to_qasm_handoff(self):
        conv, _ = _make_converter(
            framework="PennyLane",
            circuits={"circ_a": SIMPLE_QASM}
        )
        with patch.object(conv, "_qasm_to_qir") as m:
            conv.convert_all()
            assert conv.qasm_results.get("circ_a") == SIMPLE_QASM

    def test_TC022_qrisp_routes_to_qasm_handoff(self):
        conv, _ = _make_converter(
            framework="Qrisp",
            circuits={"circ_a": SIMPLE_QASM}
        )
        conv.convert_all()
        assert "circ_a" in conv.qasm_results

    def test_TC023_cudaq_routes_to_qasm_handoff(self):
        conv, _ = _make_converter(
            framework="CUDAQ",
            circuits={"circ_a": SIMPLE_QASM}
        )
        conv.convert_all()
        assert "circ_a" in conv.qasm_results

    def test_TC024_qiskit_routes_to_convert_qiskit(self):
        qc = QuantumCircuit(2, 2)
        qc.h(0)
        qc.cx(0, 1)
        qc.measure([0, 1], [0, 1])
        conv, _ = _make_converter(
            framework="Qiskit",
            circuits={"circ_a": qc}
        )
        with patch.object(conv, "_convert_qiskit") as m:
            conv.convert_all()
            m.assert_called_once_with("circ_a", qc)

    def test_TC025_perceval_qasm_handoff(self):
        """Perceval도 subprocess 이관 후 QASM 문자열로 extractor.circuits에 저장됨"""
        conv, _ = _make_converter(
            framework="Perceval",
            circuits={"circ_a": SIMPLE_QASM}
        )
        conv.convert_all()
        assert conv.qasm_results.get("circ_a") == SIMPLE_QASM

    def test_TC025b_perceval_legacy_fallback(self):
        """perceval_circuits가 있고 circuits가 비어있으면 _convert_perceval 레거시 경로 사용"""
        circuit_info = (MagicMock(), [1, 0])
        conv, _ = _make_converter(
            framework="Perceval",
            circuits={},
            perceval_circuits={"circ_a": circuit_info}
        )
        with patch.object(conv, "_convert_perceval") as m:
            conv.convert_all()
            m.assert_called_once_with("circ_a", circuit_info)

    def test_TC026_no_circuits_skips_processing(self):
        conv, _ = _make_converter(framework="PennyLane", circuits={})
        conv.convert_all()
        assert conv.qasm_results == {}

    def test_TC027_returns_qir_results_dict(self):
        conv, _ = _make_converter(framework="PennyLane", circuits={})
        result = conv.convert_all()
        assert isinstance(result, dict)

    def test_TC028_non_string_qasm_skipped(self):
        # circuits 값이 str이 아닌 경우 qasm_results에 추가 안 됨
        conv, _ = _make_converter(
            framework="PennyLane",
            circuits={"circ_a": 12345}  # int, not str
        )
        conv.convert_all()
        assert "circ_a" not in conv.qasm_results


# ─────────────────────────────────────────────────────────────
# TC-03x: _convert_qiskit
# ─────────────────────────────────────────────────────────────

class TestConvertQiskit:

    def test_TC031_qasm_stored(self):
        qc = QuantumCircuit(2, 2)
        qc.h(0)
        qc.cx(0, 1)
        qc.measure([0, 1], [0, 1])
        conv, _ = _make_converter()
        with patch.object(conv, "_qasm_to_qir"):
            conv._convert_qiskit("circ_a", qc)
            assert "circ_a" in conv.qasm_results

    def test_TC032_gphase_filtered(self):
        qc = QuantumCircuit(2, 2)
        qc.h(0)
        qc.measure([0, 1], [0, 1])
        conv, _ = _make_converter()
        with patch.object(conv, "_qasm_to_qir"):
            conv._convert_qiskit("circ_a", qc)
            qasm = conv.qasm_results.get("circ_a", "")
            for line in qasm.splitlines():
                assert not line.strip().startswith("gphase")

    def test_TC033_measure_added_when_no_cregs(self):
        qc = QuantumCircuit(2)
        qc.h(0)
        qc.cx(0, 1)
        # cregs 없음 → measure_all 호출되어야 함
        conv, _ = _make_converter()
        with patch.object(conv, "_qasm_to_qir"):
            conv._convert_qiskit("circ_a", qc)
            assert "circ_a" in conv.qasm_results

    def test_TC034_exception_stored_in_errors(self):
        conv, _ = _make_converter()
        # dumps 실패 유도
        with patch("uqi_qir_converter.UQIQIRConverter._convert_qiskit",
                   wraps=conv._convert_qiskit):
            bad_circuit = MagicMock()
            bad_circuit.cregs = []
            bad_circuit.measure_all.side_effect = Exception("fail")
            conv._convert_qiskit("circ_b", bad_circuit)
            assert "circ_b" in conv.errors

    def test_TC035_qasm_to_qir_called(self):
        qc = QuantumCircuit(2, 2)
        qc.h(0)
        qc.measure([0, 1], [0, 1])
        conv, _ = _make_converter()
        with patch.object(conv, "_qasm_to_qir") as m:
            conv._convert_qiskit("circ_a", qc)
            m.assert_called_once()


# ─────────────────────────────────────────────────────────────
# TC-04x: _qasm_to_qir
# ─────────────────────────────────────────────────────────────

class TestQasmToQir:

    def test_TC041_pyqir_missing_stores_error(self):
        conv, _ = _make_converter()
        with patch.dict("sys.modules", {"pyqir": None}):
            result = conv._qasm_to_qir("circ_a", SIMPLE_QASM)
            assert result is None
            assert "circ_a" in conv.errors

    def test_TC042_invalid_qasm_stores_error(self):
        conv, _ = _make_converter()
        result = conv._qasm_to_qir("circ_a", "INVALID QASM !!!!")
        assert result is None
        assert "circ_a" in conv.errors

    def test_TC043_returns_none_on_failure(self):
        conv, _ = _make_converter()
        with patch.dict("sys.modules", {"pyqir": None}):
            result = conv._qasm_to_qir("circ_a", SIMPLE_QASM)
            assert result is None

    def test_TC044_success_stores_in_qir_results(self):
        conv, _ = _make_converter()
        mock_pyqir = MagicMock()
        mock_module = MagicMock()
        mock_module.bitcode = b"\x00\x01\x02"
        mock_pyqir.Context.return_value = MagicMock()
        mock_pyqir.Module.from_ir.return_value = mock_module

        with patch.dict("sys.modules", {"pyqir": mock_pyqir}):
            with patch.object(conv, "_circuit_to_qir_ll", return_value="fake IR"):
                result = conv._qasm_to_qir("circ_a", SIMPLE_QASM)
                assert "circ_a" in conv.qir_results
                assert result == b"\x00\x01\x02"


# ─────────────────────────────────────────────────────────────
# TC-05x: get_result
# ─────────────────────────────────────────────────────────────

class TestGetResult:

    def test_TC051_required_keys_present(self):
        conv, _ = _make_converter()
        result = conv.get_result("circ_a")
        assert {"qasm", "qir", "qasm_ok", "qir_ok", "error"} <= set(result.keys())

    def test_TC052_nonexistent_name_all_none_false(self):
        conv, _ = _make_converter()
        result = conv.get_result("nonexistent")
        assert result["qasm"] is None
        assert result["qir"] is None
        assert result["qasm_ok"] is False
        assert result["qir_ok"] is False
        assert result["error"] is None

    def test_TC053_qasm_ok_true_when_stored(self):
        conv, _ = _make_converter()
        conv.qasm_results["circ_a"] = SIMPLE_QASM
        result = conv.get_result("circ_a")
        assert result["qasm_ok"] is True
        assert result["qasm"] == SIMPLE_QASM

    def test_TC054_qir_ok_true_when_stored(self):
        conv, _ = _make_converter()
        conv.qir_results["circ_a"] = b"\x00\x01"
        result = conv.get_result("circ_a")
        assert result["qir_ok"] is True

    def test_TC055_error_returned_when_stored(self):
        conv, _ = _make_converter()
        conv.errors["circ_a"] = "QIR 변환 실패"
        result = conv.get_result("circ_a")
        assert result["error"] == "QIR 변환 실패"


# ─────────────────────────────────────────────────────────────
# TC-06x: print_summary
# ─────────────────────────────────────────────────────────────

class TestPrintSummary:

    def test_TC061_no_results_no_exception(self):
        conv, _ = _make_converter()
        conv.print_summary()

    def test_TC062_qasm_ok_shown(self, capsys):
        conv, _ = _make_converter()
        conv.qasm_results["circ_a"] = SIMPLE_QASM
        conv.print_summary()
        out = capsys.readouterr().out
        assert "circ_a" in out
        assert "✓" in out

    def test_TC063_qir_fail_shown(self, capsys):
        conv, _ = _make_converter()
        conv.qasm_results["circ_a"] = SIMPLE_QASM
        # qir_results에 없음 → ✗
        conv.print_summary()
        out = capsys.readouterr().out
        assert "✗" in out

    def test_TC064_error_shown_in_output(self, capsys):
        conv, _ = _make_converter()
        conv.qasm_results["circ_a"] = SIMPLE_QASM
        conv.errors["circ_a"] = "QIR 변환 실패"
        conv.print_summary()
        out = capsys.readouterr().out
        assert "QIR 변환 실패" in out

    def test_TC065_both_ok_shown(self, capsys):
        conv, _ = _make_converter()
        conv.qasm_results["circ_a"] = SIMPLE_QASM
        conv.qir_results["circ_a"]  = b"\x00\x01"
        conv.print_summary()
        out = capsys.readouterr().out
        assert out.count("✓") >= 2


if __name__ == "__main__":
    pytest.main([__file__, "-v",
                 "--cov=uqi_qir_converter", "--cov-report=term-missing"])