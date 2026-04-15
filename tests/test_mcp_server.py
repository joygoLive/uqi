# test_mcp_server.py

import os
import sys
import json
import tempfile
import pytest
from unittest.mock import MagicMock, patch
import numpy as np

# ─────────────────────────────────────────────────────────────
# mcp_server.py import 시 전역 부작용 차단
# ─────────────────────────────────────────────────────────────

_mock_mcp     = MagicMock()
_mock_cal     = MagicMock()
_mock_rag     = MagicMock()
_mock_fastmcp = MagicMock()
_mock_fastmcp.FastMCP.return_value = _mock_mcp

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# patch.dict("sys.modules", ...) 는 블록 진입 시 sys.modules 스냅샷을 저장하고
# 블록 종료 시 복원한다. mcp_server import 중 로드된 qiskit 모듈들이 블록 종료 후
# sys.modules 에서 제거되면, 이후 테스트에서 qiskit 이 재로드되어 새로운 클래스
# 객체가 만들어진다. 이때 Singleton 게이트 인스턴스는 기존 클래스를 참조하므로
# isinstance(old_singleton, new_Operation) 가 False → issubclass TypeError 발생.
# → 해결: 스냅샷 이전에 qiskit/qiskit_aer 를 미리 임포트해 두면 복원 대상에서 제외됨.
try:
    import qiskit          # noqa: F401
    import qiskit_aer      # noqa: F401
except ImportError:
    pass

with patch.dict("sys.modules", {
    "fastmcp": _mock_fastmcp,
    "dotenv":  MagicMock(),
}), \
patch("uqi_calibration.UQICalibration", return_value=_mock_cal), \
patch("uqi_rag.UQIRAG", return_value=_mock_rag):
    import mcp_server
    from mcp_server import (
        _safe_file_check,
        _safe_json,
        _file_hash,
        _get_calibration,
        _build_calibration_response,
        _resolve_qpu,
        SUPPORTED_QPUS,
        _PHOTONIC_QPUS,
        _FRAMEWORK_QPU_MAP,
        _BLOCKED_PATTERNS,
        _ALLOWED_IMPORTS,
        _QISKIT_STD_GATES,
        _MAX_QUBITS,
        _MAX_GATES,
    )

# mcp_server.py는 import 시점에 builtins.print를 stderr 리다이렉트 버전으로 교체함.
# 다른 테스트 파일의 capsys 캡처가 깨지지 않도록, 원본 print를 복원한다.
import builtins as _builtins
_builtins.print = mcp_server._original_print


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _write_algo(content: str, suffix=".py") -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False)
    f.write(content)
    f.close()
    return f.name


def _clean(path: str):
    try:
        os.unlink(path)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# TC-01x: _safe_file_check — 파일 유효성
# ─────────────────────────────────────────────────────────────

