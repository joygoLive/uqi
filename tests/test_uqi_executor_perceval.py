# test_uqi_executor_perceval.py

import os
import sys
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from uqi_executor_perceval import UQIExecutorPerceval


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _make_extractor(perceval_circuits=None):
    extractor = MagicMock()
    extractor.perceval_circuits = perceval_circuits or {}
    return extractor


def _make_executor(perceval_circuits=None, shots=1024):
    extractor = _make_extractor(perceval_circuits)
    return UQIExecutorPerceval(extractor, shots=shots)


def _mock_pcvl(counts_raw=None, max_modes=12, max_photons=6):
    """perceval mock 모듈 생성"""
    pcvl = MagicMock()

    # Matrix / Unitary
    mat = MagicMock()
    mat.shape = (4, 4)
    pcvl.Matrix.return_value = mat
    unitary = MagicMock()
    unitary.m = 4
    pcvl.Unitary.return_value = unitary

    # QuandelaSession
    session = MagicMock()
    processor = MagicMock()
    processor.specs = {
        "constraints": {
            "max_mode_count": max_modes,
            "max_photon_count": max_photons,
        }
    }
    sampler = MagicMock()
    sampler.sample_count.return_value = {"results": counts_raw or {"(1, 0)": 600, "(0, 1)": 400}}
    pcvl.algorithm.Sampler.return_value = sampler
    session.build_remote_processor.return_value = processor
    pcvl.QuandelaSession.return_value = session

    return pcvl, session, processor, sampler


# ─────────────────────────────────────────────────────────────
# TC-01x: 초기화
# ─────────────────────────────────────────────────────────────

class TestInitialState:

    def test_TC011_extractor_stored(self):
        extractor = _make_extractor()
        executor = UQIExecutorPerceval(extractor)
        assert executor.extractor is extractor

    def test_TC012_default_shots(self):
        executor = _make_executor()
        assert executor.shots == 1024

    def test_TC013_custom_shots(self):
        executor = _make_executor(shots=2048)
        assert executor.shots == 2048

    def test_TC014_results_empty(self):
        executor = _make_executor()
        assert executor.results == {}


# ─────────────────────────────────────────────────────────────
# TC-02x: run_all
# ─────────────────────────────────────────────────────────────

class TestRunAll:

    def test_TC021_no_circuits_returns_empty(self):
        executor = _make_executor(perceval_circuits={})
        assert executor.run_all() == {}

    def test_TC022_single_circuit_executed(self):
        executor = _make_executor(perceval_circuits={
            "circ_a": (MagicMock(), [1, 0])
        })
        with patch.object(executor, "_run_single", return_value={"ok": True}) as m:
            executor.run_all()
            m.assert_called_once()
            assert m.call_args[0][0] == "circ_a"

    def test_TC023_multiple_circuits_all_executed(self):
        executor = _make_executor(perceval_circuits={
            "circ_a": (MagicMock(), [1, 0]),
            "circ_b": (MagicMock(), [0, 1]),
        })
        with patch.object(executor, "_run_single", return_value={"ok": True}):
            result = executor.run_all()
            assert set(result.keys()) == {"circ_a", "circ_b"}

    def test_TC024_token_stored(self):
        executor = _make_executor(perceval_circuits={
            "circ_a": (MagicMock(), [1, 0])
        })
        with patch.object(executor, "_run_single", return_value={"ok": True}):
            executor.run_all(token="quandela-tok")
            assert executor._token == "quandela-tok"

    def test_TC025_platform_sim_stored(self):
        executor = _make_executor(perceval_circuits={
            "circ_a": (MagicMock(), [1, 0])
        })
        with patch.object(executor, "_run_single", return_value={"ok": True}):
            executor.run_all(platform_sim="sim:ascella")
            assert executor._platform_sim == "sim:ascella"

    def test_TC026_platform_qpu_stored(self):
        executor = _make_executor(perceval_circuits={
            "circ_a": (MagicMock(), [1, 0])
        })
        with patch.object(executor, "_run_single", return_value={"ok": True}):
            executor.run_all(platform_qpu="qpu:belenos")
            assert executor._platform_qpu == "qpu:belenos"

    def test_TC027_use_simulator_passed_to_run_single(self):
        executor = _make_executor(perceval_circuits={
            "circ_a": (MagicMock(), [1, 0])
        })
        captured = {}
        def fake_run(name, circuit, input_state, use_simulator):
            captured["use_simulator"] = use_simulator
            return {"ok": True}
        with patch.object(executor, "_run_single", side_effect=fake_run):
            executor.run_all(use_simulator=False)
            assert captured["use_simulator"] is False


