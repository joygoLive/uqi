# test_uqi_messages.py
# uqi_messages.py 상수 및 함수 테스트

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from uqi_messages import (
    # Status codes
    STATUS_SUBMITTED, STATUS_RUNNING, STATUS_DONE, STATUS_ERROR,
    STATUS_CANCELLED, STATUS_FAILED,
    STATUS_AWAITING_CONFIRMATION, STATUS_COMPLETED,
    STATUS_CACHE_EXPIRED, STATUS_SUBMITTING,
    # IBM
    IBM_BACKEND_INACCESSIBLE, IBM_NO_CIRCUIT,
    IBM_ESTIMATOR_NO_CIRCUIT, IBM_NO_QASM,
    # IQM
    IQM_NO_QASM, IQM_CIRCUIT_CONVERT_FAIL, IQM_NO_RESULT, IQM_NO_TOKEN,
    iqm_limit_exceeded,
    # CUDAQ
    CUDAQ_NO_SAMPLE_RESULT, cudaq_qubit_exceeded,
    # Perceval
    PERCEVAL_NO_CIRCUIT, PERCEVAL_NO_TOKEN, PERCEVAL_EMPTY_RESULT,
    perceval_modes_exceeded, perceval_photons_exceeded, perceval_run_fail,
    # MCP
    MCP_CACHE_EXPIRED, MCP_SEMANTIC_NO_QUERY,
    MCP_FILE_EXT_NOT_ALLOWED, MCP_FILE_ONLY_PY,
    MCP_FILE_TOO_LARGE, MCP_SUBMISSION_NOT_FOUND,
    mcp_qpu_offline, mcp_qpu_offline_cached,
    mcp_unavailable_qpu, mcp_qubit_exceeded_submit, mcp_qubit_exceeded_transpile,
    mcp_unsupported_query_type, mcp_no_calibration,
    mcp_file_not_found, mcp_dir_not_found,
)


# ─── Status constants ────────────────────────────────────────

class TestStatusConstants:
    def test_db_status_codes_are_strings(self):
        for val in [STATUS_SUBMITTED, STATUS_RUNNING, STATUS_DONE,
                    STATUS_ERROR, STATUS_CANCELLED, STATUS_FAILED]:
            assert isinstance(val, str) and val

    def test_api_status_codes_are_strings(self):
        for val in [STATUS_AWAITING_CONFIRMATION, STATUS_COMPLETED,
                    STATUS_CACHE_EXPIRED, STATUS_SUBMITTING]:
            assert isinstance(val, str) and val

    def test_status_codes_are_unique(self):
        db_codes = [STATUS_SUBMITTED, STATUS_RUNNING, STATUS_DONE,
                    STATUS_ERROR, STATUS_CANCELLED, STATUS_FAILED]
        api_codes = [STATUS_AWAITING_CONFIRMATION, STATUS_COMPLETED,
                     STATUS_CACHE_EXPIRED, STATUS_SUBMITTING]
        all_codes = db_codes + api_codes
        assert len(all_codes) == len(set(all_codes))


# ─── IBM constants ───────────────────────────────────────────

class TestIBMMessages:
    def test_constants_non_empty(self):
        for val in [IBM_BACKEND_INACCESSIBLE, IBM_NO_CIRCUIT,
                    IBM_ESTIMATOR_NO_CIRCUIT, IBM_NO_QASM]:
            assert isinstance(val, str) and val


# ─── IQM constants and functions ─────────────────────────────

class TestIQMMessages:
    def test_constants_non_empty(self):
        for val in [IQM_NO_QASM, IQM_CIRCUIT_CONVERT_FAIL,
                    IQM_NO_RESULT, IQM_NO_TOKEN]:
            assert isinstance(val, str) and val

    def test_iqm_limit_exceeded_contains_count(self):
        msg = iqm_limit_exceeded(15000)
        assert "15000" in msg
        assert isinstance(msg, str) and msg

    def test_iqm_limit_exceeded_zero(self):
        msg = iqm_limit_exceeded(0)
        assert "0" in msg


# ─── CUDAQ constants and functions ───────────────────────────