class TestSafeFileCheck:

    def test_TC011_nonexistent_file_returns_error(self):
        result = _safe_file_check("/nonexistent/path/algo.py")
        assert result is not None
        assert "파일 없음" in result

    def test_TC012_non_py_extension_returns_error(self):
        f = _write_algo("print('hello')", suffix=".txt")
        try:
            result = _safe_file_check(f)
            assert result is not None
            assert "Python (.py)" in result
        finally:
            _clean(f)

    def test_TC013_valid_clean_file_returns_none(self):
        f = _write_algo("import qiskit\nprint('hello')\n")
        try:
            _mock_rag.add_security_block = MagicMock()
            result = _safe_file_check(f)
            assert result is None
        finally:
            _clean(f)

    def test_TC014_file_too_large_returns_error(self):
        f = _write_algo("x = 1\n")
        try:
            with patch("pathlib.Path.stat") as mock_stat:
                mock_stat.return_value.st_size = 2 * 1024 * 1024
                result = _safe_file_check(f)
                assert result is not None
                assert "File size exceeded" in result
        finally:
            _clean(f)

    def test_TC015_subprocess_blocked(self):
        f = _write_algo("import subprocess\nsubprocess.run(['ls'])\n")
        try:
            result = _safe_file_check(f)
            assert result is not None
            assert "Security policy violation" in result
        finally:
            _clean(f)

    def test_TC016_eval_blocked(self):
        f = _write_algo("import qiskit\neval('1+1')\n")
        try:
            result = _safe_file_check(f)
            assert result is not None
            assert "Security policy violation" in result
        finally:
            _clean(f)

    def test_TC017_exec_blocked(self):
        f = _write_algo("import qiskit\nexec('x=1')\n")
        try:
            result = _safe_file_check(f)
            assert result is not None
            assert "Security policy violation" in result
        finally:
            _clean(f)

    def test_TC018_socket_blocked(self):
        f = _write_algo("import socket\nsocket.connect()\n")
        try:
            result = _safe_file_check(f)
            assert result is not None
            assert "Security policy violation" in result
        finally:
            _clean(f)

    def test_TC019_pickle_blocked(self):
        f = _write_algo("import pickle\npickle.loads(b'')\n")
        try:
            result = _safe_file_check(f)
            assert result is not None
            assert "Security policy violation" in result
        finally:
            _clean(f)

    def test_TC01A_requests_blocked(self):
        f = _write_algo("import requests\nrequests.get('http://x')\n")
        try:
            result = _safe_file_check(f)
            assert result is not None
            assert "Security policy violation" in result
        finally:
            _clean(f)

    def test_TC01B_urllib_blocked(self):
        f = _write_algo("import urllib\nurllib.request.urlopen('x')\n")
        try:
            result = _safe_file_check(f)
            assert result is not None
            assert "Security policy violation" in result
        finally:
            _clean(f)

    def test_TC01C_shutil_blocked(self):
        f = _write_algo("import shutil\nshutil.rmtree('/')\n")
        try:
            result = _safe_file_check(f)
            assert result is not None
            assert "Security policy violation" in result
        finally:
            _clean(f)

    def test_TC01D_importlib_blocked(self):
        f = _write_algo("import importlib\nimportlib.import_module('os')\n")
        try:
            result = _safe_file_check(f)
            assert result is not None
            assert "Security policy violation" in result
        finally:
            _clean(f)

    def test_TC01E_threading_blocked(self):
        f = _write_algo("import threading\nthreading.Thread()\n")
        try:
            result = _safe_file_check(f)
            assert result is not None
            assert "Security policy violation" in result
        finally:
            _clean(f)

    def test_TC01F_disallowed_import_blocked(self):
        f = _write_algo("import flask\nflask.Flask(__name__)\n")
        try:
            result = _safe_file_check(f)
            assert result is not None
            assert "Unauthorized module import" in result
        finally:
            _clean(f)

    def test_TC01G_allowed_imports_pass(self):
        f = _write_algo(
            "import numpy as np\n"
            "import scipy\n"
            "from qiskit import QuantumCircuit\n"
        )
        try:
            result = _safe_file_check(f)
            assert result is None
        finally:
            _clean(f)

    def test_TC01H_pennylane_allowed(self):
        f = _write_algo("import pennylane as qml\n")
        try:
            result = _safe_file_check(f)
            assert result is None
        finally:
            _clean(f)

    def test_TC01I_file_open_write_blocked(self):
        f = _write_algo('import qiskit\nopen("x.txt", "w")\n')
        try:
            result = _safe_file_check(f)
            assert result is not None
            assert "Security policy violation" in result
        finally:
            _clean(f)

    def test_TC01J_os_getenv_allowed(self):
        # os.getenv는 허용 패턴
        f = _write_algo("import os\ntoken = os.getenv('TOKEN')\n")
        try:
            result = _safe_file_check(f)
            assert result is None
        finally:
            _clean(f)

    def test_TC01K_security_block_logged_on_violation(self):
        f = _write_algo("import subprocess\n")
        _mock_rag.add_security_block = MagicMock()
        try:
            _safe_file_check(f, tool="uqi_analyze")
            _mock_rag.add_security_block.assert_called_once()
        finally:
            _clean(f)

    def test_TC01L_tool_name_passed_to_security_block(self):
        f = _write_algo("import socket\n")
        _mock_rag.add_security_block = MagicMock()
        try:
            _safe_file_check(f, tool="uqi_test_tool")
            call_kwargs = _mock_rag.add_security_block.call_args
            assert call_kwargs is not None
        finally:
            _clean(f)


# ─────────────────────────────────────────────────────────────
# TC-02x: _safe_json
# ─────────────────────────────────────────────────────────────

