# test_uqi_extractor.py

import os
import sys
import json
import math
import unittest
import tempfile
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from uqi_extractor import UQIExtractor


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _write_tmp(content: str, suffix=".py") -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False)
    f.write(content)
    f.close()
    return f.name


def _make_extractor(source: str):
    path = _write_tmp(source)
    return UQIExtractor(path), path


def _mock_op(name, wires, params=None):
    op = MagicMock()
    op.name = name
    op.wires = wires
    op.parameters = params or []
    return op


def _mock_tape(ops, num_wires=None):
    wires = list(range(num_wires or (max(w for op in ops for w in op.wires) + 1 if ops else 1)))
    tape = MagicMock()
    tape.expand.return_value = tape
    tape.operations = ops
    tape.num_wires = len(wires)
    tape.wires = wires
    return tape


# ─────────────────────────────────────────────────────────────
# TC-01x: detect_framework
# ─────────────────────────────────────────────────────────────

class TestDetectFramework:

    def _detect(self, source: str) -> str:
        ext, path = _make_extractor(source)
        try:
            return ext.detect_framework()
        finally:
            os.unlink(path)

    def test_TC011_detect_pennylane_import(self):
        assert self._detect("import pennylane as qml\n") == "PennyLane"

    def test_TC012_detect_pennylane_from(self):
        assert self._detect("from pennylane import qnode\n") == "PennyLane"

    def test_TC013_detect_qml_alias(self):
        assert self._detect("import qml\n") == "PennyLane"

    def test_TC014_detect_qrisp_import(self):
        assert self._detect("import qrisp\n") == "Qrisp"

    def test_TC015_detect_qrisp_from(self):
        assert self._detect("from qrisp import QuantumVariable\n") == "Qrisp"

    def test_TC016_detect_qiskit_import(self):
        assert self._detect("import qiskit\n") == "Qiskit"

    def test_TC017_detect_qiskit_from(self):
        assert self._detect("from qiskit import QuantumCircuit\n") == "Qiskit"

    def test_TC018_detect_cudaq_import(self):
        assert self._detect("import cudaq\n") == "CUDAQ"

    def test_TC019_detect_cudaq_from(self):
        assert self._detect("from cudaq import kernel\n") == "CUDAQ"

    def test_TC01A_detect_perceval_import(self):
        assert self._detect("import perceval as pcvl\n") == "Perceval"

    def test_TC01B_detect_perceval_from(self):
        assert self._detect("from perceval import circuit\n") == "Perceval"

    def test_TC01C_cudaq_priority_over_pennylane(self):
        assert self._detect("import cudaq\nimport pennylane as qml\n") == "CUDAQ"

    def test_TC01D_perceval_priority_over_qiskit(self):
        assert self._detect("import perceval\nimport qiskit\n") == "Perceval"

    def test_TC01E_unknown_raises_valueerror(self):
        with pytest.raises(ValueError):
            self._detect("import numpy as np\n")

    def test_TC01F_file_not_found_raises(self):
        ext = UQIExtractor("/nonexistent/path/algo.py")
        with pytest.raises(FileNotFoundError):
            ext.detect_framework()

    def test_TC01G_framework_stored_on_instance(self):
        ext, path = _make_extractor("import qiskit\n")
        try:
            ext.detect_framework()
            assert ext.framework == "Qiskit"
        finally:
            os.unlink(path)


# ─────────────────────────────────────────────────────────────
# TC-02x: extract_circuits 분기 라우팅
# ─────────────────────────────────────────────────────────────

class TestExtractCircuitsRouting:

    def _ext(self, framework: str) -> UQIExtractor:
        ext = UQIExtractor("dummy.py")
        ext.framework = framework
        return ext

    def test_TC021_routes_pennylane(self):
        ext = self._ext("PennyLane")
        with patch.object(ext, "_extract_pennylane_circuits") as m:
            ext.extract_circuits()
            m.assert_called_once()

    def test_TC022_routes_qrisp(self):
        ext = self._ext("Qrisp")
        with patch.object(ext, "_extract_qrisp_circuits") as m:
            ext.extract_circuits()
            m.assert_called_once()

    def test_TC023_routes_cudaq(self):
        ext = self._ext("CUDAQ")
        with patch.object(ext, "_extract_cudaq_circuits") as m:
            ext.extract_circuits()
            m.assert_called_once()

    def test_TC024_routes_qiskit(self):
        ext = self._ext("Qiskit")
        with patch.object(ext, "_extract_qiskit_circuits") as m:
            ext.extract_circuits()
            m.assert_called_once()

    def test_TC025_routes_perceval(self):
        ext = self._ext("Perceval")
        with patch.object(ext, "_extract_perceval_circuits") as m:
            ext.extract_circuits()
            m.assert_called_once()

    def test_TC026_unknown_framework_raises(self):
        ext = self._ext("UnknownSDK")
        with pytest.raises(NotImplementedError):
            ext.extract_circuits()

    def test_TC027_none_framework_raises(self):
        ext = self._ext(None)
        with pytest.raises(NotImplementedError):
            ext.extract_circuits()


