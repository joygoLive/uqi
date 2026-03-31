# uqi_extractor.py
# 범용 양자 회로 추출기 (subprocess 격리 기반)
# UQI (Universal Quantum Infrastructure)

import re
import sys
import os
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Optional
import resource
import signal

# 실행 제한 상수
_SANDBOX_CPU_SEC = 600  # CPU 시간 상한 (초)


def _apply_resource_limits():
    """CPU 시간 상한만 적용 (RLIMIT_AS 제거 - llvmlite mmap 충돌 방지)"""
    resource.setrlimit(resource.RLIMIT_CPU,
                       (_SANDBOX_CPU_SEC, _SANDBOX_CPU_SEC))


class UQIExtractor:

    TAPE_EXPAND_DEPTH = 15

    def __init__(self, algorithm_file: str):
        self.algorithm_file = algorithm_file
        self.framework = None         # 하위 호환성 유지 (첫 번째 감지된 framework)
        self.frameworks = []          # 감지된 모든 framework 목록
        self.tapes = {}               # (legacy, 미사용)
        self.sessions = {}            # (legacy, 미사용)
        self.cudaq_kernels = {}       # (legacy, 미사용)
        self.cudaq_sample_count = 0
        self.qnode_call_counts = {}
        self.observables = {}
        self.circuits = {}            # 모든 framework 공통: {name: qasm_str}
        self.perceval_circuits = {}   # Perceval: {name: (circuit, input_state)}

    # ─────────────────────────────────────────
    # Framework 감지
    # ─────────────────────────────────────────

    @staticmethod
    def _strip_comments(source: str) -> str:
        """Python 소스에서 # 주석과 문자열 리터럴 내부를 제거 (import 감지 정확도 향상)"""
        result = []
        i = 0
        n = len(source)
        while i < n:
            # 삼중 따옴표 문자열 스킵
            if source[i:i+3] in ('"""', "'''"):
                quote = source[i:i+3]
                i += 3
                end = source.find(quote, i)
                i = (end + 3) if end != -1 else n
            # 단일 따옴표 문자열 스킵
            elif source[i] in ('"', "'"):
                q = source[i]
                i += 1
                while i < n and source[i] != q:
                    if source[i] == '\\':
                        i += 1
                    i += 1
                i += 1  # 닫는 따옴표
            # 라인 주석 스킵
            elif source[i] == '#':
                while i < n and source[i] != '\n':
                    i += 1
            else:
                result.append(source[i])
                i += 1
        return ''.join(result)

    def detect_framework(self) -> str:
        """소스 파일에서 사용된 양자 프레임워크를 모두 감지한다.
        주석 및 문자열 리터럴 내부의 import 문은 무시된다.
        self.frameworks: 감지된 모든 framework 목록 (우선순위 순)
        self.framework:  첫 번째 감지된 framework (하위 호환성)
        """
        if not Path(self.algorithm_file).exists():
            raise FileNotFoundError(f"파일 없음: {self.algorithm_file}")

        with open(self.algorithm_file, 'r') as f:
            source = f.read()

        # 주석/문자열 제거 후 활성 소스만 검사
        active = self._strip_comments(source)

        # 우선순위 순서대로 (CUDAQ > Perceval > PennyLane > Qrisp > Qiskit)
        framework_patterns = [
            ('CUDAQ',     [r'\bimport\s+cudaq\b',    r'\bfrom\s+cudaq\b']),
            ('Perceval',  [r'\bimport\s+perceval\b', r'\bfrom\s+perceval\b']),
            ('PennyLane', [r'\bimport\s+pennylane\b', r'\bimport\s+qml\b',
                           r'\bfrom\s+pennylane\b']),
            ('Qrisp',     [r'\bimport\s+qrisp\b',    r'\bfrom\s+qrisp\b']),
            ('Qiskit',    [r'\bimport\s+qiskit\b',   r'\bfrom\s+qiskit\b']),
        ]

        detected = []
        for fw, patterns in framework_patterns:
            if any(re.search(p, active) for p in patterns):
                detected.append(fw)

        if not detected:
            raise ValueError("양자 프레임워크를 감지할 수 없습니다")

        self.frameworks = detected
        self.framework = detected[0]  # 하위 호환성

        if len(detected) > 1:
            print(f"  [Extractor] 복수 framework 감지: {', '.join(detected)}")
            print(f"    각 framework 독립 추출 모드로 실행")
        else:
            print(f"  [Extractor] framework 감지: {detected[0]}")

        return self.framework

    # ─────────────────────────────────────────
    # 회로 추출 진입점
    # ─────────────────────────────────────────

    def extract_circuits(self):
        """감지된 모든 framework에 대해 독립적으로 회로를 추출한다.
        복수 framework인 경우 회로 이름에 'fw__' 접두어를 붙여 구분한다.
        """
        if not self.frameworks:
            raise RuntimeError("detect_framework()를 먼저 호출하세요")

        multi = len(self.frameworks) > 1

        for fw in self.frameworks:
            prefix = f"{fw.lower()}__" if multi else ""
            if fw == 'PennyLane':
                self._extract_pennylane_circuits(prefix=prefix)
            elif fw == 'Qrisp':
                self._extract_qrisp_circuits(prefix=prefix)
            elif fw == 'CUDAQ':
                self._extract_cudaq_circuits(prefix=prefix)
            elif fw == 'Qiskit':
                self._extract_qiskit_circuits(prefix=prefix)
            elif fw == 'Perceval':
                self._extract_perceval_circuits(prefix=prefix)
            else:
                print(f"  [Extractor] 현재 검증 범위 외 framework: {fw}")

    # ─────────────────────────────────────────
    # subprocess 공통 실행기
    # ─────────────────────────────────────────

    _SUBPROCESS_SENTINEL = "__UQI_JSON__:"

    def _run_subprocess(self, script: str, timeout: int = 120) -> Optional[dict]:
        """script를 subprocess로 실행, sentinel(__UQI_JSON__:) 기반 JSON 파싱 후 반환.
        각 subprocess 스크립트는 결과를 print('__UQI_JSON__:' + json.dumps(data)) 형식으로 출력해야 함.
        """
        SENTINEL = self._SUBPROCESS_SENTINEL
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(script)
            tmp_path = f.name
        try:
            result = subprocess.run(
                [sys.executable, tmp_path],
                capture_output=True, text=True, timeout=timeout
            )
            stdout = result.stdout
            if not stdout.strip():
                print(f"  [Extractor] subprocess 출력 없음: {result.stderr[:300]}")
                return None
            # sentinel 기반 JSON 파싱 (가장 신뢰성 높음)
            for line in reversed(stdout.splitlines()):
                line = line.strip()
                if line.startswith(SENTINEL):
                    json_str = line[len(SENTINEL):]
                    try:
                        return json.loads(json_str)
                    except Exception:
                        continue
            # fallback: 마지막 { 로 시작하는 줄 (구버전 스크립트 호환)
            for line in reversed(stdout.splitlines()):
                line = line.strip()
                if line.startswith('{'):
                    try:
                        return json.loads(line)
                    except Exception:
                        continue
            print(f"  [Extractor] JSON 파싱 실패: {stdout[:300]}")
            return None
        except subprocess.TimeoutExpired:
            print(f"  [Extractor] subprocess timeout ({timeout}s)")
            return None
        except Exception as e:
            print(f"  [Extractor] subprocess 오류: {e}")
            return None
        finally:
            os.unlink(tmp_path)

    # ─────────────────────────────────────────
    # PennyLane 추출 (subprocess 격리)
    # ─────────────────────────────────────────

    def _extract_pennylane_circuits(self, prefix: str = ""):
        print(f"  [Extractor] PennyLane 회로 추출 시작")

        alg_file = self.algorithm_file
        expand_depth = self.TAPE_EXPAND_DEPTH

        script = f"""
import sys, json, os
sys.path.insert(0, '{os.path.dirname(os.path.abspath(alg_file))}')

import pennylane as qml
from pennylane.workflow import construct_batch
import matplotlib
matplotlib.use('Agg')

all_created_qnodes = {{}}
qnode_name_mapping = {{}}
tapes = {{}}
qnode_call_counts = {{}}

original_device = qml.device
def mock_device(name, **kwargs):
    if any(hw in name for hw in ['ibm', 'ionq', 'iqm', 'braket', 'azure']):
        wires = kwargs.get('wires', 10)
        return original_device('default.qubit', wires=wires)
    return original_device(name, **kwargs)
qml.device = mock_device

original_qnode_init = qml.QNode.__init__
def tracked_init(qnode_self, *args, **kwargs):
    original_qnode_init(qnode_self, *args, **kwargs)
    qnode_id = id(qnode_self)
    all_created_qnodes[qnode_id] = qnode_self
    if hasattr(qnode_self, 'func') and hasattr(qnode_self.func, '__name__'):
        qnode_name_mapping[qnode_id] = qnode_self.func.__name__

original_qnode_call = qml.QNode.__call__
def tracked_call(qnode_self, *args, **kwargs):
    qnode_id = id(qnode_self)
    name = qnode_name_mapping.get(qnode_id, f"qnode_{{qnode_id}}")
    qnode_call_counts[name] = qnode_call_counts.get(name, 0) + 1
    try:
        tape_fn = qml.workflow.construct_tape(qnode_self)
        tape = tape_fn(*args, **kwargs)
        tapes[name] = tape
    except Exception:
        try:
            batch_fn = construct_batch(qnode_self, level="top")
            batch, _ = batch_fn(*args, **kwargs)
            tape = batch[0] if isinstance(batch, (list, tuple)) else batch
            tapes[name] = tape
        except Exception:
            pass
    return original_qnode_call(qnode_self, *args, **kwargs)

qml.QNode.__init__ = tracked_init
qml.QNode.__call__ = tracked_call

class TrackingDict(dict):
    def __setitem__(self_dict, key, value):
        super().__setitem__(key, value)
        if isinstance(value, qml.QNode):
            qnode_name_mapping[id(value)] = key

exec_globals = TrackingDict({{'__name__': '__main__'}})
try:
    with open(r'{alg_file}', 'r') as f:
        code = f.read()
    exec(code, exec_globals)
except Exception as e:
    pass
finally:
    qml.QNode.__init__ = original_qnode_init
    qml.QNode.__call__ = original_qnode_call
    qml.device = original_device

# tape → QASM 변환
def tape_to_qasm(tape):
    try:
        expanded = tape.expand(
            depth={expand_depth},
            stop_at=lambda obj: not hasattr(obj, 'decomposition')
        )
    except Exception:
        expanded = tape

    num_wires = expanded.num_wires
    wires_list = list(expanded.wires)
    wire_to_idx = {{w: i for i, w in enumerate(wires_list)}}

    lines = [
        "OPENQASM 2.0;",
        'include "qelib1.inc";',
        f"qreg q[{{num_wires}}];",
        f"creg c[{{num_wires}}];",
    ]

    def w(wire):
        return f"q[{{wire_to_idx[wire]}}]"

    # 커스텀 게이트 정의 (qelib1.inc에 없는 게이트)
    CUSTOM_GATE_DEFS = {{
        'sxdg':  'gate sxdg a {{ rx(-pi/2) a; }}',
        'iswap': 'gate iswap a, b {{ s a; s b; h a; cx a, b; cx b, a; h b; }}',
        'rzz':   'gate rzz(theta) a, b {{ cx a, b; rz(theta) b; cx a, b; }}',
        'rxx':   'gate rxx(theta) a, b {{ h a; h b; cx a, b; rz(theta) b; cx a, b; h b; h a; }}',
        'ryy':   'gate ryy(theta) a, b {{ rx(pi/2) a; rx(pi/2) b; cx a, b; rz(theta) b; cx a, b; rx(-pi/2) a; rx(-pi/2) b; }}',
        'ecr':   'gate ecr a, b {{ h b; cx a, b; rz(pi/4) b; cx a, b; h b; x a; h b; cx a, b; rz(-pi/4) b; cx a, b; h b; }}',
    }}

    # qelib1.inc에 포함된 단순 게이트 매핑 (파라미터 없음)
    single_map = {{
        "Hadamard": "h", "PauliX": "x", "PauliY": "y", "PauliZ": "z",
        "S": "s", "T": "t", "SX": "sx", "Adjoint(S)": "sdg",
        "Adjoint(T)": "tdg", "CNOT": "cx", "CZ": "cz", "SWAP": "swap",
        "CY": "cy", "CH": "ch",
    }}

    used_custom = set()
    gate_lines = []

    for op in expanded.operations:
        name = op.name
        wires = op.wires
        params = op.parameters
        if name in single_map:
            gate = single_map[name]
            args = ", ".join(w(wire) for wire in wires)
            gate_lines.append(f"{{gate}} {{args}};")
        elif name == "RX":
            gate_lines.append(f"rx({{float(params[0])}}) {{w(wires[0])}};")
        elif name == "RY":
            gate_lines.append(f"ry({{float(params[0])}}) {{w(wires[0])}};")
        elif name == "RZ":
            gate_lines.append(f"rz({{float(params[0])}}) {{w(wires[0])}};")
        elif name == "PhaseShift":
            gate_lines.append(f"p({{float(params[0])}}) {{w(wires[0])}};")
        elif name == "U1":
            gate_lines.append(f"u1({{float(params[0])}}) {{w(wires[0])}};")
        elif name == "U2":
            gate_lines.append(f"u2({{float(params[0])}},{{float(params[1])}}) {{w(wires[0])}};")
        elif name == "U3":
            gate_lines.append(f"u3({{float(params[0])}},{{float(params[1])}},{{float(params[2])}}) {{w(wires[0])}};")
        elif name in ("Toffoli", "CCX"):
            gate_lines.append(f"ccx {{w(wires[0])}}, {{w(wires[1])}}, {{w(wires[2])}};")
        elif name in ("MultiControlledX", "ctrl"):
            ctrl_args = ", ".join(w(wire) for wire in wires[:-1])
            tgt_arg = w(wires[-1])
            if len(wires) == 3:
                gate_lines.append(f"ccx {{ctrl_args}}, {{tgt_arg}};")
            elif len(wires) == 2:
                gate_lines.append(f"cx {{ctrl_args}}, {{tgt_arg}};")
        elif name == "CRX":
            gate_lines.append(f"crx({{float(params[0])}}) {{w(wires[0])}}, {{w(wires[1])}};")
        elif name == "CRY":
            gate_lines.append(f"cry({{float(params[0])}}) {{w(wires[0])}}, {{w(wires[1])}};")
        elif name == "CRZ":
            gate_lines.append(f"crz({{float(params[0])}}) {{w(wires[0])}}, {{w(wires[1])}};")
        elif name == "Identity":
            gate_lines.append(f"id {{w(wires[0])}};")
        elif name in ("Adjoint(SX)", "SXdg"):
            used_custom.add('sxdg')
            gate_lines.append(f"sxdg {{w(wires[0])}};")
        elif name == "ISWAP":
            used_custom.add('iswap')
            gate_lines.append(f"iswap {{w(wires[0])}}, {{w(wires[1])}};")
        elif name == "RZZ":
            used_custom.add('rzz')
            gate_lines.append(f"rzz({{float(params[0])}}) {{w(wires[0])}}, {{w(wires[1])}};")
        elif name == "RXX":
            used_custom.add('rxx')
            gate_lines.append(f"rxx({{float(params[0])}}) {{w(wires[0])}}, {{w(wires[1])}};")
        elif name == "RYY":
            used_custom.add('ryy')
            gate_lines.append(f"ryy({{float(params[0])}}) {{w(wires[0])}}, {{w(wires[1])}};")
        elif name == "ECR":
            used_custom.add('ecr')
            gate_lines.append(f"ecr {{w(wires[0])}}, {{w(wires[1])}};")
        elif name == "GlobalPhase":
            pass  # 전역 위상 무시 (측정 결과에 영향 없음)
        else:
            import sys as _sys
            print(f"  [QASM] 알 수 없는 게이트 스킵: {{name}}", file=_sys.stderr)
            gate_lines.append(f"// unknown gate: {{name}}")

    # 필요한 커스텀 게이트 정의를 헤더에 삽입
    # rzz가 필요한 경우 rzz 정의를 rxx/ryy/ecr 보다 먼저 삽입
    CUSTOM_GATE_ORDER = ['sxdg', 'iswap', 'rzz', 'rxx', 'ryy', 'ecr']
    for cg in CUSTOM_GATE_ORDER:
        if cg in used_custom:
            lines.append(CUSTOM_GATE_DEFS[cg])

    lines.extend(gate_lines)

    for wire in wires_list:
        lines.append(f"measure {{w(wire)}} -> c[{{wire_to_idx[wire]}}];")

    return "\\n".join(lines)

results = {{}}
for name, tape in tapes.items():
    try:
        qasm = tape_to_qasm(tape)
        results[name] = {{'qasm': qasm, 'ok': True, 'calls': qnode_call_counts.get(name, 0)}}
    except Exception as e:
        results[name] = {{'qasm': None, 'ok': False, 'error': str(e)}}

print('__UQI_JSON__:' + json.dumps(results))
"""

        data = self._run_subprocess(script, timeout=120)
        if not data:
            print(f"  [Extractor] PennyLane subprocess 실패")
            return

        if '__error__' in data:
            print(f"  [Extractor] 실행 오류: {data['__error__']}")
            return

        new_circuits = {}
        for name, info in data.items():
            if info.get('ok') and info.get('qasm'):
                key = f"{prefix}{name}"
                new_circuits[key] = info['qasm']
                self.circuits[key] = info['qasm']
                self.qnode_call_counts[key] = info.get('calls', 0)

        if not new_circuits:
            print(f"  [Extractor] 추출된 회로 없음")
            return

        print(f"  [Extractor] QNode {len(new_circuits)}개 발견: {', '.join(new_circuits.keys())}")
        print(f"  [Extractor] 추출 완료: {len(new_circuits)}개 tape")
        for key, count in self.qnode_call_counts.items():
            if key in new_circuits:
                print(f"    {key}: {count}회 호출")

    # ─────────────────────────────────────────
    # Qrisp 추출 (subprocess 격리)
    # ─────────────────────────────────────────

    def _extract_qrisp_circuits(self, prefix: str = ""):
        print(f"  [Extractor] Qrisp 회로 추출 시작")

        alg_file = self.algorithm_file

        script = f"""
import sys, json, os
sys.path.insert(0, '{os.path.dirname(os.path.abspath(alg_file))}')

import matplotlib
matplotlib.use('Agg')

from qrisp import QuantumSession, QuantumVariable

captured_sessions = []
measured_sessions = []
measurement_count = [0]

original_qs_init = QuantumSession.__init__
original_get_measurement = QuantumVariable.get_measurement

def tracking_qs_init(self_qs, *args, **kwargs):
    original_qs_init(self_qs, *args, **kwargs)
    captured_sessions.append(self_qs)

def tracking_get_measurement(self_qv, *args, **kwargs):
    measurement_count[0] += 1
    try:
        qs = self_qv.qs
        if (qs not in measured_sessions
                and hasattr(qs, 'qubits')
                and len(qs.qubits) > 0):
            measured_sessions.append(qs)
    except Exception:
        pass
    return {{}}

QuantumSession.__init__ = tracking_qs_init
QuantumVariable.get_measurement = tracking_get_measurement

try:
    with open(r'{alg_file}', 'r') as f:
        code = f.read()
    exec(code, {{'__name__': '__main__'}})
except Exception as e:
    pass
finally:
    QuantumSession.__init__ = original_qs_init
    QuantumVariable.get_measurement = original_get_measurement

valid_sessions = [
    qs for qs in captured_sessions
    if hasattr(qs, 'qubits') and len(qs.qubits) > 0
]
target_sessions = measured_sessions if measured_sessions else valid_sessions

results = {{}}
for idx, qs in enumerate(target_sessions):
    name = f"qrisp_circuit_{{idx}}" if len(target_sessions) > 1 else "qrisp_circuit"
    try:
        qasm = qs.to_qasm2()
        # gphase 제거
        qasm = "\\n".join(
            line for line in qasm.splitlines()
            if not line.strip().startswith("gphase")
        )
        results[name] = {{'qasm': qasm, 'ok': True, 'num_qubits': len(qs.qubits)}}
    except Exception as e1:
        try:
            qasm3 = qs.to_qasm3()
            results[name] = {{'qasm': qasm3, 'ok': True, 'num_qubits': len(qs.qubits), 'qasm3': True}}
        except Exception as e2:
            results[name] = {{'qasm': None, 'ok': False, 'error': str(e2)}}

results['__measurement_count__'] = measurement_count[0]
print('__UQI_JSON__:' + json.dumps(results))
"""

        data = self._run_subprocess(script, timeout=120)
        if not data:
            print(f"  [Extractor] Qrisp subprocess 실패")
            return

        measurement_count = data.pop('__measurement_count__', 0)

        new_circuits = {}
        for name, info in data.items():
            if info.get('ok') and info.get('qasm'):
                key = f"{prefix}{name}"
                new_circuits[key] = info['qasm']
                self.circuits[key] = info['qasm']

        if not new_circuits:
            print(f"  [Extractor] 유효한 QuantumSession 없음")
            return

        print(f"  [Extractor] 추출 완료: {len(new_circuits)}개 세션")
        for name, info in data.items():
            if info.get('ok'):
                key = f"{prefix}{name}"
                print(f"    {key}: 큐비트 수 {info.get('num_qubits', '?')}")
        print(f"    측정 호출: {measurement_count}회")

    # ─────────────────────────────────────────
    # CUDAQ 추출 (subprocess 격리)
    # ─────────────────────────────────────────

    def _extract_cudaq_circuits(self, prefix: str = ""):
        print(f"  [Extractor] CUDAQ 커널 추출 시작 (subprocess 격리)")

        alg_file = self.algorithm_file

        script = f"""
import sys, json
sys.path.insert(0, '{os.path.dirname(os.path.abspath(alg_file))}')
try:
    import cudaq
    cudaq.set_target("qpp-cpu")

    captured = {{}}
    original_sample  = cudaq.sample
    original_observe = cudaq.observe

    def patched_sample(kernel, *args, **kwargs):
        name = getattr(kernel, 'name', f'kernel_{{len(captured)}}')
        captured[name] = {{'kernel': kernel, 'args': args, 'type': 'sample'}}
        return original_sample(kernel, *args, **kwargs)

    def patched_observe(kernel, *args, **kwargs):
        name = getattr(kernel, 'name', f'kernel_{{len(captured)}}')
        hamiltonian = args[0] if args else None
        kernel_args = args[1:] if len(args) > 1 else ()
        captured[name] = {{'kernel': kernel, 'args': kernel_args, 'type': 'observe'}}
        return original_observe(kernel, *args, **kwargs)

    cudaq.sample  = patched_sample
    cudaq.observe = patched_observe

    import importlib.util
    spec = importlib.util.spec_from_file_location("__main__", r"{alg_file}")
    mod  = importlib.util.module_from_spec(spec)
    mod.__name__ = "__main__"
    spec.loader.exec_module(mod)

    def _draw_to_qasm(draw_str, n_qubits_hint=None):
        # cudaq.draw() ASCII art (multi-page) → OpenQASM 2.0
        import re as _re

        BOX_L  = '\u2524'   # ┤
        BOX_R  = '\u251c'   # ├
        CTRL   = '\u25cf'   # ●
        CONT   = '\u00bb'   # » (line continuation marker)

        # Step 1: strip trailing » from all lines
        all_lines = [l.rstrip(CONT).rstrip() for l in draw_str.splitlines()]

        # Step 2: split into page blocks by ### separators
        blocks = []
        cur = []
        for line in all_lines:
            if _re.match(r'^[#]{{5,}}', line.strip()):
                if cur:
                    blocks.append(cur)
                cur = []
            else:
                cur.append(line)
        if cur:
            blocks.append(cur)

        if not blocks:
            return None

        # Step 3: find qubit row offsets from first block (has "qN :" labels)
        first = blocks[0]
        qubit_row = {{}}   # q_idx -> row index within first block
        for ri, line in enumerate(first):
            mm = _re.match(r'^\s*q(\d+)\s*:\s*(.*)', line)
            if mm:
                qubit_row[int(mm.group(1))] = ri
        if not qubit_row:
            return None

        n_q = max(qubit_row.keys()) + 1
        if n_qubits_hint and n_qubits_hint > n_q:
            n_q = n_qubits_hint

        # Step 4: build per-qubit wire by concatenating across pages
        # Continuation pages have the same row structure but offset by +1
        # (one extra leading line before the first qubit row)
        qubit_wires = {{}}
        for qi, ri in qubit_row.items():
            mm = _re.match(r'^\s*q\d+\s*:\s*(.*)', first[ri])
            qubit_wires[qi] = mm.group(1) if mm else ''
        for block in blocks[1:]:
            for qi, ri in qubit_row.items():
                cont_ri = ri + 1
                if cont_ri < len(block):
                    qubit_wires[qi] += block[cont_ri]

        # Step 5: parse each qubit wire → QASM operations
        # gate name map (cudaq draw uses lowercase names)
        single_q = {{
            'h':'h','x':'x','y':'y','z':'z','s':'s','t':'t',
            'sdg':'sdg','tdg':'tdg','rx':'rx','ry':'ry','rz':'rz',
            'r1':'p','p':'p','u1':'u1',
            'H':'h','X':'x','Y':'y','Z':'z','S':'s','T':'t',
        }}
        gate_lines = []
        meas_lines = []

        for qi in sorted(qubit_wires.keys()):
            wire = qubit_wires[qi]
            pos = 0
            while pos < len(wire):
                box_p  = wire.find(BOX_L, pos)
                ctrl_p = wire.find(CTRL, pos)
                meas_p = -1
                for mp in range(pos, len(wire)):
                    if wire[mp] == 'M':
                        meas_p = mp
                        break
                cands = [(p, t) for p, t in
                         [(box_p,'box'),(ctrl_p,'ctrl'),(meas_p,'meas')] if p >= 0]
                if not cands:
                    break
                nxt, ev = min(cands, key=lambda x: x[0])

                if ev == 'box':
                    end = wire.find(BOX_R, nxt + 1)
                    if end < 0:
                        pos = nxt + 1
                        continue
                    gs = wire[nxt+1:end].strip()
                    pos = end + 1
                    # Skip CNOT target x (no params, lowercase)
                    if gs.lower() == 'x':
                        continue
                    # Skip measure boxes (handled as 'M' or fallback)
                    if gs.lower() in ('mz', 'mx', 'my', 'm'):
                        meas_lines.append('measure q[%d] -> c[%d];' % (qi, qi))
                        continue
                    pmm = _re.match(r'([A-Za-z]\w*)\((.+?)\)', gs)
                    if pmm:
                        gname = pmm.group(1)
                        ps    = pmm.group(2).split('...')[0].strip().rstrip(',')
                        qg    = single_q.get(gname, gname.lower())
                        try:
                            angle = float(ps)
                            gate_lines.append('%s(%.10g) q[%d];' % (qg, angle, qi))
                        except Exception:
                            gate_lines.append('%s(0) q[%d];' % (qg, qi))
                    else:
                        qg = single_q.get(gs, None)
                        if qg and qg in ('h','x','y','z','s','t','sdg','tdg'):
                            gate_lines.append('%s q[%d];' % (qg, qi))

                elif ev == 'ctrl':
                    target = qi + 1
                    for tq in [qi + 1, qi - 1]:
                        if tq in qubit_wires and 0 <= tq < n_q:
                            tw = qubit_wires[tq]
                            s2 = max(0, nxt - 3)
                            e2 = min(len(tw), nxt + 10)
                            if _re.search(BOX_L + '[ ]*[Xx][ ]*' + BOX_R, tw[s2:e2]):
                                target = tq
                                break
                    if 0 <= target < n_q:
                        gate_lines.append('cx q[%d], q[%d];' % (qi, target))
                    pos = nxt + 1

                else:   # 'M' measurement marker
                    meas_lines.append('measure q[%d] -> c[%d];' % (qi, qi))
                    pos = nxt + 1

        if not meas_lines:
            meas_lines = ['measure q[%d] -> c[%d];' % (i, i) for i in range(n_q)]
        header = [
            "OPENQASM 2.0;",
            'include "qelib1.inc";',
            "qreg q[%d];" % n_q,
            "creg c[%d];" % n_q,
        ]
        return '\\n'.join(header + gate_lines + meas_lines)

    def _qir_to_qasm(qir_ll, n_qubits):
        # QIR LLVM IR to OpenQASM 2.0 (CUDAQ gate pattern parsing)
        import re as _re
        header = [
            "OPENQASM 2.0;",
            'include "qelib1.inc";',
            "qreg q[%d];" % n_qubits,
            "creg c[%d];" % n_qubits,
        ]
        one_q   = {{'h':'h','x':'x','y':'y','z':'z','s':'s','t':'t','sdg':'sdg','tdg':'tdg'}}
        two_q   = {{'cnot':'cx','cz':'cz','swap':'swap'}}
        param_q = {{'rx':'rx','ry':'ry','rz':'rz','r1':'p','u1':'u1'}}
        def q_idx(ref):
            mm = _re.search(r'inttoptr\s*\(\s*i64\s+(\d+)', ref)
            if mm:
                return int(mm.group(1))
            mm = _re.search(r'i64\s+(\d+)\s+to\s+%Qubit', ref)
            return int(mm.group(1)) if mm else 0
        gate_lines    = []
        measure_lines = []
        for line in qir_ll.splitlines():
            line = line.strip()
            mm = _re.match(
                r'(?:tail\s+)?call\s+void\s+@__quantum__qis__(\w+?)(?:__body)?\s*\((.+)\)',
                line
            )
            if not mm:
                continue
            gate   = mm.group(1).lower()
            args_s = mm.group(2)
            q_refs = _re.findall(r'%Qubit\*[^,)]*', args_s)
            if gate in one_q and q_refs:
                gate_lines.append('%s q[%d];' % (one_q[gate], q_idx(q_refs[0])))
            elif gate in two_q and len(q_refs) >= 2:
                gate_lines.append('%s q[%d], q[%d];' % (
                    two_q[gate], q_idx(q_refs[0]), q_idx(q_refs[1])))
            elif gate in param_q and q_refs:
                pm = _re.search(r'double\s+(-?[\d.e+\-]+)', args_s)
                if pm:
                    gate_lines.append('%s(%s) q[%d];' % (
                        param_q[gate], pm.group(1), q_idx(q_refs[0])))
            elif gate in ('mz', 'mx', 'my') and q_refs:
                q = q_idx(q_refs[0])
                if q < n_qubits:
                    measure_lines.append('measure q[%d] -> c[%d];' % (q, q))
        if measure_lines:
            gate_lines.extend(measure_lines)
        else:
            gate_lines.extend('measure q[%d] -> c[%d];' % (i, i) for i in range(n_qubits))
        return '\\n'.join(header + gate_lines)

    def _extract_n_qubits_from_qir(qir_ll):
        # QIR에서 큐비트 수 추출 (inttoptr literal 패턴)
        import re as _re
        mm = _re.search(r'__quantum__rt__qubit_allocate_array[^(]*\(\s*i64\s+(\d+)', qir_ll)
        if mm:
            return int(mm.group(1))
        indices = [int(x) for x in _re.findall(r'i64\s+(\d+)\s+to\s+%Qubit', qir_ll)]
        return max(indices) + 1 if indices else 1

    results = {{}}
    for name, info in captured.items():
        try:
            qasm = cudaq.translate(info['kernel'], *info['args'], format="openqasm2")
            results[name] = {{'qasm': qasm, 'ok': True}}
        except Exception as e_openqasm:
            # openqasm2 실패 → draw() 기반 재구성 (1순위)
            int_args = [a for a in info['args'] if isinstance(a, int)]
            n_qubits_hint = max(int_args) if int_args else None
            try:
                draw_str = cudaq.draw(info['kernel'], *info['args'])
                qasm = _draw_to_qasm(draw_str, n_qubits_hint)
                if qasm:
                    results[name] = {{'qasm': qasm, 'ok': True, 'from_draw': True,
                                      'note': 'openqasm2 미지원 → draw() 기반 재구성'}}
                else:
                    raise ValueError("draw() parsing 실패")
            except Exception as e_draw:
                # draw 실패 → QIR 폴백 (2순위)
                try:
                    qir_ll = cudaq.translate(info['kernel'], *info['args'], format="qir")
                    if isinstance(qir_ll, bytes):
                        qir_ll = qir_ll.decode('utf-8')
                    n_qubits = n_qubits_hint or _extract_n_qubits_from_qir(qir_ll)
                    qasm = _qir_to_qasm(qir_ll, n_qubits)
                    results[name] = {{'qasm': qasm, 'ok': True, 'from_qir': True,
                                      'note': 'openqasm2 미지원 → QIR 변환 (동적 qubit 할당)'}}
                except Exception as e_qir:
                    results[name] = {{'qasm': None, 'ok': False,
                                      'error': 'openqasm2: %s / draw: %s / qir: %s' % (
                                          str(e_openqasm), str(e_draw), str(e_qir))}}

    print('__UQI_JSON__:' + json.dumps(results))

except Exception as e:
    print('__UQI_JSON__:' + json.dumps({{'__error__': str(e)}}))
"""

        data = self._run_subprocess(script, timeout=120)
        if not data:
            print(f"  [Extractor] CUDAQ subprocess 실패")
            return

        if '__error__' in data:
            print(f"  [Extractor] 실행 오류: {data['__error__']}")
            return

        new_circuits = {}
        for name, info in data.items():
            if info.get('ok') and info.get('qasm'):
                key = f"{prefix}{name}"
                new_circuits[key] = info['qasm']
                self.circuits[key] = info['qasm']
                if info.get('from_qir'):
                    print(f"  [Extractor] CUDAQ QIR→QASM 변환 ({name}): {info.get('note', '')}")
            elif not info.get('ok') and info.get('error'):
                print(f"  [Extractor] CUDAQ 커널 변환 실패 ({name}): {info['error']}")

        if not new_circuits:
            print(f"  [Extractor] CUDAQ 추출 실패: QASM 없음")
            return

        print(f"  [Extractor] 추출 완료: {len(new_circuits)}개 커널")
        print(f"    커널 목록: {', '.join(new_circuits.keys())}")

    # ─────────────────────────────────────────
    # Qiskit 추출 (subprocess 격리)
    # ─────────────────────────────────────────

    def _extract_qiskit_circuits(self, prefix: str = ""):
        print(f"  [Extractor] Qiskit 회로 추출 시작 (subprocess 격리)")

        alg_file = self.algorithm_file
        alg_dir = os.path.dirname(os.path.abspath(alg_file))

        script = f"""
import sys, json, os, importlib, inspect
sys.path.insert(0, '{alg_dir}')

import matplotlib
matplotlib.use('Agg')

try:
    from qiskit import QuantumCircuit
    from qiskit.qasm2 import dumps as qasm2_dumps
except ImportError as e:
    print('__UQI_JSON__:' + json.dumps({{'__error__': str(e)}}))
    sys.exit(0)

captured = {{}}
run_count = [0]

targets = [
    ("qiskit.primitives",     "StatevectorSampler",   "run"),
    ("qiskit.primitives",     "Sampler",              "run"),
    ("qiskit.primitives",     "StatevectorEstimator", "run"),
    ("qiskit.primitives",     "Estimator",            "run"),
    ("qiskit_aer.primitives", "SamplerV2",            "run"),
    ("qiskit_aer.primitives", "Sampler",              "run"),
    ("qiskit_aer.primitives", "EstimatorV2",          "run"),
    ("qiskit_aer",            "AerSimulator",         "run"),
    ("qiskit_ibm_runtime",    "SamplerV2",            "run"),
    ("qiskit_ibm_runtime",    "Sampler",              "run"),
    ("qiskit_ibm_runtime",    "EstimatorV2",          "run"),
    ("qiskit.providers",      "BackendV2",            "run"),
    ("qiskit.providers",      "BackendV1",            "run"),
]

def _extract_qcs(circuits):
    result = []
    if isinstance(circuits, QuantumCircuit):
        result = [(circuits, None)]
    elif hasattr(circuits, "__iter__"):
        for item in circuits:
            if isinstance(item, tuple) and len(item) > 0:
                if isinstance(item[0], QuantumCircuit):
                    params = None
                    if len(item) == 2 and not hasattr(item[1], 'num_qubits'):
                        params = item[1]
                    elif len(item) >= 3:
                        params = item[2]
                    result.append((item[0], params))
            elif isinstance(item, QuantumCircuit):
                result.append((item, None))
    if not result and hasattr(circuits, 'circuits'):
        result = [(qc, None) for qc in circuits.circuits]
    return result

def _qc_to_qasm(qc):
    cloned = qc.copy()
    if not cloned.cregs:
        cloned.measure_all()
    qasm = qasm2_dumps(cloned)
    return "\\n".join(
        line for line in qasm.splitlines()
        if not line.strip().startswith("gphase")
    ), cloned.num_qubits, len(cloned.data)

def wrap_run(original_func):
    def patched_run(self_obj, circuits, *args, **kwargs):
        run_count[0] += 1
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

        for qc, params in _extract_qcs(circuits):
            cloned = qc.copy()
            base_name = getattr(qc, 'name', 'qc')
            if base_name in ("circuit-0", "circuit"):
                base_name = "qc"
            name = f"{{caller_context}}_{{base_name}}_{{len(captured)}}"
            if params is not None and cloned.parameters:
                try:
                    import numpy as np
                    param_dict = dict(zip(cloned.parameters, np.asarray(params).flatten()))
                    cloned = cloned.assign_parameters(param_dict)
                except Exception:
                    pass
            try:
                qasm, nq, ng = _qc_to_qasm(cloned)
                captured[name] = {{'qasm': qasm, 'ok': True, 'num_qubits': nq, 'num_gates': ng}}
            except Exception as e:
                captured[name] = {{'qasm': None, 'ok': False, 'error': str(e)}}

        return original_func(self_obj, circuits, *args, **kwargs)
    return patched_run

original_methods = []
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
    with open(r'{alg_file}', 'r') as f:
        code = f.read()
    exec(code, {{'__name__': '__main__'}})
except Exception:
    pass
finally:
    for cls, method_name, orig in original_methods:
        setattr(cls, method_name, orig)

# fallback: 전역 변수에서 QuantumCircuit 스캔
if not captured:
    try:
        scan_globals = {{'__name__': '__main__'}}
        with open(r'{alg_file}', 'r') as f:
            code = f.read()
        exec(code, scan_globals)
        for var_name, var_val in scan_globals.items():
            if isinstance(var_val, QuantumCircuit) and var_val.num_qubits > 0:
                try:
                    qasm, nq, ng = _qc_to_qasm(var_val)
                    captured[var_name] = {{'qasm': qasm, 'ok': True, 'num_qubits': nq, 'num_gates': ng}}
                except Exception as e:
                    captured[var_name] = {{'qasm': None, 'ok': False, 'error': str(e)}}
    except Exception:
        pass

captured['__run_count__'] = run_count[0]
print('__UQI_JSON__:' + json.dumps(captured))
"""

        data = self._run_subprocess(script, timeout=120)
        if not data:
            print(f"  [Extractor] Qiskit subprocess 실패")
            return

        if '__error__' in data:
            print(f"  [Extractor] 실행 오류: {data['__error__']}")
            return

        run_count = data.pop('__run_count__', 0)

        new_circuits = {}
        for name, info in data.items():
            if info.get('ok') and info.get('qasm'):
                key = f"{prefix}{name}"
                new_circuits[key] = info['qasm']
                self.circuits[key] = info['qasm']

        if not new_circuits:
            print(f"  [Extractor] 추출된 회로 없음")
            return

        print(f"  [Extractor] 추출 완료: {len(new_circuits)}개 회로")
        print(f"    회로 목록: {', '.join(new_circuits.keys())}")
        print(f"    Sampler.run() 호출: {run_count}회")

    # ─────────────────────────────────────────
    # Perceval 추출 (subprocess 격리)
    # ─────────────────────────────────────────

    def _extract_perceval_circuits(self, prefix: str = ""):
        print(f"  [Extractor] Perceval 회로 추출 시작 (subprocess 격리)")

        alg_file = self.algorithm_file
        alg_dir = os.path.dirname(os.path.abspath(alg_file))

        script = f"""
import sys, json, re
sys.path.insert(0, '{alg_dir}')

import matplotlib
matplotlib.use('Agg')

try:
    import perceval as pcvl
    from perceval.algorithm import Sampler as PcvlSampler
except ImportError as e:
    print('__UQI_JSON__:' + json.dumps({{'__error__': str(e)}}))
    sys.exit(0)

try:
    from qiskit import QuantumCircuit
    from qiskit.qasm2 import dumps as qasm2_dumps
except ImportError as e:
    print('__UQI_JSON__:' + json.dumps({{'__error__': f'Qiskit 필요: {{e}}'}}))
    sys.exit(0)

captured = {{}}
original_processor        = pcvl.Processor
original_remote_processor = pcvl.RemoteProcessor

class CapturingProcessor:
    def __init__(self_p, backend_name_or_modes, *args, **kwargs):
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
        name = f"perceval_circuit_{{len(captured)}}"
        captured[name] = (self_p._circuit, self_p._input_state)
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
        self_p._inner       = original_processor("SLOS")
        self_p._circuit     = None
        self_p._input_state = None

pcvl.Processor       = CapturingProcessor
pcvl.RemoteProcessor = CapturingRemoteProcessor

original_sample_count = PcvlSampler.sample_count
original_samples      = PcvlSampler.samples

def mock_sample_count(self_s, count, *args, **kwargs):
    return {{'results': {{}}}}

def mock_samples(self_s, count, *args, **kwargs):
    return {{'results': {{}}}}

PcvlSampler.sample_count = mock_sample_count
PcvlSampler.samples      = mock_samples

try:
    with open(r'{alg_file}', 'r') as f:
        code = f.read()
    code = re.sub(
        r'(TARGET\\s*=\\s*)["\\\'](?!local)[^"\\\']+["\\\']',
        r'\\g<1>"local"',
        code
    )
    exec(code, {{'__name__': '__main__'}})
except Exception:
    pass
finally:
    pcvl.Processor        = original_processor
    pcvl.RemoteProcessor  = original_remote_processor
    PcvlSampler.sample_count = original_sample_count
    PcvlSampler.samples      = original_samples

# 캡처된 Perceval 회로를 Qiskit 게이트로 매핑 후 QASM 변환
def perceval_circuit_to_qasm(circuit, circuit_name):
    if circuit is None:
        return None, 'circuit is None'
    try:
        m = circuit.m
        qc = QuantumCircuit(m, m)
        for _, component in circuit:
            comp_type = type(component).__name__
            if 'BS' in comp_type:
                if m >= 2:
                    qc.h(0)
                    qc.cx(0, 1)
            elif 'PS' in comp_type:
                qc.rz(0.5, 0)
            elif 'PERM' in comp_type:
                if m >= 2:
                    qc.swap(0, 1)
        qc.measure(list(range(m)), list(range(m)))
        qasm = qasm2_dumps(qc)
        return qasm, None
    except Exception as e:
        return None, str(e)

results = {{}}
for name, (circuit, input_state) in captured.items():
    qasm, err = perceval_circuit_to_qasm(circuit, name)
    if qasm:
        m = circuit.m if circuit else 0
        results[name] = {{'qasm': qasm, 'ok': True, 'num_modes': m}}
    else:
        results[name] = {{'qasm': None, 'ok': False, 'error': err}}

print('__UQI_JSON__:' + json.dumps(results))
"""

        data = self._run_subprocess(script, timeout=120)
        if not data:
            print(f"  [Extractor] Perceval subprocess 실패")
            return

        if '__error__' in data:
            print(f"  [Extractor] 실행 오류: {data['__error__']}")
            return

        new_circuits = {}
        for name, info in data.items():
            if info.get('ok') and info.get('qasm'):
                key = f"{prefix}{name}"
                new_circuits[key] = info['qasm']
                self.circuits[key] = info['qasm']
            elif not info.get('ok') and info.get('error'):
                print(f"  [Extractor] Perceval 변환 실패 ({name}): {info['error']}")

        if not new_circuits:
            print(f"  [Extractor] 추출된 회로 없음")
            return

        print(f"  [Extractor] 추출 완료: {len(new_circuits)}개 회로")
        print(f"    회로 목록: {', '.join(new_circuits.keys())}")

    # ─────────────────────────────────────────
    # Utility
    # ─────────────────────────────────────────

    def get_total_call_count(self) -> int:
        return sum(self.qnode_call_counts.values())

    # 커스텀 게이트 정의 (qelib1.inc에 없는 게이트)
    _CUSTOM_GATE_DEFS = {
        'sxdg':  'gate sxdg a { rx(-pi/2) a; }',
        'iswap': 'gate iswap a, b { s a; s b; h a; cx a, b; cx b, a; h b; }',
        'rzz':   'gate rzz(theta) a, b { cx a, b; rz(theta) b; cx a, b; }',
        'rxx':   'gate rxx(theta) a, b { h a; h b; cx a, b; rz(theta) b; cx a, b; h b; h a; }',
        'ryy':   'gate ryy(theta) a, b { rx(pi/2) a; rx(pi/2) b; cx a, b; rz(theta) b; cx a, b; rx(-pi/2) a; rx(-pi/2) b; }',
        'ecr':   'gate ecr a, b { h b; cx a, b; rz(pi/4) b; cx a, b; h b; x a; h b; cx a, b; rz(-pi/4) b; cx a, b; h b; }',
    }
    _CUSTOM_GATE_ORDER = ['sxdg', 'iswap', 'rzz', 'rxx', 'ryy', 'ecr']

    def tape_to_openqasm(self, tape) -> str:
        """tape → OpenQASM 2.0 (게이트별 직접 변환, 행렬 계산 없음)"""
        try:
            expanded = tape.expand(
                depth=self.TAPE_EXPAND_DEPTH,
                stop_at=lambda obj: not hasattr(obj, 'decomposition')
            )
        except Exception:
            expanded = tape

        num_wires = expanded.num_wires
        wires_list = list(expanded.wires)
        wire_to_idx = {w: i for i, w in enumerate(wires_list)}

        header = [
            "OPENQASM 2.0;",
            'include "qelib1.inc";',
            f"qreg q[{num_wires}];",
            f"creg c[{num_wires}];",
        ]

        def w(wire):
            return f"q[{wire_to_idx[wire]}]"

        # qelib1.inc에 포함된 단순 게이트 매핑
        single_map = {
            "Hadamard": "h", "PauliX": "x", "PauliY": "y", "PauliZ": "z",
            "S": "s", "T": "t", "SX": "sx", "Adjoint(S)": "sdg",
            "Adjoint(T)": "tdg", "CNOT": "cx", "CZ": "cz", "SWAP": "swap",
            "CY": "cy", "CH": "ch",
        }

        used_custom = set()
        gate_lines = []

        for op in expanded.operations:
            name = op.name
            wires = op.wires
            params = op.parameters
            if name in single_map:
                gate = single_map[name]
                args = ", ".join(w(wire) for wire in wires)
                gate_lines.append(f"{gate} {args};")
            elif name == "RX":
                gate_lines.append(f"rx({float(params[0])}) {w(wires[0])};")
            elif name == "RY":
                gate_lines.append(f"ry({float(params[0])}) {w(wires[0])};")
            elif name == "RZ":
                gate_lines.append(f"rz({float(params[0])}) {w(wires[0])};")
            elif name == "PhaseShift":
                gate_lines.append(f"p({float(params[0])}) {w(wires[0])};")
            elif name == "U1":
                gate_lines.append(f"u1({float(params[0])}) {w(wires[0])};")
            elif name == "U2":
                gate_lines.append(f"u2({float(params[0])},{float(params[1])}) {w(wires[0])};")
            elif name == "U3":
                gate_lines.append(f"u3({float(params[0])},{float(params[1])},{float(params[2])}) {w(wires[0])};")
            elif name in ("Toffoli", "CCX"):
                gate_lines.append(f"ccx {w(wires[0])}, {w(wires[1])}, {w(wires[2])};")
            elif name in ("MultiControlledX", "ctrl"):
                ctrl_args = ", ".join(w(wire) for wire in wires[:-1])
                tgt_arg = w(wires[-1])
                if len(wires) == 3:
                    gate_lines.append(f"ccx {ctrl_args}, {tgt_arg};")
                elif len(wires) == 2:
                    gate_lines.append(f"cx {ctrl_args}, {tgt_arg};")
            elif name == "CRX":
                gate_lines.append(f"crx({float(params[0])}) {w(wires[0])}, {w(wires[1])};")
            elif name == "CRY":
                gate_lines.append(f"cry({float(params[0])}) {w(wires[0])}, {w(wires[1])};")
            elif name == "CRZ":
                gate_lines.append(f"crz({float(params[0])}) {w(wires[0])}, {w(wires[1])};")
            elif name == "Identity":
                gate_lines.append(f"id {w(wires[0])};")
            elif name in ("Adjoint(SX)", "SXdg"):
                used_custom.add('sxdg')
                gate_lines.append(f"sxdg {w(wires[0])};")
            elif name == "ISWAP":
                used_custom.add('iswap')
                gate_lines.append(f"iswap {w(wires[0])}, {w(wires[1])};")
            elif name == "RZZ":
                used_custom.add('rzz')
                gate_lines.append(f"rzz({float(params[0])}) {w(wires[0])}, {w(wires[1])};")
            elif name == "RXX":
                used_custom.add('rxx')
                gate_lines.append(f"rxx({float(params[0])}) {w(wires[0])}, {w(wires[1])};")
            elif name == "RYY":
                used_custom.add('ryy')
                gate_lines.append(f"ryy({float(params[0])}) {w(wires[0])}, {w(wires[1])};")
            elif name == "ECR":
                used_custom.add('ecr')
                gate_lines.append(f"ecr {w(wires[0])}, {w(wires[1])};")
            elif name == "GlobalPhase":
                pass  # 전역 위상 무시 (측정 결과에 영향 없음)
            else:
                print(f"  [QASM] 알 수 없는 게이트 스킵: {name}")
                gate_lines.append(f"// unknown gate: {name}")

        # 커스텀 게이트 정의를 헤더 직후 삽입 (의존 관계 순서 유지)
        custom_defs = [self._CUSTOM_GATE_DEFS[cg]
                       for cg in self._CUSTOM_GATE_ORDER if cg in used_custom]
        lines = header + custom_defs + gate_lines

        for wire in wires_list:
            lines.append(f"measure {w(wire)} -> c[{wire_to_idx[wire]}];")

        return "\n".join(lines)

    def print_tape_info(self, name: str):
        # circuits에서 QASM 기반으로 출력
        qasm = self.circuits.get(name)
        if qasm is None:
            print(f"  회로 없음: {name}")
            return
        try:
            from qiskit import QuantumCircuit
            qc = QuantumCircuit.from_qasm_str(qasm)
            print(f"  [{name}]")
            print(f"    큐비트 수:   {qc.num_qubits}")
            print(f"    게이트 수:   {len(qc.data)}")
            print(f"    회로 깊이:   {qc.depth()}")
        except Exception as e:
            print(f"  [{name}] QASM 파싱 오류: {e}")

    # ─────────────────────────────────────────
    # PennyLane Observable → SparsePauliOp 변환
    # ─────────────────────────────────────────

    def _pl_obs_to_sparse_pauli(self, tape) -> Optional[object]:
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
                if hasattr(obs, 'terms'):
                    coeffs, ops = obs.terms()
                    for coeff, op in zip(coeffs, ops):
                        pauli_str = self._op_to_pauli_str(op, num_wires, pauli_map)
                        if pauli_str:
                            terms.append((pauli_str,
                                float(coeff.real if hasattr(coeff, 'real') else coeff)))
                elif hasattr(obs, 'name') and obs.name in pauli_map:
                    wire = obs.wires[0]
                    pauli_str = ("I" * (num_wires - 1 - wire)
                                 + pauli_map[obs.name]
                                 + "I" * wire)
                    terms.append((pauli_str, 1.0))
                elif hasattr(obs, 'operands'):
                    pauli_str = self._op_to_pauli_str(obs, num_wires, pauli_map)
                    if pauli_str:
                        terms.append((pauli_str, 1.0))

            if not terms:
                return None

            return SparsePauliOp.from_list(terms)

        except Exception:
            return None

    def _op_to_pauli_str(self, op, num_wires: int, pauli_map: dict) -> Optional[str]:
        try:
            pauli_list = ["I"] * num_wires
            if hasattr(op, 'operands'):
                for sub_op in op.operands:
                    if hasattr(sub_op, 'name') and sub_op.name in pauli_map:
                        wire = sub_op.wires[0]
                        pauli_list[wire] = pauli_map[sub_op.name]
            elif hasattr(op, 'name') and op.name in pauli_map:
                wire = op.wires[0]
                pauli_list[wire] = pauli_map[op.name]
            else:
                return None
            return "".join(reversed(pauli_list))
        except Exception:
            return None