class TestCUDAQMessages:
    def test_constant_non_empty(self):
        assert isinstance(CUDAQ_NO_SAMPLE_RESULT, str) and CUDAQ_NO_SAMPLE_RESULT

    def test_cudaq_qubit_exceeded_contains_count(self):
        msg = cudaq_qubit_exceeded(12)
        assert "12" in msg
        assert isinstance(msg, str) and msg

    def test_cudaq_qubit_exceeded_boundary(self):
        msg = cudaq_qubit_exceeded(11)
        assert "11" in msg


# ─── Perceval constants and functions ────────────────────────

class TestPercevalMessages:
    def test_constants_non_empty(self):
        for val in [PERCEVAL_NO_CIRCUIT, PERCEVAL_NO_TOKEN, PERCEVAL_EMPTY_RESULT]:
            assert isinstance(val, str) and val

    def test_perceval_modes_exceeded_contains_values(self):
        msg = perceval_modes_exceeded(15, 12)
        assert "15" in msg
        assert "12" in msg
        assert isinstance(msg, str) and msg

    def test_perceval_photons_exceeded_contains_values(self):
        msg = perceval_photons_exceeded(8, 6)
        assert "8" in msg
        assert "6" in msg

    def test_perceval_run_fail_contains_reason(self):
        msg = perceval_run_fail("timeout")
        assert "timeout" in msg
        assert isinstance(msg, str) and msg

    def test_perceval_modes_exceeded_equal_values(self):
        # edge: circuit_m == max_modes should still produce a message
        msg = perceval_modes_exceeded(12, 12)
        assert "12" in msg


# ─── MCP Server constants and functions ──────────────────────

class TestMCPMessages:
    def test_constants_non_empty(self):
        for val in [MCP_CACHE_EXPIRED, MCP_SEMANTIC_NO_QUERY,
                    MCP_FILE_EXT_NOT_ALLOWED, MCP_FILE_ONLY_PY,
                    MCP_FILE_TOO_LARGE, MCP_SUBMISSION_NOT_FOUND]:
            assert isinstance(val, str) and val

    def test_mcp_qpu_offline_contains_name(self):
        msg = mcp_qpu_offline("ibm_kyoto")
        assert "ibm_kyoto" in msg

    def test_mcp_qpu_offline_cached_contains_name(self):
        msg = mcp_qpu_offline_cached("iqm_garnet")
        assert "iqm_garnet" in msg

    def test_mcp_unavailable_qpu_contains_name(self):
        msg = mcp_unavailable_qpu("fake_qpu")
        assert "fake_qpu" in msg

    def test_mcp_qubit_exceeded_submit_contains_all_parts(self):
        msg = mcp_qubit_exceeded_submit("circuit_a", 5, "ibm_kyoto", 3)
        assert "circuit_a" in msg
        assert "5" in msg
        assert "ibm_kyoto" in msg
        assert "3" in msg

    def test_mcp_qubit_exceeded_transpile_contains_all_parts(self):
        msg = mcp_qubit_exceeded_transpile("circuit_b", 7, "iqm_garnet", 4)
        assert "7" in msg
        assert "iqm_garnet" in msg
        assert "4" in msg

    def test_mcp_unsupported_query_type_contains_type(self):
        msg = mcp_unsupported_query_type("invalid_type")
        assert "invalid_type" in msg

    def test_mcp_no_calibration_contains_qpu(self):
        msg = mcp_no_calibration("ibm_sherbrooke")
        assert "ibm_sherbrooke" in msg

    def test_mcp_file_not_found_contains_path(self):
        msg = mcp_file_not_found("/some/path/file.py")
        assert "/some/path/file.py" in msg

    def test_mcp_dir_not_found_contains_path(self):
        msg = mcp_dir_not_found("/some/dir")
        assert "/some/dir" in msg


# ─── Import checks for executor modules ──────────────────────

class TestExecutorImports:
    """Verify all executor modules can import from uqi_messages without error."""

    def test_uqi_messages_importable(self):
        import uqi_messages  # noqa: F401

    def test_no_circular_imports(self):
        # Re-import to ensure no side effects or circular dependency
        import importlib
        import uqi_messages
        importlib.reload(uqi_messages)