class TestSafeJson:

    def test_TC021_basic_dict_serialized(self):
        result = _safe_json({"key": "value", "num": 42})
        parsed = json.loads(result)
        assert parsed["key"] == "value"
        assert parsed["num"] == 42

    def test_TC022_numpy_float_serialized(self):
        result = _safe_json({"val": np.float64(3.14)})
        parsed = json.loads(result)
        assert abs(parsed["val"] - 3.14) < 1e-6

    def test_TC023_numpy_int_serialized(self):
        result = _safe_json({"val": np.int64(42)})
        parsed = json.loads(result)
        assert parsed["val"] == 42

    def test_TC024_non_serializable_converted_to_str(self):
        class Custom:
            def __str__(self): return "custom_obj"
        result = _safe_json({"obj": Custom()})
        parsed = json.loads(result)
        assert parsed["obj"] == "custom_obj"

    def test_TC025_returns_string(self):
        result = _safe_json({"a": 1})
        assert isinstance(result, str)

    def test_TC026_nested_dict_serialized(self):
        result = _safe_json({"outer": {"inner": [1, 2, 3]}})
        parsed = json.loads(result)
        assert parsed["outer"]["inner"] == [1, 2, 3]

    def test_TC027_none_value_serialized(self):
        result = _safe_json({"val": None})
        parsed = json.loads(result)
        assert parsed["val"] is None


# ─────────────────────────────────────────────────────────────
# TC-03x: _file_hash
# ─────────────────────────────────────────────────────────────

class TestFileHash:

    def test_TC031_returns_string(self):
        f = _write_algo("import qiskit\n")
        try:
            result = _file_hash(f)
            assert isinstance(result, str)
        finally:
            _clean(f)

    def test_TC032_hash_length_12(self):
        f = _write_algo("import qiskit\n")
        try:
            result = _file_hash(f)
            assert len(result) == 12
        finally:
            _clean(f)

    def test_TC033_same_content_same_hash(self):
        f1 = _write_algo("import qiskit\n")
        f2 = _write_algo("import qiskit\n")
        try:
            assert _file_hash(f1) == _file_hash(f2)
        finally:
            _clean(f1)
            _clean(f2)

    def test_TC034_different_content_different_hash(self):
        f1 = _write_algo("import qiskit\n")
        f2 = _write_algo("import pennylane\n")
        try:
            assert _file_hash(f1) != _file_hash(f2)
        finally:
            _clean(f1)
            _clean(f2)

    def test_TC035_nonexistent_file_returns_nohash(self):
        result = _file_hash("/nonexistent/path.py")
        assert result == "nohash"


# ─────────────────────────────────────────────────────────────
# TC-04x: _get_calibration
# ─────────────────────────────────────────────────────────────

class TestGetCalibration:

    def test_TC041_returns_dict(self):
        _mock_cal.get_transpile_params.return_value = {}
        result = _get_calibration("ibm_fez")
        assert isinstance(result, dict)

    def test_TC042_nonstandard_gates_filtered(self):
        _mock_cal.get_transpile_params.return_value = {
            "basis_gates": ["cx", "rz", "custom_gate_xyz", "h"]
        }
        result = _get_calibration("ibm_fez")
        assert "custom_gate_xyz" not in result["basis_gates"]
        assert "cx" in result["basis_gates"]

    def test_TC043_standard_gates_preserved(self):
        _mock_cal.get_transpile_params.return_value = {
            "basis_gates": ["cx", "rz", "h", "x", "measure"]
        }
        result = _get_calibration("ibm_fez")
        for gate in ["cx", "rz", "h", "x"]:
            assert gate in result["basis_gates"]

    def test_TC044_no_basis_gates_no_filter(self):
        _mock_cal.get_transpile_params.return_value = {
            "num_qubits": 5
        }
        result = _get_calibration("ibm_fez")
        assert "basis_gates" not in result or result.get("basis_gates") is None

    def test_TC045_empty_calibration_returns_empty(self):
        _mock_cal.get_transpile_params.return_value = None
        result = _get_calibration("unknown_qpu")
        assert result == {}


# ─────────────────────────────────────────────────────────────
# TC-046~048: _build_calibration_response (detail 파라미터)
# ─────────────────────────────────────────────────────────────

_PER_QUBIT_KEYS = ("qubit_t1_ms", "qubit_t2_ms", "qubit_ro_error",
                   "qubit_1q_error", "edge_2q_error",
                   "coupling_map", "qubit_positions")
_SAMPLE_CAL = {
    "num_qubits": 5,
    "avg_2q_error": 0.01,
    "avg_t1_ms": 100.0,
    "last_updated": "2026-01-01T00:00:00",
    "qubit_t1_ms": [100.0, 200.0, 150.0, 120.0, 180.0],
    "qubit_t2_ms": [50.0,  80.0,  60.0,  70.0,  90.0],
    "qubit_ro_error": [0.01] * 5,
    "qubit_1q_error": [0.001] * 5,
    "edge_2q_error":  [[0, 1, 0.01], [1, 2, 0.012]],
    "coupling_map":   [[0, 1], [1, 2]],
    "qubit_positions": {"0": [0.0, 0.0], "1": [1.0, 0.0]},
}

