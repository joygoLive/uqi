# test_uqi_executor_iqm.py

import os
import sys
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from uqi_executor_iqm import UQIExecutorIQM, IQM_NATIVE_GATES, QISKIT_TO_IQM


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _make_executor(circuits=None, tapes=None, sessions=None,
                   qasm=None, shots=1024):
    extractor = MagicMock()
    extractor.tapes    = tapes    or {}
    extractor.sessions = sessions or {}
    extractor.circuits = circuits or {}

    converter = MagicMock()
    converter.extractor    = extractor
    converter.qasm_results = qasm or {}

    return UQIExecutorIQM(converter, shots=shots)


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
# TC-01x: 초기화 및 상수
# ─────────────────────────────────────────────────────────────

class TestInitialState:

    def test_TC011_converter_stored(self):
        converter = MagicMock()
        executor = UQIExecutorIQM(converter)
        assert executor.converter is converter

    def test_TC012_default_shots(self):
        executor = UQIExecutorIQM(MagicMock())
        assert executor.shots == 1024

    def test_TC013_custom_shots(self):
        executor = UQIExecutorIQM(MagicMock(), shots=2048)
        assert executor.shots == 2048

    def test_TC014_results_empty(self):
        executor = UQIExecutorIQM(MagicMock())
        assert executor.results == {}

    def test_TC015_iqm_native_gates_contains_required(self):
        for gate in ["prx", "cz", "measure", "reset", "barrier"]:
            assert gate in IQM_NATIVE_GATES

    def test_TC016_qiskit_to_iqm_cx_is_none(self):
        assert QISKIT_TO_IQM["cx"] is None

    def test_TC017_qiskit_to_iqm_cz_mapped(self):
        assert QISKIT_TO_IQM["cz"] == "cz"

    def test_TC018_garnet_topology_not_empty(self):
        assert len(UQIExecutorIQM.GARNET_CZ_LOCI_QB) > 0

    def test_TC019_garnet_topology_pairs_are_tuples(self):
        for pair in UQIExecutorIQM.GARNET_CZ_LOCI_QB:
            assert isinstance(pair, tuple)
            assert len(pair) == 2


# ─────────────────────────────────────────────────────────────
# TC-02x: run_all
# ─────────────────────────────────────────────────────────────

class TestRunAll:

    def test_TC021_no_circuits_returns_empty(self):
        executor = _make_executor()
        assert executor.run_all() == {}

    def test_TC022_circuit_without_qasm_filtered_out(self):
        executor = _make_executor(
            circuits={"circ_a": MagicMock()},
            qasm={}  # QASM 없음 → 필터링
        )
        result = executor.run_all()
        assert result == {}

    def test_TC023_circuit_with_qasm_executed(self):
        executor = _make_executor(
            circuits={"circ_a": MagicMock()},
            qasm={"circ_a": SIMPLE_QASM}
        )
        with patch.object(executor, "_run_single", return_value={"ok": True}) as m:
            executor.run_all()
            m.assert_called_once()
            assert m.call_args[0][0] == "circ_a"

    def test_TC024_circuit_names_from_tapes(self):
        executor = _make_executor(
            tapes={"tape_a": MagicMock()},
            qasm={"tape_a": SIMPLE_QASM}
        )
        with patch.object(executor, "_run_single", return_value={"ok": True}) as m:
            executor.run_all()
            assert m.call_args[0][0] == "tape_a"

    def test_TC025_circuit_names_from_sessions_fallback(self):
        executor = _make_executor(
            sessions={"sess_a": MagicMock()},
            qasm={"sess_a": SIMPLE_QASM}
        )
        with patch.object(executor, "_run_single", return_value={"ok": True}) as m:
            executor.run_all()
            assert m.call_args[0][0] == "sess_a"

    def test_TC026_results_aggregated(self):
        executor = _make_executor(
            circuits={"a": MagicMock(), "b": MagicMock()},
            qasm={"a": SIMPLE_QASM, "b": SIMPLE_QASM}
        )
        with patch.object(executor, "_run_single", return_value={"ok": True}):
            result = executor.run_all()
            assert set(result.keys()) == {"a", "b"}

    def test_TC027_token_stored(self):
        executor = _make_executor(
            circuits={"a": MagicMock()},
            qasm={"a": SIMPLE_QASM}
        )
        with patch.object(executor, "_run_single", return_value={"ok": True}):
            executor.run_all(token="test-token")
            assert executor._token == "test-token"

    def test_TC028_tapes_priority_over_circuits(self):
        executor = _make_executor(
            tapes={"tape_a": MagicMock()},
            circuits={"circ_a": MagicMock()},
            qasm={"tape_a": SIMPLE_QASM}
        )
        called_names = []
        def fake_run(name, *a, **kw):
            called_names.append(name)
            return {"ok": True}
        with patch.object(executor, "_run_single", side_effect=fake_run):
            executor.run_all()
            assert called_names == ["tape_a"]


