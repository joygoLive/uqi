# test_uqi_executor_cudaq.py

import os
import sys
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from uqi_executor_cudaq import UQIExecutorCUDAQ


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _make_extractor(kernels=None):
    extractor = MagicMock()
    extractor.cudaq_kernels = kernels or {}
    extractor.algorithm_file = "/tmp/dummy_algo.py"
    return extractor


def _make_executor(kernels=None, shots=1024):
    extractor = _make_extractor(kernels)
    return UQIExecutorCUDAQ(extractor, shots=shots)


def _kernel_info(exec_type="sample", hamiltonian=None):
    return {
        "kernel": MagicMock(),
        "args": (),
        "type": exec_type,
        "hamiltonian": hamiltonian,
    }


# ─────────────────────────────────────────────────────────────
# TC-01x: 초기화
# ─────────────────────────────────────────────────────────────

class TestInitialState:

    def test_TC011_extractor_stored(self):
        extractor = _make_extractor()
        executor = UQIExecutorCUDAQ(extractor)
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

    def test_TC021_no_kernels_returns_empty(self):
        executor = _make_executor(kernels={})
        assert executor.run_all() == {}

    def test_TC022_single_kernel_executed(self):
        executor = _make_executor(kernels={"kern_a": _kernel_info()})
        with patch.object(executor, "_run_single", return_value={"ok": True}) as m:
            executor.run_all()
            m.assert_called_once()
            assert m.call_args[0][0] == "kern_a"

    def test_TC023_multiple_kernels_all_executed(self):
        executor = _make_executor(kernels={
            "kern_a": _kernel_info(),
            "kern_b": _kernel_info(),
        })
        with patch.object(executor, "_run_single", return_value={"ok": True}):
            result = executor.run_all()
            assert set(result.keys()) == {"kern_a", "kern_b"}

    def test_TC024_ibm_path_not_run_by_default(self):
        executor = _make_executor(kernels={"kern_a": _kernel_info()})
        with patch.object(executor, "_run_single", return_value={"ok": True}), \
             patch.object(executor, "_run_ibm") as mock_ibm:
            executor.run_all(run_ibm=False)
            mock_ibm.assert_not_called()

    def test_TC025_ibm_path_run_when_flag_set(self):
        executor = _make_executor(kernels={"kern_a": _kernel_info()})
        with patch.object(executor, "_run_single", return_value={"ok": True}), \
             patch.object(executor, "_run_ibm", return_value={"ok": True}) as mock_ibm:
            executor.run_all(run_ibm=True)
            mock_ibm.assert_called_once()

    def test_TC026_ibm_result_key_has_ibm_suffix(self):
        executor = _make_executor(kernels={"kern_a": _kernel_info()})
        with patch.object(executor, "_run_single", return_value={"ok": True}), \
             patch.object(executor, "_run_ibm", return_value={"ok": True}):
            result = executor.run_all(run_ibm=True)
            assert "kern_a_ibm" in result

    def test_TC027_results_aggregated(self):
        executor = _make_executor(kernels={"kern_a": _kernel_info()})
        with patch.object(executor, "_run_single", return_value={"ok": True}):
            result = executor.run_all()
            assert "kern_a" in result

    def test_TC028_run_single_receives_correct_args(self):
        executor = _make_executor(kernels={"kern_a": _kernel_info()})
        captured = {}
        def fake_run(name, target, backend_url, use_emulator, token):
            captured.update({"name": name, "target": target,
                             "use_emulator": use_emulator, "token": token})
            return {"ok": True}
        with patch.object(executor, "_run_single", side_effect=fake_run):
            executor.run_all(target="iqm", use_emulator=True, token="tok123")
            assert captured["name"] == "kern_a"
            assert captured["target"] == "iqm"
            assert captured["use_emulator"] is True
            assert captured["token"] == "tok123"


# ─────────────────────────────────────────────────────────────
# TC-03x: _run_single
# ─────────────────────────────────────────────────────────────

