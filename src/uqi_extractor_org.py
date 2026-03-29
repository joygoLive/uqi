# uqi_extractor.py
# PennyLane 회로 추출 - monkey patch 기반 범용 추출기
# 알고리즘 종속적 처리 없음

from pathlib import Path
import pennylane as qml
from pennylane.workflow import construct_batch
from typing import Optional

class UQIExtractor:

    TAPE_EXPAND_DEPTH = 15

    def __init__(self, algorithm_file: str):
        self.algorithm_file = algorithm_file
        self.framework = None
        self.tapes = {}               # PennyLane: {qnode_name: tape}
        self.sessions = {}            # Qrisp: {circuit_name: QuantumSession}
        self.cudaq_kernels = {}       # CUDAQ: {kernel_name: (kernel_fn, args)}
        self.cudaq_sample_count = 0
        self.qnode_call_counts = {}   # {qnode_name: count}
        self.observables = {}         # PennyLane: {qnode_name: SparsePauliOp}
        self.circuits = {}            # Qiskit: {circuit_name: QuantumCircuit}
        self.perceval_circuits = {}   # Perceval: {name: (processor, input_state)}

    # ─────────────────────────────────────────
    # Framework 감지
    # ─────────────────────────────────────────

    def detect_framework(self) -> str:
        if not Path(self.algorithm_file).exists():
            raise FileNotFoundError(f"파일 없음: {self.algorithm_file}")

        with open(self.algorithm_file, 'r') as f:
            source = f.read()

        if 'import cudaq' in source or 'from cudaq' in source:
            self.framework = 'CUDAQ'
        elif 'import perceval' in source or 'from perceval' in source:
            self.framework = 'Perceval'
        elif 'import pennylane' in source or 'from pennylane' in source:
            self.framework = 'PennyLane'
        elif 'import qrisp' in source or 'from qrisp' in source:
            self.framework = 'Qrisp'
        elif 'import qiskit' in source or 'from qiskit' in source:
            self.framework = 'Qiskit'
        else:
            raise ValueError("양자 프레임워크를 감지할 수 없습니다")

        return self.framework

    # ─────────────────────────────────────────
    # 회로 추출 진입점
    # ─────────────────────────────────────────

    def extract_circuits(self):
        if self.framework == 'PennyLane':
            self._extract_pennylane_circuits()
        elif self.framework == 'Qrisp':
            self._extract_qrisp_circuits()
        elif self.framework == 'CUDAQ':
            self._extract_cudaq_circuits()
        elif self.framework == 'Qiskit':
            self._extract_qiskit_circuits()
        elif self.framework == 'Perceval':
            self._extract_perceval_circuits()
        else:
            raise NotImplementedError(f"현재 검증 범위 외 framework: {self.framework}")

    # ─────────────────────────────────────────
    # PennyLane 추출 (monkey patch 기반)
    # ─────────────────────────────────────────

    def _extract_pennylane_circuits(self):
        print(f"  [Extractor] PennyLane 회로 추출 시작")

        all_created_qnodes = {}   # {id: qnode_obj}
        qnode_name_mapping = {}   # {id: name}
        extractor_self = self

        # ── QNode 생성 추적 ──
        original_qnode_init = qml.QNode.__init__

        def tracked_init(qnode_self, *args, **kwargs):
            original_qnode_init(qnode_self, *args, **kwargs)
            qnode_id = id(qnode_self)
            all_created_qnodes[qnode_id] = qnode_self
            if hasattr(qnode_self, 'func') and hasattr(qnode_self.func, '__name__'):
                qnode_name_mapping[qnode_id] = qnode_self.func.__name__

        # ── QNode 호출 추적 ──
        original_qnode_call = qml.QNode.__call__

        def tracked_call(qnode_self, *args, **kwargs):
            qnode_id = id(qnode_self)
            name = qnode_name_mapping.get(qnode_id, f"qnode_{qnode_id}")
            extractor_self.qnode_call_counts[name] = \
                extractor_self.qnode_call_counts.get(name, 0) + 1

            # 호출 시점에 tape 캡처 (PennyLane 0.44)
            try:
                tape_fn = qml.workflow.construct_tape(qnode_self)
                tape = tape_fn(*args, **kwargs)
                extractor_self.tapes[name] = tape
            except Exception as e:
                try:
                    # fallback: construct_batch
                    batch_fn = construct_batch(qnode_self, level="top")
                    batch, _ = batch_fn(*args, **kwargs)
                    tape = batch[0] if isinstance(batch, (list, tuple)) else batch
                    extractor_self.tapes[name] = tape
                except Exception as e2:
                    print(f"    ✗ tape 캡처 실패: {e2}")

            # Observable 캡처 (Estimator용)
            try:
                tape_for_obs = extractor_self.tapes.get(name)
                if tape_for_obs is not None:
                    obs = extractor_self._pl_obs_to_sparse_pauli(tape_for_obs)
                    if obs is not None:
                        extractor_self.observables[name] = obs
            except Exception as e:
                pass  # Observable 없는 회로는 정상 (Sampler용)

            return original_qnode_call(qnode_self, *args, **kwargs)

        qml.QNode.__init__ = tracked_init
        qml.QNode.__call__ = tracked_call

        try:
            import matplotlib
            matplotlib.use('Agg')

            # 변수명으로도 QNode 추적
            class TrackingDict(dict):
                def __setitem__(self_dict, key, value):
                    super().__setitem__(key, value)
                    if isinstance(value, qml.QNode):
                        qnode_name_mapping[id(value)] = key

            exec_globals = TrackingDict({'__name__': '__main__'})
            with open(self.algorithm_file, 'r') as f:
                code = f.read()
            exec(code, exec_globals)

        finally:
            qml.QNode.__init__ = original_qnode_init
            qml.QNode.__call__ = original_qnode_call

        # ── QNode 수집 ──
        qnodes = {
            qnode_name_mapping.get(qid, f"qnode_{qid}"): qobj
            for qid, qobj in all_created_qnodes.items()
        }

        if not qnodes:
            print(f"  [Extractor] QNode를 찾을 수 없습니다")
            return

        print(f"  [Extractor] QNode {len(qnodes)}개 발견: {', '.join(qnodes.keys())}")

        if self.tapes:
            print(f"  [Extractor] 추출 완료: {len(self.tapes)}개 tape")
        else:
            print(f"  [Extractor] 추출된 tape 없음")
        for name, count in self.qnode_call_counts.items():
            print(f"    {name}: {count}회 호출")

    # ─────────────────────────────────────────
    # Utility
    # ─────────────────────────────────────────

    def get_total_call_count(self) -> int:
        return sum(self.qnode_call_counts.values())

    def tape_to_openqasm(self, tape) -> str:
        """tape → OpenQASM 3"""
        expanded = tape.expand(
            depth=self.TAPE_EXPAND_DEPTH,
            stop_at=lambda obj: not hasattr(obj, 'decomposition')
        )
        return qml.to_openqasm(expanded)

    # ─────────────────────────────────────────
    # Qrisp 추출 (monkey patch 기반)
    # ─────────────────────────────────────────

    def _extract_qrisp_circuits(self):
        from qrisp import QuantumSession, QuantumVariable
        import matplotlib
        matplotlib.use('Agg')

        print(f"  [Extractor] Qrisp 회로 추출 시작")

        captured_sessions = []
        measured_sessions = []  # get_measurement() 기여 세션
        extractor_self = self
        original_qs_init = QuantumSession.__init__
        original_get_measurement = QuantumVariable.get_measurement
        self.qrisp_measurement_count = 0

        def tracking_qs_init(self_qs, *args, **kwargs):
            original_qs_init(self_qs, *args, **kwargs)
            captured_sessions.append(self_qs)

        def tracking_get_measurement(self_qv, *args, **kwargs):
            extractor_self.qrisp_measurement_count += 1
            # get_measurement() 호출 시점에 해당 세션 캡처
            try:
                qs = self_qv.qs
                if qs not in measured_sessions and hasattr(qs, 'qubits') and len(qs.qubits) > 0:
                    measured_sessions.append(qs)
            except Exception:
                pass
            return original_get_measurement(self_qv, *args, **kwargs)

        QuantumSession.__init__ = tracking_qs_init
        QuantumVariable.get_measurement = tracking_get_measurement

        try:
            exec_globals = {'__name__': '__main__'}
            with open(self.algorithm_file, 'r') as f:
                code = f.read()
            exec(code, exec_globals)
        except Exception as e:
            print(f"  [Extractor] 실행 오류: {e}")
        finally:
            QuantumSession.__init__ = original_qs_init
            QuantumVariable.get_measurement = original_get_measurement

        # measured_sessions 우선, 없으면 valid_sessions 폴백
        valid_sessions = [
            qs for qs in captured_sessions
            if hasattr(qs, 'qubits') and len(qs.qubits) > 0
        ]

        target_sessions = measured_sessions if measured_sessions else valid_sessions

        if not target_sessions:
            print(f"  [Extractor] 유효한 QuantumSession 없음")
            return

        # 세션별로 개별 저장
        self.sessions = {}
        for idx, qs in enumerate(target_sessions):
            name = f"qrisp_circuit_{idx}" if len(target_sessions) > 1 else "qrisp_circuit"
            self.sessions[name] = qs

        print(f"  [Extractor] 추출 완료: {len(target_sessions)}개 세션")
        for name, qs in self.sessions.items():
            print(f"    {name}: 큐비트 수 {len(qs.qubits)}")
        print(f"    측정 호출: {self.qrisp_measurement_count}회")

    # ─────────────────────────────────────────
    # CUDAQ 추출 (monkey patch 기반)
    # ─────────────────────────────────────────

    def _extract_cudaq_circuits(self):
        import cudaq
        import sys

        print(f"  [Extractor] CUDAQ 커널 추출 시작")

        captured = {}
        extractor_self = self

        original_sample     = cudaq.sample
        original_observe    = cudaq.observe
        original_set_target = cudaq.set_target
        cudaq.set_target("qpp-cpu")

        def blocked_set_target(*args, **kwargs):
            pass  # 추출 단계에서 target 변경 차단

        cudaq.set_target = blocked_set_target

        def patched_sample(kernel, *args, **kwargs):
            extractor_self.cudaq_sample_count += 1
            name = getattr(kernel, 'name', f'kernel_{len(captured)}')
            captured[name] = {
                'kernel': kernel,
                'args': args,
                'hamiltonian': None,
                'type': 'sample'
            }
            return original_sample(kernel, *args, **kwargs)

        def patched_observe(kernel, *args, **kwargs):
            extractor_self.cudaq_sample_count += 1
            name = getattr(kernel, 'name', f'kernel_{len(captured)}')
            # hamiltonian(args[0])과 커널 파라미터(args[1:]) 분리
            hamiltonian = args[0] if args else None
            kernel_args = args[1:] if len(args) > 1 else ()
            captured[name] = {
                'kernel': kernel,
                'args': kernel_args,
                'hamiltonian': hamiltonian,
                'type': 'observe'
            }
            return original_observe(kernel, *args, **kwargs)

        cudaq.sample  = patched_sample
        cudaq.observe = patched_observe

        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "__main__", self.algorithm_file
            )
            mod = importlib.util.module_from_spec(spec)
            mod.__name__ = "__main__"
            spec.loader.exec_module(mod)

        except Exception as e:
            print(f"  [Extractor] 실행 오류: {e}")
        finally:
            cudaq.sample     = original_sample
            cudaq.observe    = original_observe
            cudaq.set_target = original_set_target
            cudaq.reset_target()

        if not captured:
            print(f"  [Extractor] cudaq.sample/observe 호출 없음")
            return

        self.cudaq_kernels = captured
        print(f"  [Extractor] 추출 완료: {len(captured)}개 커널")
        print(f"    커널 목록: {', '.join(captured.keys())}")
        print(f"    sample/observe 호출: {self.cudaq_sample_count}회")

    def _extract_qiskit_circuits(self):
        import importlib
        import inspect
        from qiskit import QuantumCircuit

        print(f"  [Extractor] Qiskit 회로 추출 시작")

        captured_circuits = {}
        self.qiskit_run_count = 0
        extractor_self = self

        targets = [
            ("qiskit.primitives",     "StatevectorSampler",  "run"),
            ("qiskit.primitives",     "Sampler",             "run"),
            ("qiskit.primitives",     "StatevectorEstimator","run"),
            ("qiskit.primitives",     "Estimator",           "run"),
            ("qiskit_aer.primitives", "SamplerV2",           "run"),
            ("qiskit_aer.primitives", "Sampler",             "run"),
            ("qiskit_aer.primitives", "EstimatorV2",         "run"),
            ("qiskit_aer",            "AerSimulator",        "run"),
            ("qiskit_ibm_runtime",    "SamplerV2",           "run"),
            ("qiskit_ibm_runtime",    "Sampler",             "run"),
            ("qiskit_ibm_runtime",    "EstimatorV2",         "run"),
        ]

        original_methods = []

        def wrap_run(original_func):
            def patched_run(self_obj, circuits, *args, **kwargs):
                extractor_self.qiskit_run_count += 1
                target_circuits = []

                caller_context = "qiskit_circuit"
                try:
                    for frame_info in inspect.stack():
                        frame_locals = frame_info.frame.f_locals
                        if 'self' in frame_locals:
                            cls_name = frame_locals['self'].__class__.__name__
                            if any(t in cls_name for t in [
                                'Pricing', 'Delta', 'Estimation', 'AmplitudeEstimation'
                            ]):
                                caller_context = cls_name
                                break
                except Exception:
                    pass

                if isinstance(circuits, QuantumCircuit):
                    target_circuits = [(circuits, None)]
                elif hasattr(circuits, "__iter__"):
                    for item in circuits:
                        if isinstance(item, tuple) and len(item) > 0:
                            if isinstance(item[0], QuantumCircuit):
                                # params: Sampler=(circuit, params), Estimator=(circuit, obs, params)
                                params = None
                                if len(item) == 2 and not hasattr(item[1], 'num_qubits'):
                                    params = item[1]  # Sampler PUB
                                elif len(item) >= 3:
                                    params = item[2]  # Estimator PUB
                                target_circuits.append((item[0], params))
                        elif isinstance(item, QuantumCircuit):
                            target_circuits.append((item, None))

                if not target_circuits and hasattr(circuits, 'circuits'):
                    target_circuits = [(qc, None) for qc in circuits.circuits]

                for qc, params in target_circuits:
                    cloned = qc.copy()
                    base_name = getattr(qc, 'name', 'qc')
                    if base_name in ("circuit-0", "circuit"):
                        base_name = "qc"
                    name = f"{caller_context}_{base_name}_{len(captured_circuits)}"
                    # 파라미터화 회로: params 바인딩
                    if params is not None and cloned.parameters:
                        try:
                            import numpy as np
                            param_dict = dict(zip(cloned.parameters, np.asarray(params).flatten()))
                            cloned = cloned.assign_parameters(param_dict)
                            print(f"    ✓ 파라미터 바인딩: {name} ({len(param_dict)}개)")
                        except Exception as e:
                            print(f"    ⚠ 파라미터 바인딩 실패: {e} → 미바인딩 상태로 저장")
                    captured_circuits[name] = cloned

                return original_func(self_obj, circuits, *args, **kwargs)
            return patched_run

        for module_name, class_name, method_name in targets:
            try:
                mod = importlib.import_module(module_name)
                cls = getattr(mod, class_name)
                orig = getattr(cls, method_name)
                original_methods.append((cls, method_name, orig))
                setattr(cls, method_name, wrap_run(orig))
            except (ImportError, AttributeError):
                continue

        try:
            import matplotlib
            matplotlib.use('Agg')
            with open(self.algorithm_file, 'r') as f:
                code = f.read()
            exec(code, {'__name__': '__main__'})
        except Exception as e:
            print(f"  [Extractor] 실행 오류: {e}")
        finally:
            for cls, method_name, orig in original_methods:
                setattr(cls, method_name, orig)

        if not captured_circuits:
            print(f"  [Extractor] 추출된 회로 없음")
            return

        self.circuits = captured_circuits
        print(f"  [Extractor] 추출 완료: {len(captured_circuits)}개 회로")
        print(f"    회로 목록: {', '.join(captured_circuits.keys())}")
        print(f"    Sampler.run() 호출: {self.qiskit_run_count}회")

    def _extract_perceval_circuits(self):
        import perceval as pcvl
        import matplotlib
        matplotlib.use('Agg')

        print(f"  [Extractor] Perceval 회로 추출 시작")

        captured = {}
        extractor_self = self
        original_processor = pcvl.Processor
        original_remote_processor = pcvl.RemoteProcessor

        class CapturingProcessor:
            def __init__(self_p, backend_name_or_modes, *args, **kwargs):
                # Circuit이 두 번째 인자로 직접 전달되는 경우 캡처
                if args and hasattr(args[0], 'm'):
                    self_p._circuit = args[0]
                    inner_args = args[1:]
                else:
                    self_p._circuit = None
                    inner_args = args

                if isinstance(backend_name_or_modes, int):
                    try:
                        self_p._inner = original_processor(backend_name_or_modes, *inner_args, **kwargs)
                    except Exception:
                        self_p._inner = original_processor(backend_name_or_modes)
                else:
                    try:
                        self_p._inner = original_processor(backend_name_or_modes, *inner_args, **kwargs)
                    except Exception:
                        self_p._inner = original_processor(4)
                self_p._input_state = None

            def set_circuit(self_p, circuit):
                self_p._circuit = circuit
                return self_p._inner.set_circuit(circuit)

            def with_input(self_p, input_state):
                self_p._input_state = input_state
                name = f"perceval_circuit_{len(captured)}"
                captured[name] = (self_p._circuit, self_p._input_state)
                # n>4 광자 수 → with_input 스킵 (추출만, 로컬 시뮬 방지)
                try:
                    if input_state.n > 4:
                        return self_p._inner
                except Exception:
                    pass
                try:
                    return self_p._inner.with_input(input_state)
                except Exception:
                    return self_p._inner

            def min_detected_photons_filter(self_p, n):
                try:
                    return self_p._inner.min_detected_photons_filter(n)
                except Exception:
                    pass

            def __getattr__(self_p, name):
                return getattr(self_p._inner, name)

        class CapturingRemoteProcessor(CapturingProcessor):
            def __init__(self_p, name, *args, **kwargs):
                self_p._inner = original_processor("SLOS")
                self_p._circuit = None
                self_p._input_state = None

        pcvl.Processor = CapturingProcessor
        pcvl.RemoteProcessor = CapturingRemoteProcessor

        # Sampler 실행 차단
        from perceval.algorithm import Sampler as PcvlSampler
        original_sample_count = PcvlSampler.sample_count
        original_samples = PcvlSampler.samples

        def mock_sample_count(self_s, count, *args, **kwargs):
            return {'results': {}}

        def mock_samples(self_s, count, *args, **kwargs):
            return {'results': {}}

        PcvlSampler.sample_count = mock_sample_count
        PcvlSampler.samples = mock_samples

        try:
            with open(self.algorithm_file, 'r') as f:
                code = f.read()
            exec(code, {'__name__': '__main__'})
        except Exception as e:
            print(f"  [Extractor] 실행 오류: {e}")
        finally:
            pcvl.Processor = original_processor
            pcvl.RemoteProcessor = original_remote_processor
            PcvlSampler.sample_count = original_sample_count
            PcvlSampler.samples = original_samples

        if not captured:
            print(f"  [Extractor] 추출된 회로 없음")
            return

        self.perceval_circuits = captured
        print(f"  [Extractor] 추출 완료: {len(captured)}개 회로")
        print(f"    회로 목록: {', '.join(captured.keys())}")

    # ─────────────────────────────────────────
    # PennyLane Observable → SparsePauliOp 변환
    # ─────────────────────────────────────────

    def _pl_obs_to_sparse_pauli(self, tape) -> Optional[object]:
        """
        tape.measurements에서 ExpectationMP를 찾아 SparsePauliOp로 변환
        측정이 expval이 아닌 경우 None 반환 (Sampler용 회로)
        """
        try:
            from qiskit.quantum_info import SparsePauliOp
            import pennylane as qml

            expval_measurements = [
                m for m in tape.measurements
                if isinstance(m, qml.measurements.ExpectationMP)
            ]
            if not expval_measurements:
                return None

            pauli_map = {"PauliX": "X", "PauliY": "Y", "PauliZ": "Z", "Identity": "I"}
            num_wires = tape.num_wires
            terms = []

            for meas in expval_measurements:
                obs = meas.obs
                # Hamiltonian / LinearCombination 처리
                if hasattr(obs, 'terms'):
                    coeffs, ops = obs.terms()
                    for coeff, op in zip(coeffs, ops):
                        pauli_str = self._op_to_pauli_str(op, num_wires, pauli_map)
                        if pauli_str:
                            terms.append((pauli_str, float(coeff.real if hasattr(coeff, 'real') else coeff)))
                # 단일 Pauli 처리
                elif hasattr(obs, 'name') and obs.name in pauli_map:
                    wire = obs.wires[0]
                    pauli_str = "I" * (num_wires - 1 - wire) + pauli_map[obs.name] + "I" * wire
                    terms.append((pauli_str, 1.0))
                # Tensor product 처리
                elif hasattr(obs, 'operands'):
                    pauli_str = self._op_to_pauli_str(obs, num_wires, pauli_map)
                    if pauli_str:
                        terms.append((pauli_str, 1.0))

            if not terms:
                return None

            return SparsePauliOp.from_list(terms)

        except Exception as e:
            return None

    def _op_to_pauli_str(self, op, num_wires: int, pauli_map: dict) -> Optional[str]:
        """단일 PennyLane 연산자 → Pauli 문자열 (Qiskit 역순 convention)"""
        try:
            pauli_list = ["I"] * num_wires
            # Tensor product (operands 속성)
            if hasattr(op, 'operands'):
                for sub_op in op.operands:
                    if hasattr(sub_op, 'name') and sub_op.name in pauli_map:
                        wire = sub_op.wires[0]
                        pauli_list[wire] = pauli_map[sub_op.name]
            # 단일 Pauli
            elif hasattr(op, 'name') and op.name in pauli_map:
                wire = op.wires[0]
                pauli_list[wire] = pauli_map[op.name]
            else:
                return None
            # Qiskit은 역순 (qubit 0이 오른쪽)
            return "".join(reversed(pauli_list))
        except Exception:
            return None
        
    def print_tape_info(self, name: str):
        tape = self.tapes.get(name)
        if tape is None:
            print(f"  tape 없음: {name}")
            return
        gate_types = list(set(op.name for op in tape.operations))
        depth = tape.graph.get_depth() if hasattr(tape, 'graph') else len(tape.operations)
        print(f"  [{name}]")
        print(f"    큐비트 수:   {tape.num_wires}")
        print(f"    게이트 수:   {len(tape.operations)}")
        print(f"    회로 깊이:   {depth}")
        print(f"    게이트 종류: {gate_types}")
        print(f"    측정:        {[m.__class__.__name__ for m in tape.measurements]}")