# ─────────────────────────────────────────────────────────────
# TC-03x: _run_subprocess
# ─────────────────────────────────────────────────────────────

class TestRunSubprocess:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.ext = UQIExtractor("dummy.py")

    def test_TC031_valid_json_returned(self):
        script = 'import json\nprint(json.dumps({"ok": True, "val": 42}))\n'
        result = self.ext._run_subprocess(script, timeout=10)
        assert result is not None
        assert result["ok"] is True
        assert result["val"] == 42

    def test_TC032_json_mixed_with_print_output(self):
        script = (
            'import json\n'
            'print("some debug output")\n'
            'print(json.dumps({"result": "found"}))\n'
        )
        result = self.ext._run_subprocess(script, timeout=10)
        assert result is not None
        assert result["result"] == "found"

    def test_TC033_empty_output_returns_none(self):
        script = "x = 1\n"
        result = self.ext._run_subprocess(script, timeout=10)
        assert result is None

    def test_TC034_invalid_json_returns_none(self):
        script = 'print("not json at all")\n'
        result = self.ext._run_subprocess(script, timeout=10)
        assert result is None

    def test_TC035_timeout_returns_none(self):
        script = "import time\ntime.sleep(60)\n"
        result = self.ext._run_subprocess(script, timeout=2)
        assert result is None

    def test_TC036_exception_in_script_returns_none(self):
        script = "raise RuntimeError('boom')\n"
        result = self.ext._run_subprocess(script, timeout=10)
        assert result is None

    def test_TC037_array_json_returns_none(self):
        script = 'print("[1, 2, 3]")\n'
        result = self.ext._run_subprocess(script, timeout=10)
        assert result is None


# ─────────────────────────────────────────────────────────────
# TC-04x: get_total_call_count
# ─────────────────────────────────────────────────────────────

class TestGetTotalCallCount:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.ext = UQIExtractor("dummy.py")

    def test_TC041_empty_counts(self):
        assert self.ext.get_total_call_count() == 0

    def test_TC042_single_qnode(self):
        self.ext.qnode_call_counts = {"circuit_a": 3}
        assert self.ext.get_total_call_count() == 3

    def test_TC043_multiple_qnodes(self):
        self.ext.qnode_call_counts = {"circuit_a": 2, "circuit_b": 5, "circuit_c": 1}
        assert self.ext.get_total_call_count() == 8

    def test_TC044_all_zero_counts(self):
        self.ext.qnode_call_counts = {"circuit_a": 0, "circuit_b": 0}
        assert self.ext.get_total_call_count() == 0


# ─────────────────────────────────────────────────────────────
# TC-05x: tape_to_openqasm
# ─────────────────────────────────────────────────────────────

class TestTapeToOpenQASM:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.ext = UQIExtractor("dummy.py")

    def _qasm(self, ops, num_wires=None):
        tape = _mock_tape(ops, num_wires)
        return self.ext.tape_to_openqasm(tape)

    def test_TC051_header_present(self):
        qasm = self._qasm([], num_wires=1)
        assert "OPENQASM 2.0;" in qasm
        assert 'include "qelib1.inc";' in qasm

    def test_TC052_qreg_creg_declared(self):
        qasm = self._qasm([], num_wires=2)
        assert "qreg q[2];" in qasm
        assert "creg c[2];" in qasm

    def test_TC053_hadamard_gate(self):
        assert "h q[0];" in self._qasm([_mock_op("Hadamard", [0])], num_wires=1)

    def test_TC054_pauli_x_gate(self):
        assert "x q[0];" in self._qasm([_mock_op("PauliX", [0])], num_wires=1)

    def test_TC055_pauli_y_gate(self):
        assert "y q[0];" in self._qasm([_mock_op("PauliY", [0])], num_wires=1)

    def test_TC056_pauli_z_gate(self):
        assert "z q[0];" in self._qasm([_mock_op("PauliZ", [0])], num_wires=1)

    def test_TC057_cnot_gate(self):
        assert "cx q[0], q[1];" in self._qasm([_mock_op("CNOT", [0, 1])], num_wires=2)

    def test_TC058_cz_gate(self):
        assert "cz q[0], q[1];" in self._qasm([_mock_op("CZ", [0, 1])], num_wires=2)

    def test_TC059_swap_gate(self):
        assert "swap q[0], q[1];" in self._qasm([_mock_op("SWAP", [0, 1])], num_wires=2)

    def test_TC05A_s_gate(self):
        assert "s q[0];" in self._qasm([_mock_op("S", [0])], num_wires=1)

    def test_TC05B_t_gate(self):
        assert "t q[0];" in self._qasm([_mock_op("T", [0])], num_wires=1)

    def test_TC05C_rx_gate(self):
        qasm = self._qasm([_mock_op("RX", [0], [math.pi / 2])], num_wires=1)
        assert "rx(" in qasm and "q[0];" in qasm

    def test_TC05D_ry_gate(self):
        qasm = self._qasm([_mock_op("RY", [0], [math.pi])], num_wires=1)
        assert "ry(" in qasm

    def test_TC05E_rz_gate(self):
        qasm = self._qasm([_mock_op("RZ", [0], [math.pi / 4])], num_wires=1)
        assert "rz(" in qasm

    def test_TC05F_u3_gate(self):
        qasm = self._qasm([_mock_op("U3", [0], [math.pi, math.pi / 2, math.pi / 4])], num_wires=1)
        assert "u3(" in qasm

    def test_TC05G_toffoli_gate(self):
        assert "ccx q[0], q[1], q[2];" in self._qasm([_mock_op("Toffoli", [0, 1, 2])], num_wires=3)

    def test_TC05H_identity_gate(self):
        assert "id q[0];" in self._qasm([_mock_op("Identity", [0])], num_wires=1)

    def test_TC05I_measure_all_wires(self):
        qasm = self._qasm([], num_wires=3)
        assert "measure q[0] -> c[0];" in qasm
        assert "measure q[1] -> c[1];" in qasm
        assert "measure q[2] -> c[2];" in qasm

    def test_TC05J_adjoint_s_gate(self):
        assert "sdg q[0];" in self._qasm([_mock_op("Adjoint(S)", [0])], num_wires=1)

    def test_TC05K_adjoint_t_gate(self):
        assert "tdg q[0];" in self._qasm([_mock_op("Adjoint(T)", [0])], num_wires=1)

    def test_TC05L_unknown_gate_skipped(self):
        qasm = self._qasm([_mock_op("SomeUnknownGate", [0])], num_wires=1)
        assert "OPENQASM 2.0;" in qasm
        assert "SomeUnknownGate" not in qasm

    def test_TC05M_expand_exception_fallback(self):
        tape = MagicMock()
        tape.expand.side_effect = Exception("expand failed")
        tape.operations = []
        tape.num_wires = 1
        tape.wires = [0]
        qasm = self.ext.tape_to_openqasm(tape)
        assert "OPENQASM 2.0;" in qasm

    def test_TC05N_multi_gate_order(self):
        ops = [
            _mock_op("Hadamard", [0]),
            _mock_op("CNOT", [0, 1]),
            _mock_op("RZ", [1], [math.pi / 2]),
        ]
        qasm = self._qasm(ops, num_wires=2)
        assert qasm.index("h q[0];") < qasm.index("cx q[0], q[1];") < qasm.index("rz(")


