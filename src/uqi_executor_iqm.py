# uqi_executor_iqm.py
# Qiskit circuit → IQM native 게이트 트랜스파일 → IQMClient 직접 실행
# UQIQIRConverter 기반

import math
from typing import Optional
from uqi_qir_converter import UQIQIRConverter


IQM_NATIVE_GATES = ["prx", "cz", "measure", "reset", "barrier"]

QISKIT_TO_IQM = {
    "h":    "prx",
    "x":    "prx",
    "y":    "prx",
    "z":    "prx",
    "rx":   "prx",
    "ry":   "prx",
    "rz":   "prx",
    "cz":   "cz",
    "cx":   None,
    "measure": "measure",
    "reset":   "reset",
    "barrier": "barrier",
}


class UQIExecutorIQM:

    # IQM Garnet 실제 CZ 허용 토폴로지 (QB 이름 기반, API 확인값)
    GARNET_CZ_LOCI_QB = {
        ('QB1','QB2'),('QB1','QB4'),('QB2','QB5'),('QB3','QB4'),('QB3','QB8'),
        ('QB4','QB5'),('QB4','QB9'),('QB5','QB6'),('QB5','QB10'),('QB6','QB7'),
        ('QB6','QB11'),('QB7','QB12'),('QB8','QB9'),('QB8','QB13'),('QB9','QB10'),
        ('QB9','QB14'),('QB10','QB11'),('QB10','QB15'),('QB11','QB12'),('QB11','QB16'),
        ('QB12','QB17'),('QB13','QB14'),('QB14','QB15'),('QB14','QB18'),('QB15','QB16'),
        ('QB15','QB19'),('QB16','QB17'),('QB16','QB20'),('QB18','QB19'),('QB19','QB20'),
    }

    def __init__(self, converter: UQIQIRConverter, shots: int = 1024):
        self.converter = converter
        self.shots = shots
        self.results = {}

    # ─────────────────────────────────────────
    # 전체 실행
    # ─────────────────────────────────────────

    def run_all(
        self,
        use_simulator: bool = True,
        backend_url: str = "https://resonance.meetiqm.com/computers/garnet",
        token: str = None,
    ) -> dict:
        self._token = token

        circuit_names = list(self.converter.extractor.tapes.keys()) or \
                        list(self.converter.extractor.sessions.keys()) or \
                        list(self.converter.extractor.circuits.keys())

        # QASM 없는 회로 스킵 (실제 QPU 실행 시 추가 캡처된 회로 제거)
        circuit_names = [
            n for n in circuit_names
            if self.converter.qasm_results.get(n) is not None
        ]

        if not circuit_names:
            print("  [IQM] 실행할 회로 없음")
            return {}

        for name in circuit_names:
            print(f"  [IQM] 실행: {name}")
            qasm = self.converter.qasm_results.get(name)
            self.results[name] = self._run_single(
                name, qasm, use_simulator, backend_url
            )

        ok = [n for n, r in self.results.items() if r["ok"]]
        print(f"  [IQM] 완료: {len(ok)}/{len(self.results)} 실행 성공")
        return self.results

    # ─────────────────────────────────────────
    # 단일 회로 실행
    # ─────────────────────────────────────────

    def _run_single(
        self,
        name: str,
        qasm: Optional[str],
        use_simulator: bool,
        backend_url: str,
    ) -> dict:

        result = {
            "ok": False,
            "counts": None,
            "probs": None,
            "backend": None,
            "error": None,
        }

        if qasm is None:
            result["error"] = "QASM 없음"
            print(f"    ✗ {result['error']}")
            return result

        try:
            # ── Step 1: QASM → Qiskit circuit ──
            from qiskit import QuantumCircuit, transpile
            from qiskit.transpiler import CouplingMap
            circuit = QuantumCircuit.from_qasm_str(qasm)

            # ── Step 2: 실시간 토폴로지 조회 (패딩 전에 먼저 실행)
            basis_gates = ["rx", "ry", "rz", "cz", "measure", "reset"]
            cz_loci = self._get_cz_loci(backend_url)
            qubit_map = getattr(self, '_qubit_index_map', {})
            device_n_qubits = len(qubit_map) or 20

            # 장비 큐비트 수에 맞게 패딩
            if circuit.num_qubits < device_n_qubits:
                from qiskit import QuantumRegister
                qr = QuantumRegister(device_n_qubits, 'q')
                qc_pad = QuantumCircuit(qr)
                qc_pad.compose(circuit, qubits=list(range(circuit.num_qubits)), inplace=True)
                circuit = qc_pad
            print(f"    ✓ QASM → Qiskit circuit ({circuit.num_qubits}q)")

            if cz_loci:
                edges = [(qubit_map[a], qubit_map[b]) for a, b in cz_loci
                         if a in qubit_map and b in qubit_map]
                if not edges:
                    n = device_n_qubits
                    edges = [(i, j) for i in range(n) for j in range(n) if i != j]
                edges_sym = list(set(edges + [(b, a) for a, b in edges]))
                coupling_map = CouplingMap(edges_sym)
            else:
                n = device_n_qubits
                if qubit_map:
                    print(f"    ⚠ CZ 토폴로지 없음 → {n}큐비트 all-to-all fallback")
                    all_edges = [(i, j) for i in range(n) for j in range(n) if i != j]
                    coupling_map = CouplingMap(all_edges)
                else:
                    garnet_edges = [(int(a[2:])-1, int(b[2:])-1) for a, b in self.GARNET_CZ_LOCI_QB]
                    garnet_edges += [(b, a) for a, b in garnet_edges]
                    coupling_map = CouplingMap(garnet_edges)

            transpiled = transpile(
                circuit,
                basis_gates=basis_gates,
                coupling_map=coupling_map,
                optimization_level=1,
            )
            print(f"    ✓ IQM 기저 게이트 트랜스파일 ({len(transpiled.data)} gates)")

            # ── Step 3: Qiskit circuit → IQM Circuit ──
            iqm_circuit = self._to_iqm_circuit(name, transpiled)
            if iqm_circuit is None:
                result["error"] = "IQM Circuit 변환 실패"
                return result
            print(f"    ✓ IQM Circuit 변환 ({len(iqm_circuit.instructions)} instructions)")

            # ── 실제 QPU: 10000 instructions 초과 시 스킵 ──
            if not use_simulator and len(iqm_circuit.instructions) > 10000:
                result["error"] = f"IQM 제한 초과 ({len(iqm_circuit.instructions)} instructions > 10000)"
                print(f"    ✗ {result['error']}")
                return result

            # ── Step 4: 실행 ──
            if use_simulator:
                counts = self._run_simulator(iqm_circuit)
                result["backend"] = "iqm-client-simulator"
            else:
                counts = self._run_real(iqm_circuit, backend_url)
                result["backend"] = backend_url

            if counts is None:
                result["error"] = "실행 결과 없음"
                return result

            total = sum(counts.values())
            probs = {k: v / total for k, v in counts.items()}

            result["counts"] = counts
            result["probs"]  = probs
            result["ok"]     = True
            print(f"    ✓ 실행 성공 (backend={result['backend']})")

        except Exception as e:
            result["error"] = str(e)
            print(f"    ✗ 실행 실패: {e}")

        return result

    # ─────────────────────────────────────────
    # Qiskit circuit → IQM Circuit 변환
    # ─────────────────────────────────────────

    def _to_iqm_circuit(self, name: str, circuit) -> Optional[object]:
        try:
            from iqm.iqm_client.models import Circuit
            from iqm.pulse.circuit_operations import CircuitOperation

            # _index_to_qubit_map은 _get_cz_loci()에서 실제 장비 QB 이름으로 채워짐
            index_to_qb = getattr(self, '_index_to_qubit_map', {})
            qubit_names = [index_to_qb.get(i, f"QB{i+1}") for i in range(circuit.num_qubits)]
            instructions = []

            for inst in circuit.data:
                gate_name = inst.operation.name
                qubits = tuple(qubit_names[circuit.find_bit(q).index] for q in inst.qubits)
                params = inst.operation.params

                if gate_name in ("rx", "ry", "rz"):
                    angle = float(params[0]) if params else 0.0
                    phase_map = {"rx": 0.0, "ry": 0.25, "rz": 0.5}
                    instructions.append(CircuitOperation(
                        name="prx",
                        locus=qubits,
                        args={
                            "angle": angle,
                            "phase": phase_map[gate_name] * 2 * math.pi,
                        }
                    ))

                elif gate_name == "cz":
                    # IQM CZ loci는 방향성이 있을 수 있음
                    # → _cz_loci_cache에서 허용된 방향으로 교정
                    cz_loci = getattr(self, '_cz_loci_cache', set())
                    if cz_loci:
                        a, b = qubits
                        if (a, b) in cz_loci:
                            pass  # 정방향 OK
                        elif (b, a) in cz_loci:
                            qubits = (b, a)  # 역방향으로 교정
                        else:
                            # 허용 토폴로지에 없는 쌍 → 변환 실패로 처리
                            raise ValueError(
                                f"CZ({a}, {b}) 는 {len(cz_loci)}개 허용 loci에 없음. "
                                f"트랜스파일러가 잘못된 쌍 생성. "
                                f"허용 샘플: {sorted(cz_loci)[:4]}"
                            )
                    instructions.append(CircuitOperation(
                        name="cz",
                        locus=qubits,
                        args={}
                    ))

                elif gate_name == "measure":
                    clbit_idx = circuit.find_bit(inst.clbits[0]).index if inst.clbits else len(instructions)
                    instructions.append(CircuitOperation(
                        name="measure",
                        locus=qubits,
                        args={"key": f"m{clbit_idx}"}
                    ))

                elif gate_name == "reset":
                    instructions.append(CircuitOperation(
                        name="reset",
                        locus=qubits,
                        args={}
                    ))

                elif gate_name == "barrier":
                    pass

                else:
                    print(f"    ⚠ 미지원 게이트 스킵: {gate_name}")

            if not any(op.name == "measure" for op in instructions):
                for i, qb in enumerate(qubit_names):
                    instructions.append(CircuitOperation(
                        name="measure",
                        locus=(qb,),
                        args={"key": f"m{i}"}
                    ))

            return Circuit(name=name, instructions=tuple(instructions))

        except Exception as e:
            print(f"    ✗ IQM Circuit 변환 실패: {e}")
            return None

    # ─────────────────────────────────────────
    # 시뮬레이터 실행 (로컬 - Aer로 대체)
    # ─────────────────────────────────────────

    def _run_simulator(self, iqm_circuit) -> Optional[dict]:
        try:
            from qiskit_aer.primitives import SamplerV2
            from qiskit import QuantumCircuit

            if hasattr(iqm_circuit, 'all_qubits'):
                all_qubits = sorted(iqm_circuit.all_qubits())
            else:
                all_qubits = sorted({q for inst in iqm_circuit.instructions for q in inst.locus})

            num_qubits = len(all_qubits)
            qc = QuantumCircuit(num_qubits)
            qubit_index = {q: i for i, q in enumerate(all_qubits)}

            for inst in iqm_circuit.instructions:
                idxs = [qubit_index[q] for q in inst.locus]
                if inst.name == "prx":
                    angle = inst.args.get("angle", inst.args.get("angle_t", 0.0))
                    if "angle_t" in inst.args:
                        angle = angle * 2 * math.pi
                    qc.rx(angle, idxs[0])
                elif inst.name == "cz":
                    qc.cz(idxs[0], idxs[1])
                elif inst.name == "reset":
                    qc.reset(idxs[0])

            qc.measure_all()

            sampler = SamplerV2()
            job = sampler.run([qc], shots=self.shots)
            pub_result = job.result()[0]
            creg_name = qc.cregs[0].name if qc.cregs else "meas"
            return getattr(pub_result.data, creg_name).get_counts()

        except Exception as e:
            print(f"    ✗ 시뮬레이터 실행 실패: {e}")
            return None

    # ─────────────────────────────────────────
    # 실제 IQM QPU 실행
    # ─────────────────────────────────────────

    def _run_real(self, iqm_circuit, backend_url: str) -> Optional[dict]:
        try:
            from iqm.iqm_client import IQMClient
            import os

            token = getattr(self, '_token', None) or os.getenv("IQM_QUANTUM_TOKEN")
            if not token:
                print(f"    ✗ IQM_QUANTUM_TOKEN 없음")
                return None

            device_name = backend_url.rstrip("/").split("/")[-1]
            client = IQMClient(
                "https://resonance.meetiqm.com",
                quantum_computer=device_name,
                token=token,
            )
            print(f"    ✓ IQM QPU 연결: resonance.meetiqm.com/{device_name}")

            job = client.submit_circuits(
                circuits=[iqm_circuit],
                shots=self.shots,
            )
            print(f"    job_id: {job.job_id}")

            job.wait_for_completion()
            result = job.result()
            if result is None:
                print(f"    ✗ IQM job 결과 없음 (status={job.status})")
                return None
            if not result:
                return {}

            meas = result[0]
            counts = {}
            for key, shots_data in meas.items():
                for shot in shots_data:
                    bitstr = "".join(str(b) for b in shot)
                    counts[bitstr] = counts.get(bitstr, 0) + 1
            return counts

        except Exception as e:
            print(f"    ✗ IQM QPU 실행 실패: {e}")
            return None

    def _get_cz_loci(self, backend_url: str) -> set:
        """IQM 장비에서 실시간 CZ 토폴로지 조회 후 캐시"""
        try:
            from iqm.iqm_client import IQMClient
            import os

            token = getattr(self, '_token', None) or os.getenv("IQM_QUANTUM_TOKEN")
            device_name = backend_url.rstrip("/").split("/")[-1]

            client = IQMClient(
                "https://resonance.meetiqm.com",
                quantum_computer=device_name,
                token=token,
            )
            arch = client.get_dynamic_quantum_architecture()

            # 디버그: 사용 가능한 게이트 키 출력
            gate_keys = list(arch.gates.keys())
            print(f"    ✓ {device_name} 게이트 목록: {gate_keys}")

            # qubit 이름 → 인덱스 맵 (숫자 순 정렬: QB1, QB2, ..., QB9, QB10, ...)
            def _qb_sort_key(q):
                digits = ''.join(c for c in q if c.isdigit())
                return int(digits) if digits else 0
            all_qubits = sorted(arch.qubits, key=_qb_sort_key)
            self._qubit_index_map    = {q: i for i, q in enumerate(all_qubits)}
            self._index_to_qubit_map = {i: q for i, q in enumerate(all_qubits)}
            print(f"    ✓ {device_name} 큐비트 ({len(all_qubits)}개): {all_qubits}")

            # CZ 토폴로지 수집 — 대소문자 무관하게 'cz' 키 탐색
            cz_key = next((k for k in arch.gates if str(k).lower() == 'cz'), None)
            loci = set()
            if cz_key is not None:
                cz = arch.gates[cz_key]
                for impl_info in cz.implementations.values():
                    for locus in impl_info.loci:
                        if len(locus) == 2:
                            loci.add(tuple(locus))
                print(f"    ✓ {device_name} CZ 허용 loci ({len(loci)}개): {sorted(loci)[:5]}{'...' if len(loci)>5 else ''}")
            else:
                print(f"    ⚠ {device_name}: 'cz' 게이트 키 없음 — 전체 gate 키: {gate_keys}")

            self._cz_loci_cache = loci
            return loci
        except Exception as e:
            print(f"    ⚠ CZ 토폴로지 조회 실패: {e}")
            return set()
        
    # ─────────────────────────────────────────
    # 비동기 제출 (job_id만 리턴, wait 없음)
    # ─────────────────────────────────────────

    def _submit_single(
        self,
        name:        str,
        qasm:        str,
        backend_url: str,
    ) -> dict:
        """
        IQM QPU에 job 제출 후 job_id 즉시 리턴.
        qiskit-iqm 기반 트랜스파일 — Garnet/Emerald/Sirius 공진기 아키텍처 모두 지원.
        wait_for_completion() 블로킹 없음.
        """
        result = {
            "ok":          False,
            "job_id":      None,
            "backend_url": backend_url,
            "error":       None,
        }
        try:
            from qiskit import QuantumCircuit
            from iqm.qiskit_iqm import IQMProvider, transpile_to_IQM
            import os

            token = getattr(self, '_token', None) or os.getenv("IQM_QUANTUM_TOKEN")
            if not token:
                result["error"] = "IQM_QUANTUM_TOKEN 없음"
                return result

            device_name = backend_url.rstrip("/").split("/")[-1]
            provider = IQMProvider(
                "https://resonance.meetiqm.com",
                quantum_computer=device_name,
                token=token,
            )
            backend = provider.get_backend()
            print(f"    ✓ IQM backend 연결: {device_name} ({backend.num_qubits}q)")

            # QASM → Qiskit circuit
            circuit = QuantumCircuit.from_qasm_str(qasm)
            if not circuit.cregs:
                circuit.measure_all(add_bits=True)

            # qiskit-iqm 트랜스파일 (MOVE 게이트 라우팅 포함 — Sirius 공진기 아키텍처 자동 처리)
            transpiled = transpile_to_IQM(circuit, backend)
            print(f"    ✓ qiskit-iqm 트랜스파일 완료 ({len(transpiled.data)} instructions)")

            # job 제출 (비동기, 결과 대기 없음)
            job = backend.run(transpiled, shots=self.shots)
            result["job_id"] = job.job_id()
            result["ok"]     = True
            print(f"    ✓ IQM job 제출 완료: {result['job_id']}")

        except Exception as e:
            result["error"] = str(e)
            print(f"    ✗ IQM job 제출 실패: {e}")

        return result

    @staticmethod
    def fetch_job_status(job_id: str, token: str = None, backend_url: str = None) -> dict:
        """
        IQM job 상태 및 결과 조회.
        완료 시 counts 포함, 미완료 시 status만 리턴.
        """
        result = {
            "job_id": job_id,
            "status": None,
            "counts": None,
            "error":  None,
            "done":   False,
        }
        try:
            from iqm.iqm_client import IQMClient
            import os

            token = token or os.getenv("IQM_QUANTUM_TOKEN")
            if not token:
                result["error"] = "IQM_QUANTUM_TOKEN 없음"
                return result

            device_name = (backend_url or "").rstrip("/").split("/")[-1] or "garnet"
            client = IQMClient(
                "https://resonance.meetiqm.com",
                quantum_computer=device_name,
                token=token,
            )

            import uuid
            from iqm.iqm_client import JobStatus

            job = client.get_job(uuid.UUID(job_id))
            status = job.status                             # JobStatus enum
            status_str = status.value                       # 'waiting'|'processing'|'completed'|'failed'|'cancelled'
            result["status"] = status_str

            if status == JobStatus.COMPLETED:
                measurements = client.get_job_measurements(uuid.UUID(job_id))
                if measurements:
                    meas = measurements[0]
                    counts = {}
                    for key, shots_data in meas.items():
                        for shot in shots_data:
                            bitstr = "".join(str(b) for b in shot)
                            counts[bitstr] = counts.get(bitstr, 0) + 1
                    result["counts"] = counts
                    result["done"]   = True
                    print(f"    ✓ IQM job 완료: {job_id}")
            elif status == JobStatus.CANCELLED:
                result["cancelled"] = True
                print(f"    ✕ IQM job 취소됨: {job_id}")
            elif status == JobStatus.FAILED:
                # IQM 장비 측 실패: errors 상세 메시지 수집
                errors = getattr(job.data, 'errors', [])
                if errors:
                    reason = "; ".join(
                        f"[{e.source}] {e.message}" + (f" (code={e.error_code})" if e.error_code else "")
                        for e in errors
                    )
                else:
                    reason = "IQM 장비 실패 (상세 없음)"
                result["error"]          = reason
                result["cloud_failed"]   = True  # 확정 실패 → 재시도 방지
                print(f"    ✗ IQM job 실패: {job_id} — {reason}")
            else:
                print(f"    … IQM job 진행중: {job_id} ({status_str})")

        except Exception as e:
            result["error"] = str(e)
            print(f"    ✗ IQM job 조회 실패: {e}")

        return result

    @staticmethod
    def cancel_job(job_id: str, token: str = None, backend_url: str = None) -> dict:
        """IQM job 취소 요청"""
        try:
            from iqm.iqm_client import IQMClient
            import os, uuid

            token = token or os.getenv("IQM_QUANTUM_TOKEN")
            if not token:
                return {"ok": False, "error": "IQM_QUANTUM_TOKEN 없음"}

            device_name = (backend_url or "").rstrip("/").split("/")[-1] or "garnet"
            client = IQMClient(
                "https://resonance.meetiqm.com",
                quantum_computer=device_name,
                token=token,
            )
            client.cancel_job(uuid.UUID(job_id))
            print(f"    ✓ IQM job 취소 요청: {job_id}")
            return {"ok": True}

        except Exception as e:
            print(f"    ✗ IQM job 취소 실패: {e}")
            return {"ok": False, "error": str(e)}

    # ─────────────────────────────────────────
    # Utility
    # ─────────────────────────────────────────

    def print_summary(self):
        print("\n  [IQM] 실행 결과 요약")
        for name, r in self.results.items():
            status = "✓" if r["ok"] else "✗"
            detail = f"backend={r['backend']}" if r["ok"] else r["error"]
            top = ""
            if r["probs"]:
                top3 = sorted(r["probs"].items(), key=lambda x: -x[1])[:3]
                top = f" | top-3: {top3}"
            print(f"    {status} {name:<20} {detail}{top}")