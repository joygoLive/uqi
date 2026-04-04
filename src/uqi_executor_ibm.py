# uqi_executor_ibm.py
# QIR → Qiskit Runtime → IBM 실행
# UQIQIRConverter 기반

from typing import Optional
from uqi_qir_converter import UQIQIRConverter


class UQIExecutorIBM:

    def __init__(self, converter: UQIQIRConverter, shots: int = 1024):
        self.converter = converter
        self.shots = shots
        self.results = {}   # {qnode_name: result_dict}

    # ─────────────────────────────────────────
    # 전체 실행
    # ─────────────────────────────────────────

    def run_all(self, use_simulator: bool = True, backend_name: str = "ibm_fez", token: str = None) -> dict:
        self._token = token

        circuit_names = (
            list(self.converter.extractor.tapes.keys()) or
            list(self.converter.extractor.sessions.keys()) or
            list(self.converter.extractor.circuits.keys())
        )

        if not circuit_names:
            print("  [IBM] 실행할 회로 없음")
            return {}

        for name in circuit_names:
            print(f"  [IBM] 실행: {name}")
            qir  = self.converter.qir_results.get(name)
            qasm = self.converter.qasm_results.get(name)
            obs  = self.converter.extractor.observables.get(name)

            if obs is not None:
                print(f"    → Observable 감지: Estimator 경로")
                self.results[name] = self._run_single_estimator(
                    name, qasm, obs, use_simulator, backend_name
                )
            else:
                self.results[name] = self._run_single(
                    name, qir, qasm, use_simulator, backend_name
                )

        ok = [n for n, r in self.results.items() if r["ok"]]
        print(f"  [IBM] 완료: {len(ok)}/{len(self.results)} 실행 성공")
        return self.results

    # ─────────────────────────────────────────
    # 단일 회로 실행 (Sampler)
    # ─────────────────────────────────────────

    def _run_single(
        self,
        name: str,
        qir_bitcode: Optional[bytes],
        qasm: Optional[str],
        use_simulator: bool,
        backend_name: str,
    ) -> dict:

        result = {
            "ok": False,
            "counts": None,
            "probs": None,
            "backend": None,
            "via": None,
            "error": None,
        }

        try:
            from qiskit import QuantumCircuit

            circuit = None

            # Qiskit 원본 회로가 있으면 직접 사용 (커스텀 게이트 보존)
            original_circuit = self.converter.extractor.circuits.get(name)
            if original_circuit is not None:
                circuit = original_circuit.copy()
                result["via"] = "Qiskit-direct"
                print(f"    ✓ Qiskit 원본 회로 직접 사용")
            elif qasm is not None:
                filtered = "\n".join(
                    line for line in qasm.splitlines()
                    if not line.strip().startswith("gphase")
                )
                circuit = QuantumCircuit.from_qasm_str(filtered)
                result["via"] = "QASM"
                print(f"    ✓ QASM → Qiskit circuit")

            if circuit is None:
                result["error"] = "회로 변환 실패 (QIR/QASM 모두 없음)"
                return result

            # ── 측정 추가 (없으면 SamplerV2 실행 불가) ──
            if not circuit.cregs:
                circuit.measure_all(add_bits=True)

            creg_name = circuit.cregs[0].name if circuit.cregs else "meas"

            # ── 백엔드 설정 및 실행 ──
            if use_simulator:
                from qiskit_aer import AerSimulator
                from qiskit_aer.primitives import SamplerV2
                from qiskit import transpile

                backend = AerSimulator()
                result["backend"] = "AerSimulator"

                # 커스텀 게이트 완전 분해 (P(X), LinFunction, Q, F, cmp 등)
                decomposed = circuit
                for _ in range(10):
                    ops = set(decomposed.count_ops().keys())
                    standard = {"cx","cy","cz","h","x","y","z","s","t","sdg","tdg",
                                "rx","ry","rz","u","u1","u2","u3","swap","ccx",
                                "measure","reset","barrier","id"}
                    if ops <= standard:
                        break
                    try:
                        decomposed = decomposed.decompose()
                    except Exception:
                        break

                if not decomposed.cregs:
                    decomposed.measure_all()

                sampler = SamplerV2()
                job = sampler.run([decomposed], shots=self.shots)

            else:
                from qiskit_ibm_runtime import QiskitRuntimeService, SamplerV2
                from qiskit import transpile

                token = getattr(self, '_token', None)
                if token:
                    service = QiskitRuntimeService(
                        channel="ibm_quantum_platform",
                        token=token
                    )
                else:
                    service = QiskitRuntimeService()
                backend = service.backend(backend_name)
                result["backend"] = backend_name
                print(f"    ✓ IBM QPU 연결: {backend_name}")

                circuit = transpile(circuit, target=backend.target, optimization_level=1)
                print(f"    ✓ IBM 네이티브 게이트 트랜스파일 ({len(circuit.data)} gates)")

                sampler = SamplerV2(backend)
                job = sampler.run([circuit], shots=self.shots)

            pub_result = job.result()[0]
            counts = getattr(pub_result.data, creg_name).get_counts()
            total  = sum(counts.values())
            probs  = {k: v / total for k, v in counts.items()}

            result["counts"] = counts
            result["probs"]  = probs
            result["ok"]     = True
            print(f"    ✓ 실행 성공 (backend={result['backend']}, via={result['via']})")

        except Exception as e:
            result["error"] = str(e)
            print(f"    ✗ 실행 실패: {e}")

        return result

    # ─────────────────────────────────────────
    # 비동기 제출 (job_id만 리턴, 결과 대기 없음)
    # ─────────────────────────────────────────

    def _submit_single(
        self,
        name: str,
        qasm: Optional[str],
        backend_name: str,
    ) -> dict:
        """
        실제 IBM QPU에 job을 제출하고 job_id만 즉시 리턴.
        job.result() 블로킹 없음.
        """
        result = {
            "ok":       False,
            "job_id":   None,
            "backend":  backend_name,
            "via":      None,
            "error":    None,
        }
        try:
            from qiskit import QuantumCircuit, transpile
            from qiskit_ibm_runtime import QiskitRuntimeService, SamplerV2

            # 회로 준비
            original_circuit = self.converter.extractor.circuits.get(name)
            if original_circuit is not None:
                circuit = original_circuit.copy()
                result["via"] = "Qiskit-direct"
            elif qasm is not None:
                filtered = "\n".join(
                    line for line in qasm.splitlines()
                    if not line.strip().startswith("gphase")
                )
                circuit = QuantumCircuit.from_qasm_str(filtered)
                result["via"] = "QASM"
            else:
                result["error"] = "회로 없음 (QASM/원본 모두 없음)"
                return result

            if not circuit.cregs:
                circuit.measure_all(add_bits=True)

            # IBM Runtime 연결
            token = getattr(self, '_token', None)
            if token:
                service = QiskitRuntimeService(
                    channel="ibm_quantum_platform", token=token)
            else:
                service = QiskitRuntimeService()

            backend = service.backend(backend_name)
            print(f"    ✓ IBM QPU 연결: {backend_name}")

            circuit = transpile(circuit, target=backend.target, optimization_level=1)
            print(f"    ✓ 트랜스파일 완료 ({len(circuit.data)} gates)")

            sampler = SamplerV2(backend)
            job = sampler.run([circuit], shots=self.shots)

            result["job_id"] = job.job_id()
            result["ok"]     = True
            print(f"    ✓ job 제출 완료: job_id={result['job_id']}")

        except Exception as e:
            result["error"] = str(e)
            print(f"    ✗ job 제출 실패: {e}")

        return result

    @staticmethod
    def fetch_job_status(job_id: str, token: str = None) -> dict:
        """
        job_id로 IBM 클라우드 job 상태 및 결과 조회.
        완료 시 counts/probs 포함, 미완료 시 status만 리턴.
        """
        result = {
            "job_id": job_id,
            "status": None,
            "counts": None,
            "probs":  None,
            "error":  None,
            "done":   False,
        }
        try:
            from qiskit_ibm_runtime import QiskitRuntimeService

            if token:
                service = QiskitRuntimeService(
                    channel="ibm_quantum_platform", token=token)
            else:
                service = QiskitRuntimeService()

            job = service.job(job_id)
            status = job.status()
            result["status"] = str(status)

            # DONE 상태일 때만 결과 가져오기
            done_statuses = {"JobStatus.DONE", "DONE", "done"}
            if str(status) in done_statuses or status.name == "DONE":
                pub_result = job.result()[0]
                # cregs 이름 추정 (첫 번째 creg)
                creg_name = None
                for attr in dir(pub_result.data):
                    if not attr.startswith("_"):
                        creg_name = attr
                        break
                counts = getattr(pub_result.data, creg_name).get_counts()
                total  = sum(counts.values())
                result["counts"] = counts
                result["probs"]  = {k: v / total for k, v in counts.items()}
                result["done"]   = True
                print(f"    ✓ job 완료: {job_id}")
            else:
                print(f"    … job 진행중: {job_id} ({status})")

        except Exception as e:
            result["error"] = str(e)
            print(f"    ✗ job 조회 실패: {e}")

        return result

    @staticmethod
    def cancel_job(job_id: str, token: str = None) -> dict:
        """IBM Runtime job 취소 요청"""
        try:
            from qiskit_ibm_runtime import QiskitRuntimeService

            if token:
                service = QiskitRuntimeService(
                    channel="ibm_quantum_platform", token=token)
            else:
                service = QiskitRuntimeService()

            job = service.job(job_id)
            job.cancel()
            print(f"    ✓ IBM job 취소 요청: {job_id}")
            return {"ok": True}

        except Exception as e:
            print(f"    ✗ IBM job 취소 실패: {e}")
            return {"ok": False, "error": str(e)}

    # ─────────────────────────────────────────
    # Estimator 실행 (Observable 기반)
    # ─────────────────────────────────────────

    def _run_single_estimator(
        self,
        name: str,
        qasm: Optional[str],
        observable,
        use_simulator: bool,
        backend_name: str,
    ) -> dict:

        result = {
            "ok": False,
            "counts": None,
            "probs": None,
            "expectation_value": None,
            "backend": None,
            "via": "Estimator",
            "error": None,
        }

        try:
            from qiskit import QuantumCircuit, transpile

            if qasm is None:
                result["error"] = "QASM 없음"
                return result

            circuit = QuantumCircuit.from_qasm_str(qasm)
            circuit.remove_final_measurements(inplace=True)
            print(f"    ✓ QASM → Qiskit circuit (측정 제거)")

            if use_simulator:
                from qiskit_aer.primitives import EstimatorV2

                result["backend"] = "AerSimulator"

                estimator = EstimatorV2()
                job = estimator.run([(circuit, observable)])
                pub_result = job.result()[0]
                expval = float(pub_result.data.evs)

            else:
                from qiskit_ibm_runtime import QiskitRuntimeService, EstimatorV2

                token = getattr(self, '_token', None)
                if token:
                    service = QiskitRuntimeService(
                        channel="ibm_quantum_platform",
                        token=token
                    )
                else:
                    service = QiskitRuntimeService()
                backend = service.backend(backend_name)
                result["backend"] = backend_name
                print(f"    ✓ IBM QPU 연결: {backend_name}")

                isa_circuit = transpile(circuit, backend=backend, optimization_level=1)
                isa_observable = observable.apply_layout(isa_circuit.layout)

                estimator = EstimatorV2(mode=backend)
                job = estimator.run([(isa_circuit, isa_observable)])
                pub_result = job.result()[0]
                expval = float(pub_result.data.evs)

            result["expectation_value"] = expval
            result["ok"] = True
            print(f"    ✓ Estimator 실행 성공: <H> = {expval:.6f} (backend={result['backend']})")

        except Exception as e:
            result["error"] = str(e)
            print(f"    ✗ Estimator 실행 실패: {e}")

        return result

    # ─────────────────────────────────────────
    # Utility
    # ─────────────────────────────────────────

    def print_summary(self):
        print("\n  [IBM] 실행 결과 요약")
        for name, r in self.results.items():
            status = "✓" if r["ok"] else "✗"
            if r["ok"]:
                via = r.get("via", "")
                if r.get("expectation_value") is not None:
                    detail = f"backend={r['backend']} via={via} [Estimator] | <H>={r['expectation_value']:.6f}"
                else:
                    detail = f"backend={r['backend']} via={via} [Sampler]"
                    if r["probs"]:
                        top3 = sorted(r["probs"].items(), key=lambda x: -x[1])[:3]
                        detail += f" | top-3: {top3}"
            else:
                detail = f"[{'Estimator' if r.get('via') == 'Estimator' else 'Sampler'}] {r['error']}"
            print(f"    {status} {name:<20} {detail}")