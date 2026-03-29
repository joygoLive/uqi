# uqi_executor_cudaq.py
# CUDAQ 커널 → IQM/IBM 직접 실행
# UQIExtractor 기반

import re
from typing import Optional


class UQIExecutorCUDAQ:

    def __init__(self, extractor, shots: int = 1024):
        self.extractor = extractor
        self.shots = shots
        self.results = {}

    def run_all(
        self,
        target: str = "iqm",
        backend_url: str = "https://cocos.resonance.meetiqm.com/garnet",
        use_emulator: bool = False,
        token: str = None,
        run_ibm: bool = False,
        use_ibm_simulator: bool = True,
        ibm_backend_name: str = "ibm_fez",
        ibm_token: str = None,
    ) -> dict:

        if not self.extractor.cudaq_kernels:
            print("  [CUDAQ] 실행할 커널 없음")
            return {}

        for name, info in self.extractor.cudaq_kernels.items():
            print(f"  [CUDAQ] 실행: {name}")
            self.results[name] = self._run_single(
                name, target, backend_url, use_emulator, token
            )

        # IBM 경로
        if run_ibm:
            print(f"  [CUDAQ→IBM] IBM 실행 시작")
            for name, info in self.extractor.cudaq_kernels.items():
                kernel = info['kernel']
                args   = info['args']
                ibm_result = self._run_ibm(
                    name, kernel, args, use_ibm_simulator, ibm_backend_name, ibm_token
                )
                # IBM 결과를 results에 병합
                key = f"{name}_ibm"
                self.results[key] = ibm_result

        ok = [n for n, r in self.results.items() if r["ok"]]
        print(f"  [CUDAQ] 완료: {len(ok)}/{len(self.results)} 실행 성공")
        return self.results

    def _run_single(
        self,
        name: str,
        target: str,
        backend_url: str,
        use_emulator: bool,
        token: str,
    ) -> dict:

        result = {"ok": False, "counts": None, "backend": None, "error": None}

        try:
            import cudaq
            import os
            import importlib.util

            if token:
                os.environ["IQM_TOKEN"] = token

            if use_emulator:
                cudaq.set_target(target, url=backend_url, emulate=True)
                result["backend"] = f"{target}-emulator"
            else:
                cudaq.set_target(target, url=backend_url)
                result["backend"] = f"{target}:{backend_url}"

            print(f"    ✓ target 설정: {result['backend']}")

            info = self.extractor.cudaq_kernels[name]
            kernel     = info['kernel']
            kernel_args = info['args']
            exec_type  = info['type']
            hamiltonian = info.get('hamiltonian')

            original_set_target = cudaq.set_target

            def blocked_set_target(*args, **kwargs):
                pass

            cudaq.set_target = blocked_set_target

            try:
                if exec_type == 'observe':
                    # observe 커널 → sample로 변환 실행
                    obs_result = cudaq.observe(
                        kernel, hamiltonian, *kernel_args
                    )
                    expval = obs_result.expectation()
                    result["counts"] = {"expectation": expval}
                    result["ok"] = True
                    print(f"    ✓ 실행 성공 (observe, <H>={expval:.6f})")

                else:
                    # sample 커널 → 파일 재로드 방식
                    captured_result = [None]
                    original_sample = cudaq.sample

                    def capturing_sample(k, *a, **kw):
                        kw.setdefault('shots_count', self.shots)
                        r = original_sample(k, *a, **kw)
                        captured_result[0] = r
                        return r

                    cudaq.sample = capturing_sample
                    try:
                        spec = importlib.util.spec_from_file_location(
                            "__main__", self.extractor.algorithm_file
                        )
                        mod = importlib.util.module_from_spec(spec)
                        mod.__name__ = "__main__"
                        spec.loader.exec_module(mod)
                    finally:
                        cudaq.sample = original_sample

                    if captured_result[0] is None:
                        raise RuntimeError("cudaq.sample 결과 없음")

                    counts = {}
                    for bitstring, count in captured_result[0].items():
                        counts[bitstring] = int(count)
                    result["counts"] = counts
                    result["ok"] = True
                    print(f"    ✓ 실행 성공 ({len(counts)} 상태)")

            finally:
                cudaq.set_target = original_set_target
                cudaq.reset_target()

        except Exception as e:
            result["error"] = str(e)
            print(f"    ✗ 실행 실패: {e}")

        return result

    # ─────────────────────────────────────────
    # CUDAQ → IBM 실행 경로
    # ─────────────────────────────────────────

    def _run_ibm(
        self,
        name: str,
        kernel,
        args: tuple,
        use_simulator: bool,
        backend_name: str,
        token: Optional[str],
    ) -> dict:

        result = {
            "ok": False,
            "counts": None,
            "probs": None,
            "backend": None,
            "via": "CUDAQ→Unitary→Qiskit",
            "error": None,
        }

        try:
            import cudaq
            import numpy as np
            import re
            import pyqir
            from qiskit import QuantumCircuit

            # ── Step 1: QIR-base 경로 시도 ──
            qc = None
            try:
                qir_str = cudaq.translate(kernel, *args, format='qir-base')
                ctx = pyqir.Context()
                mod = pyqir.Module.from_ir(ctx, qir_str)

                gate_map = {
                    '__quantum__qis__ry__body':   'ry',
                    '__quantum__qis__rx__body':   'rx',
                    '__quantum__qis__rz__body':   'rz',
                    '__quantum__qis__h__body':    'h',
                    '__quantum__qis__x__body':    'x',
                    '__quantum__qis__y__body':    'y',
                    '__quantum__qis__z__body':    'z',
                    '__quantum__qis__s__body':    's',
                    '__quantum__qis__t__body':    't',
                    '__quantum__qis__cnot__body': 'cx',
                    '__quantum__qis__cz__body':   'cz',
                    '__quantum__qis__mz__body':   'measure',
                }

                def qubit_idx(operand):
                    s = str(operand)
                    if 'null' in s:
                        return 0
                    m = re.search(r'i64 (\d+)', s)
                    return int(m.group(1)) if m else 0

                def param_val(operand, concrete_args):
                    s = str(operand)
                    m = re.search(r'double ([\d.e+\-]+)', s)
                    if m:
                        return float(m.group(1))
                    # symbolic %0, %1 → concrete args 순서대로 매핑
                    idx_m = re.search(r'%(\d+)', s)
                    if idx_m and concrete_args:
                        idx = int(idx_m.group(1))
                        if idx < len(concrete_args):
                            return float(concrete_args[idx])
                    return 0.0

                # 큐비트 수 추정
                max_qubit = 0
                for f in mod.functions:
                    if 'mlirgen' not in f.name:
                        continue
                    for block in f.basic_blocks:
                        for instr in block.instructions:
                            s = str(instr)
                            for op in instr.operands:
                                idx = qubit_idx(op)
                                if idx > max_qubit:
                                    max_qubit = idx
                num_qubits = max_qubit + 1

                qc = QuantumCircuit(num_qubits, num_qubits)

                for f in mod.functions:
                    if 'mlirgen' not in f.name:
                        continue
                    for block in f.basic_blocks:
                        for instr in block.instructions:
                            s = str(instr)
                            matched = None
                            for qir_name, qiskit_name in gate_map.items():
                                if qir_name in s:
                                    matched = qiskit_name
                                    break
                            if not matched:
                                continue
                            operands = instr.operands
                            if matched == 'measure':
                                q = qubit_idx(operands[0])
                                qc.measure(q, q)
                            elif matched in ['ry', 'rx', 'rz']:
                                p = param_val(operands[0], args)
                                q = qubit_idx(operands[1])
                                getattr(qc, matched)(p, q)
                            elif matched == 'cx':
                                ctrl = qubit_idx(operands[0])
                                tgt  = qubit_idx(operands[1])
                                qc.cx(ctrl, tgt)
                            else:
                                q = qubit_idx(operands[0])
                                getattr(qc, matched)(q)

                print(f"    ✓ CUDAQ → QIR-base → Qiskit ({num_qubits}q, {len(qc.data)} gates)")

            except Exception as qir_e:
                print(f"    ⚠ QIR-base 경로 실패: {qir_e} → 유니터리 경로 폴백")

                # ── 유니터리 폴백 (≤10q) ──
                U = cudaq.get_unitary(kernel, *args)
                mat = np.array(U)
                num_qubits = int(np.log2(mat.shape[0]))

                if num_qubits > 10:
                    result["error"] = f"큐비트 수 초과 ({num_qubits}q > 10q)"
                    print(f"    ✗ {result['error']}")
                    return result

                qc = QuantumCircuit(num_qubits)
                qc.unitary(mat, list(range(num_qubits)))
                qc.measure_all()
                print(f"    ✓ CUDAQ → Unitary → Qiskit ({num_qubits}q)")

            # ── Step 2: 실행 ──
            creg_name = qc.cregs[0].name if qc.cregs else "meas"

            if use_simulator:
                from qiskit_aer.primitives import SamplerV2

                result["backend"] = "AerSimulator"
                sampler = SamplerV2()
                job = sampler.run([qc], shots=self.shots)

            else:
                from qiskit_ibm_runtime import QiskitRuntimeService, SamplerV2
                from qiskit import transpile

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

                qc = transpile(qc, target=backend.target, optimization_level=1)
                print(f"    ✓ IBM 트랜스파일 ({len(qc.data)} gates)")

                sampler = SamplerV2(backend)
                job = sampler.run([qc], shots=self.shots)

            pub_result = job.result()[0]
            counts = getattr(pub_result.data, creg_name).get_counts()
            total = sum(counts.values())
            probs = {k: v / total for k, v in counts.items()}

            result["counts"] = counts
            result["probs"]  = probs
            result["ok"]     = True
            print(f"    ✓ IBM 실행 성공 (backend={result['backend']})")

        except Exception as e:
            result["error"] = str(e)
            print(f"    ✗ IBM 실행 실패: {e}")

        return result


    def print_summary(self):
        print("\n  [CUDAQ] 실행 결과 요약")
        for name, r in self.results.items():
            status = "✓" if r["ok"] else "✗"
            detail = f"backend={r['backend']}" if r["ok"] else r["error"]
            top = ""
            if r.get("counts"):
                top3 = sorted(r["counts"].items(), key=lambda x: -int(x[1]))[:3]
                top = f" | top-3: {top3}"
            elif r.get("probs"):
                top3 = sorted(r["probs"].items(), key=lambda x: -x[1])[:3]
                top = f" | top-3: {top3}"
            print(f"    {status} {name:<25} {detail}{top}")