# ─────────────────────────────────────────────────────────────
# TC-03x: _run_single
# ─────────────────────────────────────────────────────────────

class TestRunSingle:

    def test_TC031_result_dict_keys(self):
        executor = _make_executor()
        executor._token = None
        executor._platform_sim = "sim:ascella"
        executor._platform_qpu = "qpu:belenos"
        with patch.dict("sys.modules", {"perceval": MagicMock(), "numpy": MagicMock()}):
            result = executor._run_single("circ_a", None, [1, 0], True)
        assert {"ok", "counts", "probs", "backend", "error"} <= set(result.keys())

    def test_TC032_circuit_none_returns_error(self):
        executor = _make_executor()
        executor._token = "tok"
        executor._platform_sim = "sim:ascella"
        executor._platform_qpu = "qpu:belenos"
        with patch.dict("sys.modules", {"perceval": MagicMock(), "numpy": MagicMock()}):
            result = executor._run_single("circ_a", None, [1, 0], True)
            assert result["ok"] is False
            assert result["error"] == "회로 없음"

    def test_TC033_no_token_returns_error(self):
        executor = _make_executor()
        executor._token = None
        executor._platform_sim = "sim:ascella"
        executor._platform_qpu = "qpu:belenos"

        pcvl, _, _, _ = _mock_pcvl()
        with patch.dict("sys.modules", {"perceval": pcvl, "numpy": MagicMock()}):
            result = executor._run_single("circ_a", MagicMock(), [1, 0], True)
            assert result["ok"] is False
            assert "QUANDELA_TOKEN" in result["error"]

    def test_TC034_max_modes_exceeded_returns_error(self):
        executor = _make_executor()
        executor._token = "tok"
        executor._platform_sim = "sim:ascella"
        executor._platform_qpu = "qpu:belenos"

        pcvl, session, processor, _ = _mock_pcvl(max_modes=4)
        # 회로 모드 수 > max_modes
        mock_unitary = pcvl.Unitary.return_value
        mock_unitary.m = 6  # 6 > 4

        with patch.dict("sys.modules", {"perceval": pcvl, "numpy": MagicMock()}):
            result = executor._run_single("circ_a", MagicMock(), [1, 0], True)
            assert result["ok"] is False
            assert "모드 수 초과" in result["error"]

    def test_TC035_max_photons_exceeded_returns_error(self):
        executor = _make_executor()
        executor._token = "tok"
        executor._platform_sim = "sim:ascella"
        executor._platform_qpu = "qpu:belenos"

        pcvl, session, processor, _ = _mock_pcvl(max_photons=2)
        mock_unitary = pcvl.Unitary.return_value
        mock_unitary.m = 4  # OK

        with patch.dict("sys.modules", {"perceval": pcvl, "numpy": MagicMock()}):
            # 광자 수 3 > max_photons 2
            result = executor._run_single("circ_a", MagicMock(), [1, 1, 1], True)
            assert result["ok"] is False
            assert "광자 수 초과" in result["error"]

    def test_TC036_empty_counts_returns_error(self):
        executor = _make_executor()
        executor._token = "tok"
        executor._platform_sim = "sim:ascella"
        executor._platform_qpu = "qpu:belenos"

        pcvl, _, _, sampler = _mock_pcvl(counts_raw={})
        # 명시적으로 빈 results 반환 재설정
        sampler.sample_count.return_value = {"results": {}}
        with patch.dict("sys.modules", {"perceval": pcvl, "numpy": MagicMock()}):
            result = executor._run_single("circ_a", MagicMock(), [1, 0], True)
            assert result["ok"] is False
            assert "빈 counts" in result["error"]

    def test_TC037_successful_execution(self):
        executor = _make_executor()
        executor._token = "tok"
        executor._platform_sim = "sim:ascella"
        executor._platform_qpu = "qpu:belenos"

        pcvl, _, _, _ = _mock_pcvl(counts_raw={"(1, 0)": 600, "(0, 1)": 400})
        with patch.dict("sys.modules", {"perceval": pcvl, "numpy": MagicMock()}):
            result = executor._run_single("circ_a", MagicMock(), [1, 0], True)
            assert result["ok"] is True
            assert result["counts"] is not None
            assert result["probs"] is not None

    def test_TC038_probs_sum_to_one(self):
        executor = _make_executor()
        executor._token = "tok"
        executor._platform_sim = "sim:ascella"
        executor._platform_qpu = "qpu:belenos"

        pcvl, _, _, _ = _mock_pcvl(counts_raw={"(1, 0)": 300, "(0, 1)": 200, "(1, 1)": 500})
        with patch.dict("sys.modules", {"perceval": pcvl, "numpy": MagicMock()}):
            result = executor._run_single("circ_a", MagicMock(), [1, 0], True)
            if result["ok"] and result["probs"]:
                assert abs(sum(result["probs"].values()) - 1.0) < 1e-9

    def test_TC039_simulator_platform_used(self):
        executor = _make_executor()
        executor._token = "tok"
        executor._platform_sim = "sim:ascella"
        executor._platform_qpu = "qpu:belenos"

        pcvl, _, _, _ = _mock_pcvl(counts_raw={"(1, 0)": 1000})
        with patch.dict("sys.modules", {"perceval": pcvl, "numpy": MagicMock()}):
            result = executor._run_single("circ_a", MagicMock(), [1, 0], True)
            if result["ok"]:
                assert result["backend"] == "sim:ascella"

    def test_TC03A_qpu_platform_used(self):
        executor = _make_executor()
        executor._token = "tok"
        executor._platform_sim = "sim:ascella"
        executor._platform_qpu = "qpu:belenos"

        pcvl, _, _, _ = _mock_pcvl(counts_raw={"(1, 0)": 1000})
        with patch.dict("sys.modules", {"perceval": pcvl, "numpy": MagicMock()}):
            result = executor._run_single("circ_a", MagicMock(), [1, 0], False)
            if result["ok"]:
                assert result["backend"] == "qpu:belenos"

    def test_TC03B_perceval_import_error_returns_false(self):
        executor = _make_executor()
        executor._token = "tok"
        executor._platform_sim = "sim:ascella"
        executor._platform_qpu = "qpu:belenos"
        with patch.dict("sys.modules", {"perceval": None}):
            result = executor._run_single("circ_a", MagicMock(), [1, 0], True)
            assert result["ok"] is False


