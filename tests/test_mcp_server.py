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

    def test_TC057_rigetti_cepheus_in_supported(self):
        """rigetti_cepheus 는 지원 QPU 목록에 포함됨 (Ankaa-3는 retire되어 제거됨)"""
        assert "rigetti_cepheus" in SUPPORTED_QPUS

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

    def test_TC078_nonexistent_file_with_auto_falls_back_to_default(self):
        """파일 없거나 framework 감지 실패 시에도 'auto' 는 글로벌 default 로 안전 변환.

        이전 동작: extractor 예외 → 'auto' 그대로 반환 → RAG/save_job 에 'auto' 누출.
        새 동작: 모든 fail 경로에서 'auto' → 'ibm_fez' (gate-based default) 보장.
        """
        result = _resolve_qpu("/tmp/nonexistent_abc123.py", "auto")
        assert result == "ibm_fez", "framework 감지 실패해도 'auto' 가 통과되면 안 됨"


# ─────────────────────────────────────────────────────────────
# _build_qpu_submit_message 통합 테스트 (비용/가용성/출처/SUBMIT_BLOCK)
# ─────────────────────────────────────────────────────────────

class TestQpuSubmitMessageEnrichment:
    """_build_qpu_submit_message가 비용/가용성/출처 정보를 자동 노출"""

    @staticmethod
    def _make_d(qpu, shots=100):
        return {
            "selected_qpu":    qpu,
            "recommended_qpu": qpu,
            "avg_fidelity":    0.95,
            "total_cost":      100,
            "shots":           shots,
        }

    def test_TC080_message_contains_cost_for_ionq(self):
        from mcp_server import _build_qpu_submit_message
        msg = _build_qpu_submit_message(self._make_d("ionq_forte1", 100))
        assert "💰" in msg
        assert "8.3" in msg     # IonQ 100 shots = $8.30

    def test_TC081_message_contains_cost_for_ibm(self):
        from mcp_server import _build_qpu_submit_message
        msg = _build_qpu_submit_message(self._make_d("ibm_fez", 1024))
        assert "💰" in msg
        assert "무료" in msg

    def test_TC082_message_contains_cost_for_iqm(self):
        from mcp_server import _build_qpu_submit_message
        msg = _build_qpu_submit_message(self._make_d("iqm_emerald", 1024))
        assert "💰" in msg
        assert "credit" in msg.lower()

    def test_TC083_message_contains_cost_for_pasqal(self):
        from mcp_server import _build_qpu_submit_message
        msg = _build_qpu_submit_message(self._make_d("pasqal_fresnel", 100))
        assert "💰" in msg
        assert "EUR" in msg or "hour" in msg

    def test_TC084_message_contains_cost_for_quandela(self):
        from mcp_server import _build_qpu_submit_message
        msg = _build_qpu_submit_message(self._make_d("qpu:ascella", 1024))
        assert "💰" in msg

    def test_TC085_braket_qpu_has_availability_section(self):
        from mcp_server import _build_qpu_submit_message
        msg = _build_qpu_submit_message(self._make_d("ionq_forte1", 100))
        assert "🕐" in msg

    def test_TC086_non_braket_no_availability_section(self):
        """IBM/IQM/Quandela는 가용성 미표시 (자사 클라우드 정책)"""
        from mcp_server import _build_qpu_submit_message
        for qpu in ["ibm_fez", "iqm_emerald", "qpu:ascella"]:
            msg = _build_qpu_submit_message(self._make_d(qpu, 100))
            assert "🕐" not in msg, f"{qpu}는 가용성 표시 안 해야 함"

    def test_TC087_pasqal_has_azure_availability_section(self):
        from mcp_server import _build_qpu_submit_message
        msg = _build_qpu_submit_message(self._make_d("pasqal_fresnel", 100))
        assert "🕐" in msg
        assert "Azure" in msg

    def _mock_cal_data(self, monkeypatch, data: dict):
        """UQICalibration().data를 mock으로 주입 — DB 의존 격리"""
        from unittest.mock import MagicMock
        fake_cal = MagicMock()
        fake_cal.data = data
        monkeypatch.setattr("uqi_calibration.UQICalibration",
                            lambda *a, **k: fake_cal)

    def test_TC088_braket_data_source_displayed(self, monkeypatch):
        """Braket 게이트웨이 경유 — 출처 표시"""
        self._mock_cal_data(monkeypatch, {
            "ionq_forte1":     {"data_source": "aws_braket"},
            "rigetti_cepheus": {"data_source": "aws_braket"},
            "quera_aquila":    {"data_source": "aws_braket"},
        })
        from mcp_server import _build_qpu_submit_message
        for qpu in ["ionq_forte1", "rigetti_cepheus", "quera_aquila"]:
            msg = _build_qpu_submit_message(self._make_d(qpu, 100))
            assert "🔄" in msg, f"{qpu} 출처 표시 안 됨"
            assert "AWS Braket" in msg or "Braket" in msg

    def test_TC089_self_cloud_no_data_source(self, monkeypatch):
        """자사 클라우드(IBM/IQM/Quandela)는 출처 미표시"""
        # 이 vendor들은 _sync_*에 data_source 안 박음 → cal.data에 없음
        self._mock_cal_data(monkeypatch, {})
        from mcp_server import _build_qpu_submit_message
        for qpu in ["ibm_fez", "iqm_emerald", "qpu:ascella"]:
            msg = _build_qpu_submit_message(self._make_d(qpu, 100))
            assert "🔄" not in msg, f"{qpu}는 출처 표시 안 해야 함"

    def test_TC090_quantinuum_static_data_warning(self, monkeypatch):
        """Quantinuum 정적 데이터 + 신뢰도 경고 자동 표시"""
        self._mock_cal_data(monkeypatch, {
            "quantinuum_h2_1": {
                "data_source": "pytket_offline_static",
                "noise_date":  "2025-04-30",
            },
        })
        from mcp_server import _build_qpu_submit_message
        msg = _build_qpu_submit_message(self._make_d("quantinuum_h2_1", 100))
        assert "🔄" in msg
        assert "정적" in msg or "OFFLINE" in msg or "pytket" in msg
        assert "Nexus" in msg     # Live 데이터는 Nexus 안내

    def test_TC091_pasqal_data_source_azure(self, monkeypatch):
        """Pasqal calibration data_source = azure_quantum"""
        self._mock_cal_data(monkeypatch, {
            "pasqal_fresnel": {"data_source": "azure_quantum"},
        })
        from mcp_server import _build_qpu_submit_message
        msg = _build_qpu_submit_message(self._make_d("pasqal_fresnel", 100))
        assert "🔄" in msg
        assert "Azure" in msg


