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

    @staticmethod
    def _restore_perceval_objects(entry):
        """직렬화된 (unitary_data, input_state_list, num_modes) →
        (pcvl.Unitary circuit, pcvl.BasicState) 복원"""
        import perceval as pcvl
        import numpy as np

        unitary_data, is_list, num_modes = entry
        # [[re,im], ...] 2D list → complex numpy array → pcvl.Unitary
        arr = np.array(
            [[complex(c[0], c[1]) for c in row] for row in unitary_data]
        )
        circuit = pcvl.Unitary(pcvl.Matrix(arr))
        input_state = pcvl.BasicState(is_list)
        return circuit, input_state

    @staticmethod
    def get_platform_specs(platform: str, token: str) -> dict:
        """Quandela 플랫폼 스펙 조회 (max_modes, max_photons 등)"""
        try:
            import perceval as pcvl
            session = pcvl.QuandelaSession(platform_name=platform, token=token)
            session.start()
            p = session.build_remote_processor()
            specs = p.specs or {}
            constraints = specs.get('constraints', {}) or {}
            # operational 상태는 p.status 기반 — max_mode_count는 maintenance 중에도
            # nominal 값이 반환되어 오탐 유발 (bcd5868 이후 로직 통일)
            status_str = str(getattr(p, "status", "")).lower()
            is_available = (status_str == "available")
            result = {
                "ok": is_available,
                "platform": platform,
                "max_modes": constraints.get('max_mode_count', 12),
                "max_photons": constraints.get('max_photon_count', 6),
                "type": "Simulator" if platform.startswith("sim:") else "QPU",
                "status": status_str or "unknown",
            }
            session.stop()
            return result
        except Exception as e:
            return {
                "ok": False,
                "platform": platform,
                "error": str(e),
                "max_modes": 12,
                "max_photons": 6,
                "type": "Simulator" if platform.startswith("sim:") else "QPU",
            }

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

        for name, entry in self.extractor.perceval_circuits.items():
            print(f"  [Perceval] 실행: {name}")
            circuit, input_state = self._restore_perceval_objects(entry)
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
        on_submit=None,           # callable(cloud_job_id, platform) — 제출 직후 1회 호출
        max_wait_s: float = 600.0,
        poll_interval_s: float = 2.0,
    ) -> dict:
        """
        Perceval 회로 1개 실행.

        on_submit 콜백은 Quandela 클라우드에 job이 생성된 직후(아직 실행 전) 호출되어
        cloud_job_id를 외부(예: job store)에 즉시 등록할 수 있게 한다.
        이후 결과 대기 중 timeout/에러가 발생해도 cloud_job_id 는 result["cloud_job_id"]
        에 보존되어 호출자가 취소/정리를 수행할 수 있다.
        """

        result = {
            "ok": False,
            "counts": None,
            "probs": None,
            "backend": None,
            "error": None,
            "cloud_job_id": None,
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

            # ── Step 5: 실행 (비동기 제출 → 폴링) ──
            p.set_circuit(u)
            p.with_input(input_state)
            p.min_detected_photons_filter(1)

            sampler = pcvl.algorithm.Sampler(p, max_shots_per_call=self.shots)
            job = sampler.sample_count

            # 비동기 제출 — 즉시 cloud_job_id 확보 (timeout 시 취소/정리용)
            job.execute_async(self.shots)
            cloud_job_id = getattr(job, 'id', None) or getattr(job, '_id', None)
            result["cloud_job_id"] = cloud_job_id
            result["backend"] = platform
            print(f"    ✓ 제출 완료: job_id={cloud_job_id}")

            # 제출 직후 콜백 (외부 job store 등록 등)
            if on_submit and cloud_job_id:
                try:
                    on_submit(cloud_job_id, platform)
                except Exception as _cb_err:
                    print(f"    ⚠ on_submit callback error: {_cb_err}")

            # 결과 대기 (poll)
            import time as _time
            _t_wait_start = _time.time()
            while not job.is_complete:
                if job.is_failed:
                    result["error"] = f"job failed on cloud: status={job.status}"
                    session.stop()
                    return result
                if _time.time() - _t_wait_start > max_wait_s:
                    result["error"] = (f"wait timeout {max_wait_s}s "
                                       f"(cloud_job_id={cloud_job_id} 는 서버에 남아있음)")
                    session.stop()
                    return result
                _time.sleep(poll_interval_s)

            # is_complete=True 이지만 실제로는 ERROR 상태로 끝난 경우가 있음
            # (Perceval이 is_failed를 세팅하지 않는 케이스 방어)
            _final_status = str(getattr(job, 'status', '')).upper()
            if _final_status in ('ERROR', 'CANCELED', 'CANCELLED', 'FAILED'):
                result["error"] = (f"job ended with status={_final_status} "
                                   f"(cloud_job_id={cloud_job_id})")
                session.stop()
                return result

            job_result = job.get_results()
            session.stop()

            # get_results()가 None을 리턴하는 경우 방어 — Quandela ERROR 시 발생
            if job_result is None:
                result["error"] = (f"cloud returned no results (status={_final_status}, "
                                   f"cloud_job_id={cloud_job_id})")
                print(f"    ✗ {result['error']}")
                return result

            # ── 결과 파싱 ──
            counts_raw = job_result.get('results', {}) if isinstance(job_result, dict) else {}
            counts = {str(k): int(v) for k, v in counts_raw.items()}
            total = sum(counts.values()) if counts else 0
            probs = {k: v / total for k, v in counts.items()} if total > 0 else {}

            if total == 0:
                result["error"] = PERCEVAL_EMPTY_RESULT
                print(f"    ✗ {result['error']}")
                return result

            result["counts"] = counts
            result["probs"] = probs
            # backend, cloud_job_id 는 제출 시점에 이미 기록됨
            result["ok"] = True
            print(f"    ✓ 실행 성공 (backend={platform}, job_id={cloud_job_id}, {len(counts)} 상태)")

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