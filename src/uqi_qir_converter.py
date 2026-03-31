# uqi_qir_converter.py
# QASM → QIR 변환
# UQIExtractor 기반 (모든 framework QASM 통일)

from typing import Optional
from uqi_extractor import UQIExtractor


class UQIQIRConverter:

    def __init__(self, extractor: UQIExtractor):
        self.extractor = extractor
        self.qasm_results = {}   # {name: qasm_str}
        self.qir_results = {}    # {name: bytes}
        self.errors = {}         # {name: str}

    # ─────────────────────────────────────────
    # 전체 변환 파이프라인
    # ─────────────────────────────────────────

    def convert_all(self) -> dict:
        # 감지된 모든 framework 목록 (신규 multi-framework 지원)
        frameworks = getattr(self.extractor, 'frameworks', None) or [self.extractor.framework]

        # 모든 framework → extractor.circuits에 QASM 문자열로 저장됨
        # (PennyLane, Qrisp, CUDAQ, Qiskit, Perceval 모두 동일 경로)
        if self.extractor.circuits:
            for name, qasm in self.extractor.circuits.items():
                if not isinstance(qasm, str):
                    # 구버전 Qiskit 호환: QuantumCircuit 객체가 들어온 경우
                    print(f"  [Converter] 변환 시작 (Qiskit 레거시): {name}")
                    self._convert_qiskit(name, qasm)
                    continue
                self.qasm_results[name] = qasm
                fw_tag = name.split('__')[0] if '__' in name else (frameworks[0] if frameworks else '?')
                print(f"  [Converter] QASM 인계 완료 ({fw_tag}): {name} ({len(qasm)} chars)")
                # QIR 변환: 게이트 수 상한 초과 시 스킵
                try:
                    from qiskit import QuantumCircuit
                    qc = QuantumCircuit.from_qasm_str(qasm)
                    if len(qc.data) > 500:
                        print(f"    ⚠ 게이트 수 {len(qc.data)} > 500, QIR 변환 스킵")
                    else:
                        self._qasm_to_qir(name, qasm)
                except Exception as e:
                    print(f"    ⚠ QIR 변환 스킵: {e}")
            ok = [n for n in self.extractor.circuits if self.qasm_results.get(n)]
            print(f"  [Converter] 완료: {len(ok)}/{len(self.extractor.circuits)} QASM 확보")

        # 레거시 perceval_circuits 호환 (구버전 extractor 사용 시)
        elif getattr(self.extractor, 'perceval_circuits', None):
            for name, circuit_info in self.extractor.perceval_circuits.items():
                print(f"  [Converter] 변환 시작 (Perceval 레거시): {name}")
                self._convert_perceval(name, circuit_info)
            ok = [n for n in self.extractor.perceval_circuits if self.qir_results.get(n)]
            print(f"  [Converter] 완료: {len(ok)}/{len(self.extractor.perceval_circuits)} QIR 변환 성공 (Perceval 레거시)")

        if not self.qasm_results and not self.qir_results:
            print("  [Converter] 변환할 회로 없음")

        return self.qir_results

    # ─────────────────────────────────────────
    # OpenQASM → QIR
    # ─────────────────────────────────────────

    def _qasm_to_qir(self, name: str, qasm: str) -> Optional[bytes]:
        try:
            from qiskit import QuantumCircuit
            import pyqir

            qiskit_circuit = QuantumCircuit.from_qasm_str(qasm)
            print(f"    ✓ Qiskit circuit 변환 성공 "
                  f"({qiskit_circuit.num_qubits}q, "
                  f"{len(qiskit_circuit.data)} gates)")

            ir_str = self._circuit_to_qir_ll(qiskit_circuit)
            ctx = pyqir.Context()
            module = pyqir.Module.from_ir(ctx, ir_str)
            qir_bitcode = module.bitcode
            self.qir_results[name] = qir_bitcode
            print(f"    ✓ QIR 변환 성공 ({len(qir_bitcode)} bytes)")
            return qir_bitcode

        except Exception as e:
            self.errors[name] = f"QIR 변환 실패: {e}"
            print(f"    ✗ QIR 변환 실패: {e}")
            return None

    def _circuit_to_qir_ll(self, circuit) -> str:
        """Qiskit QuantumCircuit → QIR LLVM IR (string) via pyqir Builder"""
        import pyqir

        num_qubits = circuit.num_qubits
        num_clbits = circuit.num_clbits or num_qubits

        ctx = pyqir.Context()
        mod = pyqir.Module(ctx, "circuit")
        builder = pyqir.Builder(ctx)
        entry = pyqir.Function(
            pyqir.FunctionType(pyqir.Type.void(ctx), []),
            pyqir.Linkage.EXTERNAL,
            "main",
            mod,
        )
        bb = pyqir.BasicBlock(ctx, "entry", entry)
        builder.insert_at_end(bb)

        qubits = [pyqir.qubit(ctx, i) for i in range(num_qubits)]
        results = [pyqir.result(ctx, i) for i in range(num_clbits)]

        qis = pyqir.BasicQisBuilder(builder)
        gate_map = {
            'h':       lambda q, _: qis.h(q[0]),
            'x':       lambda q, _: qis.x(q[0]),
            'y':       lambda q, _: qis.y(q[0]),
            'z':       lambda q, _: qis.z(q[0]),
            's':       lambda q, _: qis.s(q[0]),
            't':       lambda q, _: qis.t(q[0]),
            'sdg':     lambda q, _: qis.s_adj(q[0]),
            'tdg':     lambda q, _: qis.t_adj(q[0]),
            'cx':      lambda q, _: qis.cx(q[0], q[1]),
            'cz':      lambda q, _: qis.cz(q[0], q[1]),
            'ccx':     lambda q, _: qis.ccx(q[0], q[1], q[2]),
            'rx':      lambda q, p: qis.rx(p[0], q[0]),
            'ry':      lambda q, p: qis.ry(p[0], q[0]),
            'rz':      lambda q, p: qis.rz(p[0], q[0]),
            'measure': lambda q, r: qis.mz(q[0], r[0]),
        }

        for instr in circuit.data:
            gate_name = instr.operation.name
            q_indices = [circuit.find_bit(qb).index for qb in instr.qubits]
            c_indices = [circuit.find_bit(cb).index for cb in instr.clbits]
            params = [float(p) for p in instr.operation.params]

            q_args = [qubits[i] for i in q_indices]
            r_args = [results[i] for i in c_indices]

            if gate_name in gate_map:
                gate_map[gate_name](q_args, params if params else r_args)

        builder.ret(None)
        return str(mod)

    # ─────────────────────────────────────────
    # Qiskit QuantumCircuit → QASM → QIR
    # ─────────────────────────────────────────

    def _convert_qiskit(self, name: str, circuit) -> None:
        try:
            from qiskit.qasm2 import dumps
            if not circuit.cregs:
                circuit.measure_all()
            qasm = dumps(circuit)
            qasm = "\n".join(
                line for line in qasm.splitlines()
                if not line.strip().startswith("gphase")
            )
            self.qasm_results[name] = qasm
            print(f"    ✓ Qiskit → OpenQASM 변환 성공 ({len(qasm)} chars)")
            self._qasm_to_qir(name, qasm)
        except Exception as e:
            self.errors[name] = f"Qiskit 변환 실패: {e}"
            print(f"    ✗ Qiskit 변환 실패: {e}")

    # ─────────────────────────────────────────
    # Perceval → Qiskit 매핑 → QASM → QIR
    # ─────────────────────────────────────────

    def _convert_perceval(self, name: str, circuit_info: tuple) -> None:
        try:
            from qiskit import QuantumCircuit
            from qiskit.qasm2 import dumps as qasm2_dumps

            circuit, input_state = circuit_info
            if circuit is None:
                print(f"    Perceval circuit is None, skipping")
                return

            m = circuit.m
            qc = QuantumCircuit(m, m)

            for _, component in circuit:
                name_c = type(component).__name__
                if 'BS' in name_c:
                    if m >= 2:
                        qc.h(0)
                        qc.cx(0, 1)
                elif 'PS' in name_c:
                    qc.rz(0.5, 0)
                elif 'PERM' in name_c:
                    qc.swap(0, 1) if m >= 2 else None

            qc.measure(list(range(m)), list(range(m)))

            qasm = qasm2_dumps(qc)
            self.qasm_results[name] = qasm
            print(f"    Perceval -> Qiskit mapping ok ({m} modes, {len(qc.data)} gates)")
            self._qasm_to_qir(name, qasm)

        except Exception as e:
            self.errors[name] = f"Perceval conversion failed: {e}"
            print(f"    Perceval conversion failed: {e}")

    # ─────────────────────────────────────────
    # Utility
    # ─────────────────────────────────────────

    def get_result(self, name: str) -> dict:
        return {
            "qasm": self.qasm_results.get(name),
            "qir":  self.qir_results.get(name),
            "qasm_ok": name in self.qasm_results,
            "qir_ok":  name in self.qir_results,
            "error":   self.errors.get(name),
        }

    def print_summary(self):
        print("\n  [Converter] 변환 결과 요약")
        all_names = (list(self.qasm_results.keys())
                     or list(self.extractor.circuits.keys())
                     or list(self.extractor.perceval_circuits.keys()))
        for name in all_names:
            qasm_ok = "✓" if name in self.qasm_results else "✗"
            qir_ok  = "✓" if name in self.qir_results  else "✗"
            err     = self.errors.get(name, "")
            print(f"    {name:<40} OpenQASM:{qasm_ok}  QIR:{qir_ok}  {err}")