class TestBuildCalibrationResponse:

    def test_TC046_detail_false_excludes_per_qubit(self):
        result = json.loads(_build_calibration_response(_SAMPLE_CAL, "ibm_test", detail=False))
        for key in _PER_QUBIT_KEYS:
            assert key not in result, f"{key} should not be in summary response"

    def test_TC047_detail_false_includes_avg_fields(self):
        result = json.loads(_build_calibration_response(_SAMPLE_CAL, "ibm_test", detail=False))
        assert result["qpu_name"] == "ibm_test"
        assert result["num_qubits"] == 5
        assert result["avg_2q_error"] == pytest.approx(0.01)

    def test_TC048_detail_true_includes_per_qubit(self):
        result = json.loads(_build_calibration_response(_SAMPLE_CAL, "ibm_test", detail=True))
        for key in _PER_QUBIT_KEYS:
            assert key in result, f"{key} should be in detail response"
        assert result["qubit_t1_ms"] == [100.0, 200.0, 150.0, 120.0, 180.0]
        assert result["edge_2q_error"] == [[0, 1, 0.01], [1, 2, 0.012]]


# ─────────────────────────────────────────────────────────────
# TC-05x: 상수 검증
# ─────────────────────────────────────────────────────────────

class TestConstants:

    def test_TC051_supported_qpus_not_empty(self):
        assert len(SUPPORTED_QPUS) > 0

    def test_TC052_ibm_fez_in_supported(self):
        assert "ibm_fez" in SUPPORTED_QPUS

    def test_TC053_iqm_garnet_in_supported(self):
        assert "iqm_garnet" in SUPPORTED_QPUS

    def test_TC054_blocked_patterns_not_empty(self):
        assert len(_BLOCKED_PATTERNS) > 0

    def test_TC055_each_blocked_pattern_is_tuple_of_two(self):
        for item in _BLOCKED_PATTERNS:
            assert isinstance(item, tuple)
            assert len(item) == 2

    def test_TC056_ibm_torino_not_in_supported(self):
        """ibm_torino 은 지원 QPU 목록에서 제외됨"""
        assert "ibm_torino" not in SUPPORTED_QPUS

    def test_TC057_rigetti_ankaa3_in_supported(self):
        """rigetti_ankaa3 는 지원 QPU 목록에 포함됨"""
        assert "rigetti_ankaa3" in SUPPORTED_QPUS

    def test_TC056_subprocess_in_blocked_patterns(self):
        patterns = [p for p, _ in _BLOCKED_PATTERNS]
        assert any("subprocess" in p for p in patterns)

    def test_TC057_eval_in_blocked_patterns(self):
        patterns = [p for p, _ in _BLOCKED_PATTERNS]
        assert any("eval" in p for p in patterns)

    def test_TC058_allowed_imports_not_empty(self):
        assert len(_ALLOWED_IMPORTS) > 0

    def test_TC059_qiskit_in_allowed_imports(self):
        assert "qiskit" in _ALLOWED_IMPORTS

    def test_TC05A_numpy_in_allowed_imports(self):
        assert "numpy" in _ALLOWED_IMPORTS

    def test_TC05B_max_qubits_positive(self):
        assert _MAX_QUBITS > 0

    def test_TC05C_max_gates_positive(self):
        assert _MAX_GATES > 0

    def test_TC05D_qiskit_std_gates_not_empty(self):
        assert len(_QISKIT_STD_GATES) > 0

    def test_TC05E_cx_in_std_gates(self):
        assert "cx" in _QISKIT_STD_GATES

    def test_TC05F_measure_in_std_gates(self):
        assert "measure" in _QISKIT_STD_GATES

    def test_TC05G_16_blocked_patterns(self):
        # 소스 주석 기준 16개 패턴 검증
        assert len(_BLOCKED_PATTERNS) >= 16

    def test_TC05H_sim_ascella_in_supported(self):
        assert "sim:ascella" in SUPPORTED_QPUS

    def test_TC05I_sim_belenos_in_supported(self):
        assert "sim:belenos" in SUPPORTED_QPUS

    def test_TC05J_qpu_ascella_in_supported(self):
        assert "qpu:ascella" in SUPPORTED_QPUS

    def test_TC05K_qpu_belenos_in_supported(self):
        assert "qpu:belenos" in SUPPORTED_QPUS


# ─────────────────────────────────────────────────────────────
# TC-06x: Photonic QPU 매핑 검증
# ─────────────────────────────────────────────────────────────