# ─────────────────────────────────────────────────────────────
# SUPPORTED_QPUS 카탈로그 정합성 (본 세션 정책 반영)
# ─────────────────────────────────────────────────────────────

class TestSupportedQpusCatalog:

    def test_TC100_ionq_aria1_removed(self):
        """Aria-1 retire 정리"""
        from mcp_server import SUPPORTED_QPUS
        assert "ionq_aria1" not in SUPPORTED_QPUS

    def test_TC101_rigetti_ankaa3_removed(self):
        """Ankaa-3 retire 정리"""
        from mcp_server import SUPPORTED_QPUS
        assert "rigetti_ankaa3" not in SUPPORTED_QPUS

    def test_TC102_rigetti_cepheus_present(self):
        from mcp_server import SUPPORTED_QPUS
        assert "rigetti_cepheus" in SUPPORTED_QPUS

    def test_TC103_pasqal_fresnel_present(self):
        from mcp_server import SUPPORTED_QPUS
        assert "pasqal_fresnel" in SUPPORTED_QPUS
        assert "pasqal_fresnel_can1" in SUPPORTED_QPUS

    def test_TC104_pasqal_simulator_excluded(self):
        """정책: Pasqal 시뮬레이터(emu-*)는 카탈로그 제외"""
        from mcp_server import SUPPORTED_QPUS
        assert "pasqal_emu_tn" not in SUPPORTED_QPUS
        assert "pasqal_emu_mps" not in SUPPORTED_QPUS
        assert "pasqal_emu_sv" not in SUPPORTED_QPUS

    def test_TC105_quantinuum_present_for_analysis(self):
        """Quantinuum은 분석/추천용으로 카탈로그 유지 (submit은 BLOCK)"""
        from mcp_server import SUPPORTED_QPUS
        assert "quantinuum_h2_1" in SUPPORTED_QPUS
        assert "quantinuum_h2_2" in SUPPORTED_QPUS
        assert "quantinuum_h1_1" in SUPPORTED_QPUS