# ─────────────────────────────────────────────────────────────
# TC-03x: _run_single
# ─────────────────────────────────────────────────────────────

class TestRunSingle:

    def test_TC031_no_qasm_returns_error(self):
        executor = _make_executor()
        result = executor._run_single("circ_a", None, True, "https://dummy.url")
        assert result["ok"] is False
        assert result["error"] == "QASM 없음"

    def test_TC032_result_dict_keys(self):
        executor = _make_executor()
        result = executor._run_single("circ_a", None, True, "https://dummy.url")
        assert {"ok", "counts", "probs", "backend", "error"} <= set(result.keys())

    def test_TC033_simulator_backend_name(self):
        executor = _make_executor()
        mock_iqm_circuit = MagicMock()
        mock_iqm_circuit.instructions = [MagicMock(name="measure")]

        with patch.object(executor, "_get_cz_loci", return_value=set()), \
             patch.object(executor, "_to_iqm_circuit", return_value=mock_iqm_circuit), \
             patch.object(executor, "_run_simulator", return_value={"00": 512, "11": 512}):
            result = executor._run_single("circ_a", SIMPLE_QASM, True, "https://dummy.url")
            assert result["backend"] == "iqm-client-simulator"

    def test_TC034_qpu_backend_url(self):
        executor = _make_executor()
        url = "https://resonance.meetiqm.com/computers/garnet"
        mock_iqm_circuit = MagicMock()
        mock_iqm_circuit.instructions = [MagicMock(name="measure")]

        with patch.object(executor, "_get_cz_loci", return_value=set()), \
             patch.object(executor, "_to_iqm_circuit", return_value=mock_iqm_circuit), \
             patch.object(executor, "_run_real", return_value={"00": 512, "11": 512}):
            result = executor._run_single("circ_a", SIMPLE_QASM, False, url)
            assert result["backend"] == url

    def test_TC035_instruction_limit_exceeded(self):
        executor = _make_executor()
        mock_iqm_circuit = MagicMock()
        mock_iqm_circuit.instructions = [MagicMock()] * 10001

        with patch.object(executor, "_get_cz_loci", return_value=set()), \
             patch.object(executor, "_to_iqm_circuit", return_value=mock_iqm_circuit):
            result = executor._run_single("circ_a", SIMPLE_QASM, False, "https://dummy.url")
            assert result["ok"] is False
            assert "10000" in result["error"]

    def test_TC036_instruction_limit_not_applied_for_simulator(self):
        executor = _make_executor()
        mock_iqm_circuit = MagicMock()
        mock_iqm_circuit.instructions = [MagicMock()] * 10001

        with patch.object(executor, "_get_cz_loci", return_value=set()), \
             patch.object(executor, "_to_iqm_circuit", return_value=mock_iqm_circuit), \
             patch.object(executor, "_run_simulator", return_value={"0" * 20: 1024}):
            result = executor._run_single("circ_a", SIMPLE_QASM, True, "https://dummy.url")
            # 시뮬레이터에선 instruction 제한 미적용
            assert result["ok"] is True

    def test_TC037_to_iqm_circuit_none_returns_error(self):
        executor = _make_executor()
        with patch.object(executor, "_get_cz_loci", return_value=set()), \
             patch.object(executor, "_to_iqm_circuit", return_value=None):
            result = executor._run_single("circ_a", SIMPLE_QASM, True, "https://dummy.url")
            assert result["ok"] is False
            assert result["error"] == "IQM Circuit 변환 실패"

    def test_TC038_run_simulator_none_returns_error(self):
        executor = _make_executor()
        mock_iqm_circuit = MagicMock()
        mock_iqm_circuit.instructions = []

        with patch.object(executor, "_get_cz_loci", return_value=set()), \
             patch.object(executor, "_to_iqm_circuit", return_value=mock_iqm_circuit), \
             patch.object(executor, "_run_simulator", return_value=None):
            result = executor._run_single("circ_a", SIMPLE_QASM, True, "https://dummy.url")
            assert result["ok"] is False
            assert result["error"] == "실행 결과 없음"

    def test_TC039_probs_sum_to_one(self):
        executor = _make_executor()
        mock_iqm_circuit = MagicMock()
        mock_iqm_circuit.instructions = []
        counts = {"00": 300, "01": 200, "10": 300, "11": 200}

        with patch.object(executor, "_get_cz_loci", return_value=set()), \
             patch.object(executor, "_to_iqm_circuit", return_value=mock_iqm_circuit), \
             patch.object(executor, "_run_simulator", return_value=counts):
            result = executor._run_single("circ_a", SIMPLE_QASM, True, "https://dummy.url")
            if result["ok"] and result["probs"]:
                assert abs(sum(result["probs"].values()) - 1.0) < 1e-9


