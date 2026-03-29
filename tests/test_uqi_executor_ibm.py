# test_uqi_executor_ibm.py

import os
import sys
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from uqi_executor_ibm import UQIExecutorIBM


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _make_executor(circuits=None, tapes=None, sessions=None,
                   observables=None, qir=None, qasm=None, shots=1024):
    """converter/extractor mock 조합 생성"""
    extractor = MagicMock()
    extractor.tapes     = tapes     or {}
    extractor.sessions  = sessions  or {}
    extractor.circuits  = circuits  or {}
    extractor.observables = observables or {}

    converter = MagicMock()
    converter.extractor   = extractor
    converter.qir_results = qir  or {}
    converter.qasm_results = qasm or {}

    return UQIExecutorIBM(converter, shots=shots)


def _mock_sampler_job(counts: dict, creg_name: str = "c"):
    """SamplerV2 job mock"""
    creg = MagicMock()
    creg.get_counts.return_value = counts
    pub_result = MagicMock()
    setattr(pub_result.data, creg_name, creg)
    job = MagicMock()
    job.result.return_value = [pub_result]
    return job


def _mock_estimator_job(expval: float):
    """EstimatorV2 job mock"""
    pub_result = MagicMock()
    pub_result.data.evs = expval
    job = MagicMock()
    job.result.return_value = [pub_result]
    return job


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

QASM_NO_MEASURE = """\
OPENQASM 2.0;
include "qelib1.inc";
qreg q[1];
h q[0];
"""


# ─────────────────────────────────────────────────────────────
# TC-01x: 초기화
# ─────────────────────────────────────────────────────────────

class TestInitialState:

    def test_TC011_converter_stored(self):
        converter = MagicMock()
        converter.extractor.tapes = {}
        converter.extractor.sessions = {}
        converter.extractor.circuits = {}
        executor = UQIExecutorIBM(converter)
        assert executor.converter is converter

    def test_TC012_default_shots(self):
        converter = MagicMock()
        executor = UQIExecutorIBM(converter)
        assert executor.shots == 1024

    def test_TC013_custom_shots(self):
        converter = MagicMock()
        executor = UQIExecutorIBM(converter, shots=512)
        assert executor.shots == 512

    def test_TC014_results_empty(self):
        converter = MagicMock()
        executor = UQIExecutorIBM(converter)
        assert executor.results == {}


# ─────────────────────────────────────────────────────────────
# TC-02x: run_all — 회로 목록 소스 및 분기
# ─────────────────────────────────────────────────────────────

class TestRunAll:

    def test_TC021_no_circuits_returns_empty(self):
        executor = _make_executor()
        result = executor.run_all()
        assert result == {}

    def test_TC022_circuit_names_from_tapes(self):
        executor = _make_executor(tapes={"tape_a": MagicMock()})
        with patch.object(executor, "_run_single", return_value={"ok": True}) as m:
            executor.run_all()
            m.assert_called_once()
            assert m.call_args[0][0] == "tape_a"

    def test_TC023_circuit_names_from_sessions_fallback(self):
        executor = _make_executor(sessions={"sess_a": MagicMock()})
        with patch.object(executor, "_run_single", return_value={"ok": True}) as m:
            executor.run_all()
            m.assert_called_once()
            assert m.call_args[0][0] == "sess_a"

    def test_TC024_circuit_names_from_circuits_fallback(self):
        executor = _make_executor(circuits={"circ_a": MagicMock()})
        with patch.object(executor, "_run_single", return_value={"ok": True}) as m:
            executor.run_all()
            m.assert_called_once()
            assert m.call_args[0][0] == "circ_a"

    def test_TC025_routes_to_estimator_when_observable(self):
        executor = _make_executor(
            circuits={"circ_a": MagicMock()},
            observables={"circ_a": MagicMock()}
        )
        with patch.object(executor, "_run_single_estimator", return_value={"ok": True}) as m:
            executor.run_all()
            m.assert_called_once()

    def test_TC026_routes_to_sampler_when_no_observable(self):
        executor = _make_executor(circuits={"circ_a": MagicMock()})
        with patch.object(executor, "_run_single", return_value={"ok": True}) as m:
            executor.run_all()
            m.assert_called_once()

    def test_TC027_results_aggregated(self):
        executor = _make_executor(circuits={"a": MagicMock(), "b": MagicMock()})
        with patch.object(executor, "_run_single", return_value={"ok": True}):
            result = executor.run_all()
            assert set(result.keys()) == {"a", "b"}

    def test_TC028_token_stored(self):
        executor = _make_executor(circuits={"a": MagicMock()})
        with patch.object(executor, "_run_single", return_value={"ok": True}):
            executor.run_all(token="test-token")
            assert executor._token == "test-token"

    def test_TC029_tapes_priority_over_circuits(self):
        """tapes가 있으면 circuits 무시"""
        executor = _make_executor(
            tapes={"tape_a": MagicMock()},
            circuits={"circ_a": MagicMock()}
        )
        called_names = []
        def fake_run_single(name, *a, **kw):
            called_names.append(name)
            return {"ok": True}
        with patch.object(executor, "_run_single", side_effect=fake_run_single):
            executor.run_all()
            assert called_names == ["tape_a"]