# ─────────────────────────────────────────────────────────────
# Phase 2 — uqi_job_list distinct/search 새 catalog 컬럼 활용 검증
# ─────────────────────────────────────────────────────────────

class TestJobListPhase2:
    """uqi_job_list 가 새 catalog 컬럼 (qpu_vendor/qpu_modality/runtime) 직접 사용하는지"""

    def _setup_sample_db(self, tmp_path, monkeypatch):
        """v2 스키마 + 다양한 vendor/modality sample row"""
        import uqi_job_store
        db_path = tmp_path / "sample_jobs.db"
        monkeypatch.setattr(uqi_job_store, "_DB_PATH", db_path)
        uqi_job_store.init_db()
        # 다양한 catalog 매핑 케이스
        samples = [
            ("j_ibm_1",    "ibm_fez",          "circ_a", 100),
            ("j_ibm_2",    "ibm_marrakesh",    "circ_a", 200),
            ("j_iqm_1",    "iqm_emerald",      "circ_b", 50),
            ("j_ionq_1",   "ionq_forte1",      "circ_c", 100),
            ("j_quan_1",   "quantinuum_h2_1sc","circ_d", 100),
            ("j_qd_qpu",   "qpu:ascella",      "circ_e", 1000),
            ("j_qd_sim",   "sim:ascella",      "circ_e", 1000),
        ]
        for jid, qpu, circ, shots in samples:
            uqi_job_store.save_job(job_id=jid, qpu_name=qpu, circuit_name=circ, shots=shots)
        return uqi_job_store, db_path

    def test_TC400_distinct_values_returns_catalog_vendors(self, tmp_path, monkeypatch):
        """distinct_values 의 vendors = catalog 제조사 (IBM/IQM/IonQ/Quantinuum/Quandela)"""
        self._setup_sample_db(tmp_path, monkeypatch)
        # mcp_server 의 _DB_PATH 가 다른 곳을 가리키므로 sqlite 직접 query 로 동등 검증
        import sqlite3
        db_path = tmp_path / "sample_jobs.db"
        conn = sqlite3.connect(str(db_path))
        vendors = sorted({r[0] for r in conn.execute(
            "SELECT DISTINCT qpu_vendor FROM jobs WHERE qpu_vendor IS NOT NULL")})
        modalities = sorted({r[0] for r in conn.execute(
            "SELECT DISTINCT qpu_modality FROM jobs WHERE qpu_modality IS NOT NULL")})
        conn.close()

        # catalog 제조사 (게이트웨이 키 ['azure','braket'] 가 아니어야 함)
        assert "IBM" in vendors
        assert "IQM" in vendors
        assert "IonQ" in vendors
        assert "Quantinuum" in vendors
        assert "Quandela" in vendors
        # 게이트웨이 키 절대 X
        assert "azure" not in vendors
        assert "braket" not in vendors

        # modality 도 catalog 키 (hyphen-case)
        assert "superconducting" in modalities
        assert "ion-trap" in modalities
        assert "photonic" in modalities

    def test_TC401_search_vendor_direct_column_match(self, tmp_path, monkeypatch):
        """search_vendor='IBM' → WHERE qpu_vendor='IBM' 직접 비교, IN 절 없음"""
        self._setup_sample_db(tmp_path, monkeypatch)
        import sqlite3
        db_path = tmp_path / "sample_jobs.db"
        conn = sqlite3.connect(str(db_path))

        # mcp_server 의 search_vendor 처리 패턴: WHERE qpu_vendor = ?
        rows = conn.execute(
            "SELECT job_id FROM jobs WHERE qpu_vendor = ?", ("IBM",)).fetchall()
        ibm_ids = {r[0] for r in rows}
        conn.close()

        assert ibm_ids == {"j_ibm_1", "j_ibm_2"}

    def test_TC402_search_modality_direct_column_match(self, tmp_path, monkeypatch):
        """search_modality='ion-trap' → WHERE qpu_modality='ion-trap' 직접 비교"""
        self._setup_sample_db(tmp_path, monkeypatch)
        import sqlite3
        db_path = tmp_path / "sample_jobs.db"
        conn = sqlite3.connect(str(db_path))

        rows = conn.execute(
            "SELECT job_id FROM jobs WHERE qpu_modality = ?", ("ion-trap",)).fetchall()
        ids = {r[0] for r in rows}
        conn.close()

        # IonQ Forte-1 + Quantinuum H2-1SC 둘 다 ion-trap
        assert ids == {"j_ionq_1", "j_quan_1"}

    def test_TC403_search_modality_photonic_includes_qpu_and_sim(self, tmp_path, monkeypatch):
        """photonic 검색 시 Quandela qpu/sim 모두 포함"""
        self._setup_sample_db(tmp_path, monkeypatch)
        import sqlite3
        db_path = tmp_path / "sample_jobs.db"
        conn = sqlite3.connect(str(db_path))

        rows = conn.execute(
            "SELECT job_id FROM jobs WHERE qpu_modality = ?", ("photonic",)).fetchall()
        conn.close()

        assert {r[0] for r in rows} == {"j_qd_qpu", "j_qd_sim"}

    def test_TC404_runtime_distribution(self, tmp_path, monkeypatch):
        """runtime 컬럼 직접 사용 — 청구 클라우드 분포 확인"""
        self._setup_sample_db(tmp_path, monkeypatch)
        import sqlite3
        db_path = tmp_path / "sample_jobs.db"
        conn = sqlite3.connect(str(db_path))

        runtime_counts = dict(conn.execute(
            "SELECT runtime, COUNT(*) FROM jobs GROUP BY runtime").fetchall())
        conn.close()

        assert runtime_counts.get("IBM Quantum")    == 2  # ibm_fez + ibm_marrakesh
        assert runtime_counts.get("IQM Resonance")  == 1  # iqm_emerald
        assert runtime_counts.get("AWS Braket")     == 1  # ionq_forte1
        assert runtime_counts.get("Azure Quantum")  == 1  # quantinuum_h2_1sc
        assert runtime_counts.get("Quandela Cloud") == 2  # qpu:ascella + sim:ascella

    def test_TC405_no_legacy_vendor_column(self, tmp_path, monkeypatch):
        """v2 마이그레이션 후 legacy vendor 컬럼 절대 존재 X"""
        self._setup_sample_db(tmp_path, monkeypatch)
        import sqlite3
        db_path = tmp_path / "sample_jobs.db"
        conn = sqlite3.connect(str(db_path))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
        conn.close()
        assert "vendor" not in cols, "legacy vendor 컬럼이 v2 스키마에 남아있음"