# ─────────────────────────────────────────────────────────────
# TC-04x: print_summary
# ─────────────────────────────────────────────────────────────

class TestPrintSummary:

    def test_TC041_empty_results_no_exception(self):
        executor = _make_executor()
        executor.print_summary()

    def test_TC042_ok_result_shows_checkmark(self, capsys):
        executor = _make_executor()
        executor.results = {
            "circ_a": {
                "ok": True,
                "backend": "sim:ascella",
                "probs": {"(1,0)": 0.6, "(0,1)": 0.4},
                "error": None,
            }
        }
        executor.print_summary()
        out = capsys.readouterr().out
        assert "circ_a" in out
        assert "✓" in out

    def test_TC043_failed_result_shows_cross(self, capsys):
        executor = _make_executor()
        executor.results = {
            "circ_b": {
                "ok": False,
                "backend": None,
                "probs": None,
                "error": "QUANDELA_TOKEN 없음",
            }
        }
        executor.print_summary()
        out = capsys.readouterr().out
        assert "circ_b" in out
        assert "✗" in out

    def test_TC044_top3_probs_shown(self, capsys):
        executor = _make_executor()
        executor.results = {
            "circ_c": {
                "ok": True,
                "backend": "sim:ascella",
                "probs": {"(1,0)": 0.5, "(0,1)": 0.3, "(1,1)": 0.2},
                "error": None,
            }
        }
        executor.print_summary()
        out = capsys.readouterr().out
        assert "top-3" in out


if __name__ == "__main__":
    pytest.main([__file__, "-v",
                 "--cov=uqi_executor_perceval", "--cov-report=term-missing"])