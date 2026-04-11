# uqi_executor_perceval.py
# Perceval 회로 → Quandela 시뮬레이터/QPU 실행
# UQIExtractor 기반

from typing import Optional
from uqi_messages import (
    PERCEVAL_NO_CIRCUIT,
    PERCEVAL_NO_TOKEN,
    PERCEVAL_EMPTY_RESULT,
    perceval_modes_exceeded,
    perceval_photons_exceeded,
)


class UQIExecutorPerceval:

    def __init__(self, extractor, shots: int = 1024):
        self.extractor = extractor
        self.shots = shots
        self.results = {}

    def run_all(
        self,
        use_simulator: bool = True,
        token: str = None,
        platform_sim: str = "sim:ascella",
        platform_qpu: str = "qpu:belenos",
    ) -> dict:
        self._token = token
        self._platform_sim = platform_sim
        self._platform_qpu = platform_qpu

        if not self.extractor.perceval_circuits:
            print("  [Perceval] 실행할 회로 없음")
            return {}

        for name, (circuit, input_state) in self.extractor.perceval_circuits.items():
            print(f"  [Perceval] 실행: {name}")
            self.results[name] = self._run_single(
                name, circuit, input_state, use_simulator
            )

        ok = [n for n, r in self.results.items() if r["ok"]]
        print(f"  [Perceval] 완료: {len(ok)}/{len(self.results)} 실행 성공")
        return self.results

    def _run_single(
        self,
        name: str,
        circuit,
        input_state,
        use_simulator: bool,
    ) -> dict:

        result = {
            "ok": False,
            "counts": None,
            "probs": None,
            "backend": None,
            "error": None,
        }

        try:
            import perceval as pcvl
            import numpy as np

            if circuit is None:
                result["error"] = PERCEVAL_NO_CIRCUIT
                print(f"    ✗ {result['error']}")
                return result

            # ── Step 1: 회로 유니터리 추출 ──
            try:
                mat = pcvl.Matrix(circuit.compute_unitary())
                u = pcvl.Unitary(mat)
                print(f"    ✓ 유니터리 추출 ({mat.shape[0]}x{mat.shape[0]})")
            except Exception as e:
                # compute_unitary 실패 시 원본 회로 직접 사용
                u = circuit
                print(f"    ⚠ 유니터리 추출 실패, 원본 회로 사용: {e}")

            # ── Step 2: 플랫폼 결정 ──
            platform = self._platform_sim if use_simulator else self._platform_qpu

            # ── Step 3: RemoteProcessor 설정 ──
            token = getattr(self, '_token', None)
            if not token:
                result["error"] = PERCEVAL_NO_TOKEN
                print(f"    ✗ {result['error']}")
                return result

            session = pcvl.QuandelaSession(platform_name=platform, token=token)
            session.start()
            p = session.build_remote_processor()
            print(f"    ✓ Quandela 연결: {platform}")

            # ── Step 4: 회로 크기 검증 ──
            circuit_m = u.m if hasattr(u, 'm') else mat.shape[0]
            specs = p.specs
            max_modes = specs.get('constraints', {}).get('max_mode_count', 12)
            max_photons = specs.get('constraints', {}).get('max_photon_count', 6)

            if circuit_m > max_modes:
                result["error"] = perceval_modes_exceeded(circuit_m, max_modes)
                print(f"    ✗ {result['error']}")
                session.stop()
                return result

            n_photons = sum(input_state) if input_state else 1
            if n_photons > max_photons:
                result["error"] = perceval_photons_exceeded(n_photons, max_photons)
                print(f"    ✗ {result['error']}")
                session.stop()
                return result

            # ── Step 5: 실행 ──
            p.set_circuit(u)
            p.with_input(input_state)
            p.min_detected_photons_filter(1)

            sampler = pcvl.algorithm.Sampler(p, max_shots_per_call=self.shots)
            job_result = sampler.sample_count(self.shots)
            session.stop()

            # ── 결과 파싱 ──
            counts_raw = job_result.get('results', {})
            counts = {str(k): int(v) for k, v in counts_raw.items()}
            total = sum(counts.values()) if counts else 0
            probs = {k: v / total for k, v in counts.items()} if total > 0 else {}

            if total == 0:
                result["error"] = PERCEVAL_EMPTY_RESULT
                print(f"    ✗ {result['error']}")
                return result

            result["counts"] = counts
            result["probs"] = probs
            result["backend"] = platform
            result["ok"] = True
            print(f"    ✓ 실행 성공 (backend={platform}, {len(counts)} 상태)")

        except Exception as e:
            result["error"] = str(e)
            print(f"    ✗ 실행 실패: {e}")

        return result

    def print_summary(self):
        print("\n  [Perceval] 실행 결과 요약")
        for name, r in self.results.items():
            status = "✓" if r["ok"] else "✗"
            detail = f"backend={r['backend']}" if r["ok"] else r["error"]
            top = ""
            if r["probs"]:
                top3 = sorted(r["probs"].items(), key=lambda x: -x[1])[:3]
                top = f" | top-3: {top3}"
            print(f"    {status} {name:<30} {detail}{top}")