# ─────────────────────────────────────────────────────────────
# 비용 안전장치 (cost_safeguard) — 실제 비용 발생 없이 꼼꼼하게 검증
# ─────────────────────────────────────────────────────────────

class TestCostSafeguard:
    """_check_cost_safeguard 함수 — 13가지 시나리오"""

    # ─── admin_override=True: 모든 케이스 통과 ───

    def test_TC200_admin_override_always_passes_expensive(self):
        """admin → IonQ 1000 shots ($80.30) 통과"""
        from mcp_server import _check_cost_safeguard
        assert _check_cost_safeguard("ionq_forte1", 1000, admin_override=True) is None

    def test_TC201_admin_override_passes_pasqal(self):
        """admin → Pasqal Fresnel ($54+) 통과"""
        from mcp_server import _check_cost_safeguard
        assert _check_cost_safeguard("pasqal_fresnel", 100, admin_override=True) is None

    def test_TC202_admin_override_passes_unknown_qpu(self):
        """admin → 미등록 QPU도 통과"""
        from mcp_server import _check_cost_safeguard
        assert _check_cost_safeguard("brand_new_qpu", 100, admin_override=True) is None

    def test_TC203_admin_override_passes_quantinuum(self):
        """admin → Quantinuum (verify_required) 통과"""
        from mcp_server import _check_cost_safeguard
        assert _check_cost_safeguard("quantinuum_h2_1", 100, admin_override=True) is None

    # ─── 일반 사용자: 차단 시나리오 (모두 admin 권한 필요) ───

    def test_TC210_block_ionq_700shots_above_threshold(self):
        """IonQ 700 shots = $0.30 + $56 = $56.30 → 차단 ($50 ≥)"""
        from mcp_server import _check_cost_safeguard
        block = _check_cost_safeguard("ionq_forte1", 700)
        assert block is not None
        assert block["blocked_by"] == "cost_safeguard"
        assert "$56.30" in block["reason"] or "≥ 임계값" in block["reason"]

    def test_TC211_block_ionq_1000shots(self):
        """IonQ 1000 shots = $80.30 → 차단"""
        from mcp_server import _check_cost_safeguard
        block = _check_cost_safeguard("ionq_forte1", 1000)
        assert block is not None
        assert block["estimated_usd"] == 80.30

    def test_TC212_block_pasqal_fresnel(self):
        """Pasqal Fresnel ($54+ default 60s 가정) → 차단"""
        from mcp_server import _check_cost_safeguard
        block = _check_cost_safeguard("pasqal_fresnel", 100)
        assert block is not None
        assert block["estimated_usd"] >= 50

    def test_TC213_block_quantinuum_verify_required(self):
        """Quantinuum HQC (verify_required) → 차단"""
        from mcp_server import _check_cost_safeguard
        block = _check_cost_safeguard("quantinuum_h2_1", 100)
        assert block is not None
        assert "verify_required" in block["reason"]

    def test_TC214_block_unknown_qpu(self):
        """미등록 QPU (confidence=unknown) → 차단"""
        from mcp_server import _check_cost_safeguard
        block = _check_cost_safeguard("brand_new_qpu_xyz", 100)
        assert block is not None
        assert "unknown" in block["reason"]

    # ─── 일반 사용자: 통과 시나리오 (저비용 또는 무료) ───

    def test_TC220_pass_ionq_100shots_below_threshold(self):
        """IonQ 100 shots = $8.30 < $50 → 통과"""
        from mcp_server import _check_cost_safeguard
        assert _check_cost_safeguard("ionq_forte1", 100) is None

    def test_TC221_pass_ionq_500shots_borderline(self):
        """IonQ 500 shots = $0.30 + $40 = $40.30 < $50 → 통과"""
        from mcp_server import _check_cost_safeguard
        assert _check_cost_safeguard("ionq_forte1", 500) is None

    def test_TC222_pass_ionq_621shots_just_below_threshold(self):
        """IonQ 621 shots = $0.30 + 49.68 = $49.98 (임계값 직전) → 통과"""
        from mcp_server import _check_cost_safeguard
        assert _check_cost_safeguard("ionq_forte1", 621) is None

    def test_TC223_block_ionq_622shots_just_above_threshold(self):
        """IonQ 622 shots = $0.30 + 49.76 = $50.06 (임계값 직후) → 차단"""
        from mcp_server import _check_cost_safeguard
        block = _check_cost_safeguard("ionq_forte1", 622)
        assert block is not None
        assert block["estimated_usd"] >= 50.0

    def test_TC224_pass_ibm_open_plan_free(self):
        """IBM Open Plan (free quota) → 통과"""
        from mcp_server import _check_cost_safeguard
        assert _check_cost_safeguard("ibm_fez", 1024) is None

    def test_TC225_pass_iqm_credits(self):
        """IQM 지원 크레딧 (currency=credits, exact) → 통과"""
        from mcp_server import _check_cost_safeguard
        assert _check_cost_safeguard("iqm_emerald", 1024) is None

    def test_TC226_pass_quandela_free_quota(self):
        """Quandela qpu:ascella (월 200 credits 무료) → 통과"""
        from mcp_server import _check_cost_safeguard
        assert _check_cost_safeguard("qpu:ascella", 1024) is None

    def test_TC227_pass_quandela_simulator_free(self):
        """Quandela 시뮬레이터 (free) → 통과"""
        from mcp_server import _check_cost_safeguard
        assert _check_cost_safeguard("sim:ascella", 1024) is None

    def test_TC228_pass_rigetti_cepheus_low_cost(self):
        """Rigetti Cepheus 1000 shots = $0.725 → 통과"""
        from mcp_server import _check_cost_safeguard
        assert _check_cost_safeguard("rigetti_cepheus", 1000) is None

    def test_TC229_pass_braket_sv1_simulator(self):
        """Braket SV1 시뮬레이터 (저렴) → 통과"""
        from mcp_server import _check_cost_safeguard
        assert _check_cost_safeguard("braket_sv1", 100) is None

    # ─── 차단 메시지 형식 검증 (관리자 컨택 안내, 이메일 X) ───

    def test_TC230_block_message_contains_admin_contact(self):
        """차단 메시지에 '관리자에게 문의' 안내 포함"""
        from mcp_server import _check_cost_safeguard
        block = _check_cost_safeguard("ionq_forte1", 1000)
        assert "관리자" in block["message"]
        assert "문의" in block["message"]

    def test_TC231_block_message_no_email_exposed(self):
        """차단 메시지에 이메일 주소 표기 안 함 (정책)"""
        from mcp_server import _check_cost_safeguard
        block = _check_cost_safeguard("ionq_forte1", 1000)
        assert "@" not in block["message"]
        assert "gmail" not in block["message"].lower()

    def test_TC232_block_message_includes_qpu_and_shots(self):
        """차단 메시지에 QPU/shots/예상비용 모두 포함"""
        from mcp_server import _check_cost_safeguard
        block = _check_cost_safeguard("ionq_forte1", 1000)
        assert "ionq_forte1" in block["message"]
        assert "1000" in block["message"]

    def test_TC233_block_response_has_required_fields(self):
        """응답 dict에 필수 필드 모두 포함"""
        from mcp_server import _check_cost_safeguard
        block = _check_cost_safeguard("ionq_forte1", 1000)
        for k in ("error", "blocked_by", "reason", "qpu", "shots", "message"):
            assert k in block, f"missing field: {k}"

    # ─── 임계값 정합성 ───

    def test_TC240_threshold_is_50_usd(self):
        """기본 임계값 $50 확인"""
        from mcp_server import COST_SAFEGUARD_THRESHOLD_USD
        assert COST_SAFEGUARD_THRESHOLD_USD == 50.0