# ─────────────────────────────────────────────────────────────
# TC-03x: _run_single (Sampler)
# ─────────────────────────────────────────────────────────────

class TestRunSingle:

    def test_TC031_no_circuit_no_qasm_returns_error(self):
        executor = _make_executor()
        result = executor._run_single("test", None, None, True, "ibm_fez")
        assert result["ok"] is False
        assert result["error"] is not None

    def test_TC032_qiskit_direct_path(self):
        from qiskit import QuantumCircuit
        qc = QuantumCircuit.from_qasm_str(SIMPLE_QASM)
        executor = _make_executor(circuits={"circ_a": qc})

        counts = {"00": 512, "11": 512}
        job = _mock_sampler_job(counts)

        with patch("qiskit_aer.AerSimulator"), \
             patch("qiskit_aer.primitives.SamplerV2") as MockSampler:
            mock_sampler_inst = MagicMock()
            mock_sampler_inst.run.return_value = job
            MockSampler.return_value = mock_sampler_inst

            result = executor._run_single("circ_a", None, None, True, "ibm_fez")
            assert result["via"] == "Qiskit-direct"

    def test_TC033_qasm_path_sets_via(self):
        executor = _make_executor()
        counts = {"00": 700, "11": 324}
        job = _mock_sampler_job(counts)

        with patch("qiskit_aer.AerSimulator"), \
             patch("qiskit_aer.primitives.SamplerV2") as MockSampler:
            mock_inst = MagicMock()
            mock_inst.run.return_value = job
            MockSampler.return_value = mock_inst
            result = executor._run_single("circ_a", None, SIMPLE_QASM, True, "ibm_fez")
            assert result["via"] == "QASM"

    def test_TC034_simulator_backend_name(self):
        executor = _make_executor()
        counts = {"0": 1024}
        job = _mock_sampler_job(counts)

        with patch("qiskit_aer.AerSimulator"), \
             patch("qiskit_aer.primitives.SamplerV2") as MockSampler:
            mock_inst = MagicMock()
            mock_inst.run.return_value = job
            MockSampler.return_value = mock_inst
            result = executor._run_single("circ_a", None, SIMPLE_QASM, True, "ibm_fez")
            assert result["backend"] == "AerSimulator"

    def test_TC035_ok_false_on_exception(self):
        executor = _make_executor()
        with patch("qiskit_aer.primitives.SamplerV2", side_effect=Exception("boom")):
            result = executor._run_single("circ_a", None, SIMPLE_QASM, True, "ibm_fez")
            assert result["ok"] is False
            assert "boom" in result["error"]

    def test_TC036_result_dict_keys(self):
        executor = _make_executor()
        result = executor._run_single("circ_a", None, None, True, "ibm_fez")
        assert {"ok", "counts", "probs", "backend", "via", "error"} <= set(result.keys())

    def test_TC037_probs_sum_to_one(self):
        executor = _make_executor()
        counts = {"00": 300, "01": 200, "10": 300, "11": 200}
        job = _mock_sampler_job(counts)

        with patch("qiskit_aer.AerSimulator"), \
             patch("qiskit_aer.primitives.SamplerV2") as MockSampler:
            mock_inst = MagicMock()
            mock_inst.run.return_value = job
            MockSampler.return_value = mock_inst
            result = executor._run_single("circ_a", None, SIMPLE_QASM, True, "ibm_fez")
            if result["ok"] and result["probs"]:
                assert abs(sum(result["probs"].values()) - 1.0) < 1e-9

    def test_TC038_gphase_filtered_from_qasm(self):
        """gphase 라인이 있어도 파싱 오류 없이 처리"""
        qasm_with_gphase = SIMPLE_QASM + "gphase(0.5);\n"
        executor = _make_executor()
        counts = {"00": 512, "11": 512}
        job = _mock_sampler_job(counts)

        with patch("qiskit_aer.AerSimulator"), \
             patch("qiskit_aer.primitives.SamplerV2") as MockSampler:
            mock_inst = MagicMock()
            mock_inst.run.return_value = job
            MockSampler.return_value = mock_inst
            result = executor._run_single("circ_a", None, qasm_with_gphase, True, "ibm_fez")
            assert result["via"] == "QASM"