# ─────────────────────────────────────────────────────────────
# TC-04x: _get_cz_loci
# ─────────────────────────────────────────────────────────────

class TestGetCzLoci:

    def test_TC041_returns_empty_set_on_exception(self):
        executor = _make_executor()
        with patch("iqm.iqm_client.IQMClient", side_effect=ImportError("no iqm")):
            result = executor._get_cz_loci("https://dummy.url")
            assert result == set()

    def test_TC042_returns_empty_set_when_no_token(self):
        executor = _make_executor()
        executor._token = None
        with patch.dict("os.environ", {}, clear=True):
            with patch("iqm.iqm_client.IQMClient", side_effect=Exception("auth fail")):
                result = executor._get_cz_loci("https://dummy.url/garnet")
                assert result == set()

    def test_TC043_caches_loci_on_success(self):
        executor = _make_executor()
        executor._token = "tok"

        mock_locus = ("QB1", "QB2")
        mock_impl = MagicMock()
        mock_impl.loci = [mock_locus]
        mock_arch = MagicMock()
        mock_arch.gates = {"cz": MagicMock(implementations={"impl": mock_impl})}
        mock_arch.qubits = ["QB1", "QB2"]

        mock_client = MagicMock()
        mock_client.get_dynamic_quantum_architecture.return_value = mock_arch

        with patch("iqm.iqm_client.IQMClient", return_value=mock_client):
            result = executor._get_cz_loci("https://resonance.meetiqm.com/computers/garnet")
            assert ("QB1", "QB2") in result
            assert hasattr(executor, "_cz_loci_cache")

    def test_TC044_qubit_index_map_set_on_success(self):
        executor = _make_executor()
        executor._token = "tok"

        mock_impl = MagicMock()
        mock_impl.loci = [("QB1", "QB2")]
        mock_arch = MagicMock()
        mock_arch.gates = {"cz": MagicMock(implementations={"impl": mock_impl})}
        mock_arch.qubits = ["QB1", "QB2", "QB3"]

        mock_client = MagicMock()
        mock_client.get_dynamic_quantum_architecture.return_value = mock_arch

        with patch("iqm.iqm_client.IQMClient", return_value=mock_client):
            executor._get_cz_loci("https://resonance.meetiqm.com/computers/garnet")
            assert executor._qubit_index_map["QB1"] == 0
            assert executor._qubit_index_map["QB2"] == 1
            assert executor._qubit_index_map["QB3"] == 2