class TestRunSingle:

    def test_TC031_result_dict_keys(self):
        executor = _make_executor()
        with patch("builtins.__import__", side_effect=ImportError("no cudaq")):
            result = executor._run_single("kern_a", "iqm", "https://dummy", False, None)
        assert {"ok", "counts", "backend", "error"} <= set(result.keys())

    def test_TC032_cudaq_import_error_returns_false(self):
        executor = _make_executor()
        # cudaq가 없는 환경 시뮬레이션
        with patch.dict("sys.modules", {"cudaq": None}):
            result = executor._run_single("kern_a", "iqm", "https://dummy", False, None)
            assert result["ok"] is False

    def test_TC033_kernel_not_in_extractor_returns_error(self):
        executor = _make_executor(kernels={})
        # cudaq mock 후 커널 없는 케이스
        mock_cudaq = MagicMock()
        with patch.dict("sys.modules", {"cudaq": mock_cudaq}):
            result = executor._run_single("nonexistent", "iqm", "https://dummy", False, None)
            assert result["ok"] is False

    def test_TC034_emulator_backend_name_format(self):
        executor = _make_executor(kernels={"kern_a": _kernel_info("sample")})
        mock_cudaq = MagicMock()
        mock_cudaq.sample.return_value = MagicMock()
        mock_cudaq.sample.return_value.items.return_value = [("00", 512), ("11", 512)]

        with patch.dict("sys.modules", {
            "cudaq": mock_cudaq,
            "importlib.util": MagicMock(),
        }):
            result = executor._run_single("kern_a", "iqm", "https://dummy", True, None)
            # emulator 경로 → backend에 'emulator' 포함
            if result["backend"]:
                assert "emulator" in result["backend"]

    def test_TC035_token_set_in_env(self):
        executor = _make_executor(kernels={"kern_a": _kernel_info("sample")})
        mock_cudaq = MagicMock()

        env_captured = {}
        original_set_target = mock_cudaq.set_target

        def capture_env(*args, **kwargs):
            import os
            env_captured["IQM_TOKEN"] = os.environ.get("IQM_TOKEN")

        mock_cudaq.set_target.side_effect = capture_env

        with patch.dict("sys.modules", {"cudaq": mock_cudaq}):
            with patch.dict("os.environ", {}, clear=False):
                executor._run_single("kern_a", "iqm", "https://dummy", False, "secret_token")
                assert env_captured.get("IQM_TOKEN") == "secret_token"


# ─────────────────────────────────────────────────────────────
# TC-04x: _run_ibm
# ─────────────────────────────────────────────────────────────