class TestPhotonicQPUMapping:

    def test_TC061_photonic_qpus_contains_sim_ascella(self):
        assert "sim:ascella" in _PHOTONIC_QPUS

    def test_TC062_photonic_qpus_contains_sim_belenos(self):
        assert "sim:belenos" in _PHOTONIC_QPUS

    def test_TC063_photonic_qpus_contains_qpu_ascella(self):
        assert "qpu:ascella" in _PHOTONIC_QPUS

    def test_TC064_photonic_qpus_contains_qpu_belenos(self):
        assert "qpu:belenos" in _PHOTONIC_QPUS

    def test_TC065_ibm_fez_not_in_photonic(self):
        assert "ibm_fez" not in _PHOTONIC_QPUS

    def test_TC066_framework_map_has_perceval(self):
        assert "Perceval" in _FRAMEWORK_QPU_MAP

    def test_TC067_framework_map_has_qiskit(self):
        assert "Qiskit" in _FRAMEWORK_QPU_MAP

    def test_TC068_perceval_default_is_sim_ascella(self):
        assert _FRAMEWORK_QPU_MAP["Perceval"]["default"] == "sim:ascella"

    def test_TC069_qiskit_default_is_ibm_fez(self):
        assert _FRAMEWORK_QPU_MAP["Qiskit"]["default"] == "ibm_fez"

    def test_TC06A_perceval_qpus_all_photonic(self):
        for q in _FRAMEWORK_QPU_MAP["Perceval"]["qpus"]:
            assert q in _PHOTONIC_QPUS

    def test_TC06B_qiskit_qpus_no_photonic(self):
        for q in _FRAMEWORK_QPU_MAP["Qiskit"]["qpus"]:
            assert q not in _PHOTONIC_QPUS


# ─────────────────────────────────────────────────────────────
# TC-07x: _resolve_qpu 검증
# ─────────────────────────────────────────────────────────────

class TestResolveQPU:

    def test_TC071_perceval_file_auto_resolves_to_sim_ascella(self):
        path = _write_algo("import perceval as pcvl\ncircuit = pcvl.Circuit(2)\n")
        try:
            result = _resolve_qpu(path, "auto")
            assert result == "sim:ascella"
        finally:
            _clean(path)

    def test_TC072_perceval_file_ibm_fez_resolves_to_sim_ascella(self):
        """Perceval 파일에 gate-based QPU 선택 시 자동 보정"""
        path = _write_algo("import perceval as pcvl\ncircuit = pcvl.Circuit(2)\n")
        try:
            result = _resolve_qpu(path, "ibm_fez")
            assert result == "sim:ascella"
        finally:
            _clean(path)

    def test_TC073_qiskit_file_auto_resolves_to_ibm_fez(self):
        path = _write_algo("from qiskit import QuantumCircuit\nqc = QuantumCircuit(2)\n")
        try:
            result = _resolve_qpu(path, "auto")
            assert result == "ibm_fez"
        finally:
            _clean(path)

    def test_TC074_qiskit_file_sim_ascella_resolves_to_ibm_fez(self):
        """Qiskit 파일에 photonic QPU 선택 시 자동 보정"""
        path = _write_algo("from qiskit import QuantumCircuit\nqc = QuantumCircuit(2)\n")
        try:
            result = _resolve_qpu(path, "sim:ascella")
            assert result == "ibm_fez"
        finally:
            _clean(path)

    def test_TC075_qiskit_file_ibm_fez_unchanged(self):
        """이미 올바른 QPU면 변경 없음"""
        path = _write_algo("from qiskit import QuantumCircuit\nqc = QuantumCircuit(2)\n")
        try:
            result = _resolve_qpu(path, "ibm_fez")
            assert result == "ibm_fez"
        finally:
            _clean(path)

    def test_TC076_perceval_file_sim_ascella_unchanged(self):
        """이미 올바른 QPU면 변경 없음"""
        path = _write_algo("import perceval as pcvl\ncircuit = pcvl.Circuit(2)\n")
        try:
            result = _resolve_qpu(path, "sim:ascella")
            assert result == "sim:ascella"
        finally:
            _clean(path)

    def test_TC077_nonexistent_file_returns_original(self):
        """파일이 없으면 원래 값 반환 (에러 안 남)"""
        result = _resolve_qpu("/tmp/nonexistent_abc123.py", "ibm_fez")
        assert result == "ibm_fez"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v",
                 "--cov=mcp_server", "--cov-report=term-missing"])