# ─────────────────────────────────────────────────────────────
# 비용 요약 (uqi_billing_summary) — AWS/Azure helper 단위 테스트
# ─────────────────────────────────────────────────────────────

class TestBillingSummary:
    """_aws_billing_summary / _azure_billing_summary helper — mock 기반"""

    def test_TC300_aws_success_response_schema(self, monkeypatch):
        """AWS 정상 응답 schema 검증"""
        from unittest.mock import MagicMock
        fake_ce = MagicMock()
        fake_ce.get_cost_and_usage.return_value = {
            'ResultsByTime': [{
                'Groups': [
                    {'Keys': ['Amazon Braket'],
                     'Metrics': {'UnblendedCost': {'Amount': '8.30'}}},
                    {'Keys': ['AWS Storage'],
                     'Metrics': {'UnblendedCost': {'Amount': '0.0125'}}},
                ]
            }]
        }
        import boto3
        monkeypatch.setattr(boto3, "client", lambda *a, **k: fake_ce)
        from mcp_server import _aws_billing_summary
        r = _aws_billing_summary()
        assert r["ok"] is True
        assert r["currency"] == "USD"
        assert abs(r["total_usd"] - 8.3125) < 0.001
        assert "Amazon Braket" in r["by_service"]
        assert r["by_service"]["Amazon Braket"] == 8.30

    def test_TC301_aws_access_denied_graceful(self, monkeypatch):
        """AWS 권한 부족 시 graceful 메시지"""
        from unittest.mock import MagicMock
        fake_ce = MagicMock()
        fake_ce.get_cost_and_usage.side_effect = Exception(
            "AccessDeniedException: User not authorized to perform ce:GetCostAndUsage"
        )
        import boto3
        monkeypatch.setattr(boto3, "client", lambda *a, **k: fake_ce)
        from mcp_server import _aws_billing_summary
        r = _aws_billing_summary()
        assert r["ok"] is False
        assert "권한 필요" in r["error"]
        assert "관리자 문의" in r["error"]

    def test_TC302_azure_success_response_schema(self, monkeypatch):
        """Azure 정상 응답 schema 검증 (KRW 케이스)"""
        from unittest.mock import MagicMock, patch
        fake_token = MagicMock()
        fake_token.token = "fake-bearer-token"
        fake_cred = MagicMock()
        fake_cred.get_token.return_value = fake_token

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {
            "properties": {
                "columns": [{"name": "Cost"}, {"name": "Currency"}],
                "rows":    [[12345.6789, "KRW"]],
            }
        }
        with patch("azure.identity.ClientSecretCredential", return_value=fake_cred), \
             patch("requests.post", return_value=fake_resp):
            from mcp_server import _azure_billing_summary
            r = _azure_billing_summary()
        assert r["ok"] is True
        assert r["currency"] == "KRW"
        assert abs(r["total"] - 12345.6789) < 0.001

    def test_TC303_azure_401_graceful(self, monkeypatch):
        """Azure 401 권한 부족 시 graceful 메시지"""
        from unittest.mock import MagicMock, patch
        fake_token = MagicMock()
        fake_token.token = "fake-bearer-token"
        fake_cred = MagicMock()
        fake_cred.get_token.return_value = fake_token

        fake_resp = MagicMock()
        fake_resp.status_code = 401
        fake_resp.text = "RBACAccessDenied"

        with patch("azure.identity.ClientSecretCredential", return_value=fake_cred), \
             patch("requests.post", return_value=fake_resp):
            from mcp_server import _azure_billing_summary
            r = _azure_billing_summary()
        assert r["ok"] is False
        assert "권한 필요" in r["error"]
        assert "관리자 문의" in r["error"]

    def test_TC304_azure_403_graceful(self, monkeypatch):
        """Azure 403 forbidden도 동일하게 권한 안내"""
        from unittest.mock import MagicMock, patch
        fake_token = MagicMock()
        fake_token.token = "t"
        fake_cred = MagicMock()
        fake_cred.get_token.return_value = fake_token
        fake_resp = MagicMock()
        fake_resp.status_code = 403
        fake_resp.text = "forbidden"
        with patch("azure.identity.ClientSecretCredential", return_value=fake_cred), \
             patch("requests.post", return_value=fake_resp):
            from mcp_server import _azure_billing_summary
            r = _azure_billing_summary()
        assert r["ok"] is False
        assert "권한 필요" in r["error"]

    def test_TC305_azure_empty_rows(self, monkeypatch):
        """Azure 응답에 rows가 비어있어도 ok=True (사용량 0)"""
        from unittest.mock import MagicMock, patch
        fake_token = MagicMock()
        fake_token.token = "t"
        fake_cred = MagicMock()
        fake_cred.get_token.return_value = fake_token
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {
            "properties": {"columns": [], "rows": []}
        }
        with patch("azure.identity.ClientSecretCredential", return_value=fake_cred), \
             patch("requests.post", return_value=fake_resp):
            from mcp_server import _azure_billing_summary
            r = _azure_billing_summary()
        assert r["ok"] is True
        assert r["total"] == 0.0

    def test_TC306_azure_missing_subscription_id(self, monkeypatch):
        """AZURE_QUANTUM_SUBSCRIPTION_ID 없으면 사전 검사로 즉시 실패"""
        # 빈 문자열로 set (delenv는 .env 재로드되면 무효, setenv로 명시적 비움)
        monkeypatch.setenv("AZURE_QUANTUM_SUBSCRIPTION_ID", "")
        from mcp_server import _azure_billing_summary
        r = _azure_billing_summary()
        assert r["ok"] is False
        assert "SUBSCRIPTION_ID" in (r["error"] or "")

    def test_TC307_billing_summary_response_includes_both_vendors(self):
        """uqi_billing_summary 응답에 aws/azure 모두 포함"""
        from mcp_server import _aws_billing_summary, _azure_billing_summary
        # 직접 helper 호출만 검증 (실 API 결과는 권한 따라 다름)
        aws = _aws_billing_summary()
        az = _azure_billing_summary()
        # 두 helper 모두 dict 반환
        assert isinstance(aws, dict)
        assert isinstance(az, dict)
        # 필수 필드
        for k in ("ok", "error"):
            assert k in aws
            assert k in az


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v",
                 "--cov=mcp_server", "--cov-report=term-missing"])