# ─────────────────────────────────────────────────────────────
# TC-06x: _op_to_pauli_str
# ─────────────────────────────────────────────────────────────

class TestOpToPauliStr:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.ext = UQIExtractor("dummy.py")
        self.pauli_map = {"PauliX": "X", "PauliY": "Y", "PauliZ": "Z", "Identity": "I"}

    def _single_op(self, name, wire):
        op = MagicMock(spec=["name", "wires"])
        op.name = name
        op.wires = [wire]
        return op

    def test_TC061_single_paulix_wire0(self):
        op = self._single_op("PauliX", 0)
        assert self.ext._op_to_pauli_str(op, 2, self.pauli_map) == "IX"

    def test_TC062_single_pauliz_wire1(self):
        op = self._single_op("PauliZ", 1)
        assert self.ext._op_to_pauli_str(op, 2, self.pauli_map) == "ZI"

    def test_TC063_unknown_name_returns_none(self):
        op = self._single_op("SomethingElse", 0)
        assert self.ext._op_to_pauli_str(op, 2, self.pauli_map) is None

    def test_TC064_tensor_product_xy(self):
        sub0 = MagicMock(spec=["name", "wires"]); sub0.name = "PauliX"; sub0.wires = [0]
        sub1 = MagicMock(spec=["name", "wires"]); sub1.name = "PauliY"; sub1.wires = [1]
        op = MagicMock()
        op.operands = [sub0, sub1]
        assert self.ext._op_to_pauli_str(op, 2, self.pauli_map) == "YX"

    def test_TC065_identity_all_wires(self):
        op = self._single_op("Identity", 0)
        assert self.ext._op_to_pauli_str(op, 3, self.pauli_map) == "III"

    def test_TC066_exception_returns_none(self):
        # spec=[] 으로 name/wires/operands 모두 없게 하여 예외 유도
        op = MagicMock(spec=[])
        assert self.ext._op_to_pauli_str(op, 2, self.pauli_map) is None


# ─────────────────────────────────────────────────────────────
# TC-07x: 초기화 상태
# ─────────────────────────────────────────────────────────────

class TestInitialState:

    def test_TC071_circuits_empty(self):
        assert UQIExtractor("dummy.py").circuits == {}

    def test_TC072_perceval_circuits_empty(self):
        assert UQIExtractor("dummy.py").perceval_circuits == {}

    def test_TC073_framework_none(self):
        assert UQIExtractor("dummy.py").framework is None

    def test_TC074_qnode_call_counts_empty(self):
        assert UQIExtractor("dummy.py").qnode_call_counts == {}

    def test_TC075_algorithm_file_stored(self):
        assert UQIExtractor("/some/path/algo.py").algorithm_file == "/some/path/algo.py"

    def test_TC076_tape_expand_depth_constant(self):
        assert UQIExtractor.TAPE_EXPAND_DEPTH == 15