# ─────────────────────────────────────────────────────────────
# TC-05x: _run_real
# ─────────────────────────────────────────────────────────────

class TestRunReal:

    def test_TC051_no_token_returns_none(self):
        executor = _make_executor()
        executor._token = None
        with patch.dict("os.environ", {}, clear=True):
            result = executor._run_real(MagicMock(), "https://dummy.url")
            assert result is None

    def test_TC052_exception_returns_none(self):
        executor = _make_executor()
        executor._token = "tok"
        with patch("iqm.iqm_client.IQMClient", side_effect=Exception("conn fail")):
            result = executor._run_real(MagicMock(), "https://dummy.url/garnet")
            assert result is None

    def test_TC053_device_name_parsed_from_url(self):
        executor = _make_executor()
        executor._token = "tok"

        captured = {}
        def mock_client_init(url, quantum_computer, token):
            captured["quantum_computer"] = quantum_computer
            raise Exception("stop here")

        with patch("iqm.iqm_client.IQMClient", side_effect=mock_client_init):
            executor._run_real(MagicMock(), "https://resonance.meetiqm.com/computers/garnet")
            assert captured.get("quantum_computer") == "garnet"

    def test_TC054_job_result_none_returns_none(self):
        executor = _make_executor()
        executor._token = "tok"

        mock_job = MagicMock()
        mock_job.result.return_value = None

        mock_client = MagicMock()
        mock_client.submit_circuits.return_value = mock_job

        with patch("iqm.iqm_client.IQMClient", return_value=mock_client):
            result = executor._run_real(MagicMock(), "https://dummy.url/garnet")
            assert result is None

    def test_TC055_counts_aggregated_from_shots(self):
        executor = _make_executor()
        executor._token = "tok"

        mock_job = MagicMock()
        # 측정 결과: m0 키에 [0,0], [1,1], [1,1] 3샷
        mock_job.result.return_value = [{"m0": [[0, 0], [1, 1], [1, 1]]}]

        mock_client = MagicMock()
        mock_client.submit_circuits.return_value = mock_job

        with patch("iqm.iqm_client.IQMClient", return_value=mock_client):
            counts = executor._run_real(MagicMock(), "https://dummy.url/garnet")
            assert counts is not None
            assert counts.get("00", 0) == 1
            assert counts.get("11", 0) == 2


# ─────────────────────────────────────────────────────────────
# TC-06x: print_summary
# ─────────────────────────────────────────────────────────────

class TestPrintSummary:

    def test_TC061_empty_results_no_exception(self):
        executor = _make_executor()
        executor.print_summary()

    def test_TC062_ok_result_shows_checkmark(self, capsys):
        executor = _make_executor()
        executor.results = {
            "circ_a": {
                "ok": True,
                "backend": "iqm-client-simulator",
                "probs": {"00": 0.5, "11": 0.5},
                "error": None,
            }
        }
        executor.print_summary()
        out = capsys.readouterr().out
        assert "circ_a" in out
        assert "✓" in out

    def test_TC063_failed_result_shows_cross(self, capsys):
        executor = _make_executor()
        executor.results = {
            "circ_b": {
                "ok": False,
                "backend": None,
                "probs": None,
                "error": "QASM 없음",
            }
        }
        executor.print_summary()
        out = capsys.readouterr().out
        assert "circ_b" in out
        assert "✗" in out

    def test_TC064_top3_probs_shown(self, capsys):
        executor = _make_executor()
        executor.results = {
            "circ_c": {
                "ok": True,
                "backend": "iqm-client-simulator",
                "probs": {"00": 0.6, "11": 0.3, "01": 0.1},
                "error": None,
            }
        }
        executor.print_summary()
        out = capsys.readouterr().out
        assert "top-3" in out


if __name__ == "__main__":
    pytest.main([__file__, "-v",
                 "--cov=uqi_executor_iqm", "--cov-report=term-missing"])