# ─────────────────────────────────────────────────────────────
# TC-04x: _run_single_estimator
# ─────────────────────────────────────────────────────────────

class TestRunSingleEstimator:

    def test_TC041_no_qasm_returns_error(self):
        executor = _make_executor()
        result = executor._run_single_estimator("circ_a", None, MagicMock(), True, "ibm_fez")
        assert result["ok"] is False
        assert result["error"] == "QASM 없음"

    def test_TC042_via_is_estimator(self):
        executor = _make_executor()
        result = executor._run_single_estimator("circ_a", None, MagicMock(), True, "ibm_fez")
        assert result["via"] == "Estimator"

    def test_TC043_simulator_backend_name(self):
        executor = _make_executor()
        job = _mock_estimator_job(0.5)

        with patch("qiskit_aer.primitives.EstimatorV2") as MockEst:
            mock_inst = MagicMock()
            mock_inst.run.return_value = job
            MockEst.return_value = mock_inst
            result = executor._run_single_estimator(
                "circ_a", SIMPLE_QASM, MagicMock(), True, "ibm_fez"
            )
            assert result["backend"] == "AerSimulator"

    def test_TC044_ok_false_on_exception(self):
        executor = _make_executor()
        with patch("qiskit_aer.primitives.EstimatorV2", side_effect=Exception("est_fail")):
            result = executor._run_single_estimator(
                "circ_a", SIMPLE_QASM, MagicMock(), True, "ibm_fez"
            )
            assert result["ok"] is False
            assert "est_fail" in result["error"]

    def test_TC045_result_dict_keys(self):
        executor = _make_executor()
        result = executor._run_single_estimator("circ_a", None, MagicMock(), True, "ibm_fez")
        assert {"ok", "counts", "probs", "expectation_value", "backend", "via", "error"} <= set(result.keys())


# ─────────────────────────────────────────────────────────────
# TC-05x: print_summary
# ─────────────────────────────────────────────────────────────

class TestPrintSummary:

    def test_TC051_empty_results_no_exception(self):
        executor = _make_executor()
        executor.print_summary()  # 예외 없이 실행

    def test_TC052_ok_sampler_result(self, capsys):
        executor = _make_executor()
        executor.results = {
            "circ_a": {
                "ok": True,
                "backend": "AerSimulator",
                "via": "QASM",
                "expectation_value": None,
                "probs": {"00": 0.5, "11": 0.5},
                "error": None,
            }
        }
        executor.print_summary()
        out = capsys.readouterr().out
        assert "circ_a" in out
        assert "✓" in out

    def test_TC053_failed_result(self, capsys):
        executor = _make_executor()
        executor.results = {
            "circ_b": {
                "ok": False,
                "backend": None,
                "via": None,
                "expectation_value": None,
                "probs": None,
                "error": "something went wrong",
            }
        }
        executor.print_summary()
        out = capsys.readouterr().out
        assert "circ_b" in out
        assert "✗" in out

    def test_TC054_estimator_result(self, capsys):
        executor = _make_executor()
        executor.results = {
            "circ_c": {
                "ok": True,
                "backend": "AerSimulator",
                "via": "Estimator",
                "expectation_value": 0.123456,
                "probs": None,
                "error": None,
            }
        }
        executor.print_summary()
        out = capsys.readouterr().out
        assert "Estimator" in out
        assert "0.123456" in out


if __name__ == "__main__":
    pytest.main([__file__, "-v",
                 "--cov=uqi_executor_ibm", "--cov-report=term-missing"])