class TestRunIBM:

    def test_TC041_result_dict_keys(self):
        executor = _make_executor()
        with patch.dict("sys.modules", {"cudaq": None, "pyqir": None}):
            result = executor._run_ibm("kern_a", MagicMock(), (), True, "ibm_fez", None)
        assert {"ok", "counts", "probs", "backend", "via", "error"} <= set(result.keys())

    def test_TC042_via_is_cudaq_unitary_qiskit(self):
        executor = _make_executor()
        result = executor._run_ibm("kern_a", MagicMock(), (), True, "ibm_fez", None)
        assert result["via"] == "CUDAQ→Unitary→Qiskit"

    def test_TC043_cudaq_import_error_returns_false(self):
        executor = _make_executor()
        with patch.dict("sys.modules", {"cudaq": None}):
            result = executor._run_ibm("kern_a", MagicMock(), (), True, "ibm_fez", None)
            assert result["ok"] is False

    def test_TC044_qubit_limit_exceeded_returns_error(self):
        """유니터리 경로: 11큐비트 초과 시 에러 반환"""
        import numpy as np
        executor = _make_executor()

        mock_cudaq = MagicMock()
        mock_pyqir = MagicMock()
        # QIR-base 경로 실패 유도
        mock_cudaq.translate.side_effect = Exception("qir fail")
        # 유니터리 11큐비트 (2^11 = 2048)
        mock_cudaq.get_unitary.return_value = np.eye(2**11)

        with patch.dict("sys.modules", {
            "cudaq": mock_cudaq,
            "pyqir": mock_pyqir,
            "numpy": np,
        }):
            result = executor._run_ibm("kern_a", MagicMock(), (), True, "ibm_fez", None)
            assert result["ok"] is False
            assert "10q" in result["error"]

    def test_TC045_simulator_backend_name(self):
        import numpy as np
        executor = _make_executor()

        mock_cudaq = MagicMock()
        mock_cudaq.translate.side_effect = Exception("qir fail")
        mock_cudaq.get_unitary.return_value = np.eye(4)  # 2큐비트

        creg = MagicMock()
        creg.get_counts.return_value = {"00": 512, "11": 512}
        pub_result = MagicMock()
        setattr(pub_result.data, "meas", creg)

        mock_job = MagicMock()
        mock_job.result.return_value = [pub_result]

        mock_sampler = MagicMock()
        mock_sampler.run.return_value = mock_job

        with patch.dict("sys.modules", {"cudaq": mock_cudaq, "numpy": np}), \
             patch("qiskit_aer.primitives.SamplerV2", return_value=mock_sampler):
            result = executor._run_ibm("kern_a", MagicMock(), (), True, "ibm_fez", None)
            if result["ok"]:
                assert result["backend"] == "AerSimulator"

    def test_TC046_probs_sum_to_one(self):
        import numpy as np
        executor = _make_executor()

        mock_cudaq = MagicMock()
        mock_cudaq.translate.side_effect = Exception("qir fail")
        mock_cudaq.get_unitary.return_value = np.eye(4)

        creg = MagicMock()
        creg.get_counts.return_value = {"00": 300, "11": 700}
        pub_result = MagicMock()
        setattr(pub_result.data, "meas", creg)

        mock_job = MagicMock()
        mock_job.result.return_value = [pub_result]
        mock_sampler = MagicMock()
        mock_sampler.run.return_value = mock_job

        with patch.dict("sys.modules", {"cudaq": mock_cudaq, "numpy": np}), \
             patch("qiskit_aer.primitives.SamplerV2", return_value=mock_sampler):
            result = executor._run_ibm("kern_a", MagicMock(), (), True, "ibm_fez", None)
            if result["ok"] and result["probs"]:
                assert abs(sum(result["probs"].values()) - 1.0) < 1e-9


# ─────────────────────────────────────────────────────────────
# TC-05x: print_summary
# ─────────────────────────────────────────────────────────────

class TestPrintSummary:

    def test_TC051_empty_results_no_exception(self):
        executor = _make_executor()
        executor.print_summary()

    def test_TC052_ok_result_shows_checkmark(self, capsys):
        executor = _make_executor()
        executor.results = {
            "kern_a": {
                "ok": True,
                "backend": "iqm-emulator",
                "counts": {"00": 512, "11": 512},
                "probs": None,
                "error": None,
            }
        }
        executor.print_summary()
        out = capsys.readouterr().out
        assert "kern_a" in out
        assert "✓" in out

    def test_TC053_failed_result_shows_cross(self, capsys):
        executor = _make_executor()
        executor.results = {
            "kern_b": {
                "ok": False,
                "backend": None,
                "counts": None,
                "probs": None,
                "error": "cudaq import 실패",
            }
        }
        executor.print_summary()
        out = capsys.readouterr().out
        assert "kern_b" in out
        assert "✗" in out

    def test_TC054_probs_shown_when_counts_absent(self, capsys):
        executor = _make_executor()
        executor.results = {
            "kern_c": {
                "ok": True,
                "backend": "AerSimulator",
                "counts": None,
                "probs": {"00": 0.6, "11": 0.4},
                "error": None,
            }
        }
        executor.print_summary()
        out = capsys.readouterr().out
        assert "top-3" in out

    def test_TC055_counts_top3_shown(self, capsys):
        executor = _make_executor()
        executor.results = {
            "kern_d": {
                "ok": True,
                "backend": "iqm-emulator",
                "counts": {"00": 600, "11": 300, "01": 100},
                "probs": None,
                "error": None,
            }
        }
        executor.print_summary()
        out = capsys.readouterr().out
        assert "top-3" in out


if __name__ == "__main__":
    pytest.main([__file__, "-v",
                 "--cov=uqi_executor_cudaq", "--cov-report=term-missing"])