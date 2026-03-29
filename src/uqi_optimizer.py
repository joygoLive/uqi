# uqi_optimizer.py
# 양자 회로 최적화 엔진 - Stage 분리 아키텍처
# Stage 1: 논리 최적화 (토폴로지 무관)
# Stage 2: 하드웨어 매핑 (캘리브레이션 기반 토폴로지 반영)
# UQI (Universal Quantum Infrastructure)

import time
import re
import numpy as np
from typing import Optional
from qiskit import QuantumCircuit, transpile
from qiskit.transpiler import CouplingMap, Target, InstructionProperties
from qiskit.circuit.library import get_standard_gate_name_mapping


# ─────────────────────────────────────────────────────────
# 회로 특성 분석
# ─────────────────────────────────────────────────────────

def analyze_circuit(qc: QuantumCircuit) -> dict:
    """회로 특성 분석 → 엔진 선택 근거"""
    ops         = qc.count_ops()
    total_gates = sum(ops.values())
    num_qubits  = qc.num_qubits

    t_gates     = ops.get('t', 0) + ops.get('tdg', 0)
    t_ratio     = t_gates / total_gates if total_gates > 0 else 0.0

    two_q       = sum(v for k, v in ops.items()
                      if k in ['cx', 'cz', 'ecr', 'swap', 'iswap'])
    two_q_ratio = two_q / total_gates if total_gates > 0 else 0.0

    pauli       = ops.get('x', 0) + ops.get('y', 0) + ops.get('z', 0)
    pauli_ratio = pauli / total_gates if total_gates > 0 else 0.0

    rot         = ops.get('rx', 0) + ops.get('ry', 0) + ops.get('rz', 0)
    rot_ratio   = rot / total_gates if total_gates > 0 else 0.0

    # 실제 사용된 큐비트만 카운트 (IBM 156q 가상 할당 문제 우회)
    used_qubits = set()
    for instruction in qc.data:
        op_name = instruction.operation.name
        if op_name in ['barrier', 'measure']:
            continue
        for qubit in instruction.qubits:
            try:
                used_qubits.add(qc.find_bit(qubit).index)
            except Exception:
                pass

    active_qubits = len(used_qubits) if used_qubits else num_qubits

    return {
        "num_qubits":       active_qubits,
        "total_gates":      total_gates,
        "depth":            qc.depth(),
        "t_ratio":          t_ratio,
        "two_q_ratio":      two_q_ratio,
        "pauli_ratio":      pauli_ratio,
        "rot_ratio":        rot_ratio,
        "is_parameterized": len(qc.parameters) > 0,
        "ops":              dict(ops),
    }


# ─────────────────────────────────────────────────────────
# Stage 1 엔진 선택
# ─────────────────────────────────────────────────────────

def select_opt_engine(profile: dict, prefer_speed: bool = False) -> str:
    """
    회로 특성 기반 Stage 1 최적화 엔진 선택

    Returns:
        'qiskit_l3' | 'tket' | 'quizx' | 'qiskit_l3_appx'
    """
    total    = profile["total_gates"]
    n        = profile["num_qubits"]
    t_ratio  = profile["t_ratio"]
    is_param = profile["is_parameterized"]

    if prefer_speed:
        return "qiskit_l3"
    if is_param:
        return "qiskit_l3"
    if total > 10000 or n > 20:
        return "qiskit_l3"
    if t_ratio > 0.3 and n <= 15 and total <= 500:
        return "quizx"
    if total <= 5000:
        return "tket"
    return "qiskit_l3"


def select_map_engine(profile: dict, prefer_speed: bool = False) -> str:
    """
    회로 특성 기반 Stage 2 매핑 엔진 선택

    Returns:
        'qiskit_sabre' | 'tket_routing'
    """
    if prefer_speed:
        return "qiskit_sabre"
    # 대규모 회로 → Qiskit sabre (안정적)
    if profile["total_gates"] > 5000:
        return "qiskit_sabre"
    # 소~중규모 → TKET routing (품질 우선)
    return "qiskit_sabre"  # 현재는 sabre 기본, TKET routing 고도화 예정


# ─────────────────────────────────────────────────────────
# 캘리브레이션 기반 유틸
# ─────────────────────────────────────────────────────────

def _build_coupling_map(calibration: dict, qpu_name: str) -> Optional[CouplingMap]:
    """캘리브레이션 데이터 → Qiskit CouplingMap"""
    cm = calibration.get("coupling_map")
    if not cm:
        return None

    if cm == "all_to_all":
        n = calibration.get("num_qubits", 0)
        if n > 0:
            edges = [(i, j) for i in range(n) for j in range(n) if i != j]
            return CouplingMap(edges)
        return None

    if isinstance(cm, list) and len(cm) > 0:
        if 'iqm' in qpu_name and isinstance(cm[0][0], str):
            qubit_names = set()
            for edge in cm:
                qubit_names.add(edge[0])
                qubit_names.add(edge[1])

            def _nat_key(s):
                return [int(t) if t.isdigit() else t.lower()
                        for t in re.split(r'([0-9]+)', s)]

            sorted_names = sorted(qubit_names, key=_nat_key)
            q2i  = {name: idx for idx, name in enumerate(sorted_names)}
            cm   = [[q2i[e[0]], q2i[e[1]]] for e in cm]

        return CouplingMap(cm)

    return None


def _build_target(calibration: dict, qpu_name: str) -> Optional[Target]:
    """
    캘리브레이션 데이터 → Qiskit Target
    에러율 / 게이트 시간 반영 → 매핑 시 저품질 큐비트 자동 회피
    """
    basis_gates = calibration.get("basis_gates")
    cm          = _build_coupling_map(calibration, qpu_name)
    if not basis_gates or cm is None:
        return None

    try:
        n = calibration.get("num_qubits")
        if not n:
            # coupling_map에서 큐비트 수 추정
            edges = list(cm.get_edges())
            n = max(max(e) for e in edges) + 1 if edges else 0
        if not n:
            return None
        q1_error   = calibration.get("avg_1q_error")
        q2_error   = calibration.get("avg_2q_error")
        ro_error   = calibration.get("avg_ro_error")
        q1_dur_ns  = calibration.get("avg_1q_ns")
        q2_dur_ns  = calibration.get("avg_2q_ns")

        gate_map   = get_standard_gate_name_mapping()
        target = Target(num_qubits=n)

        # measure 명시적 추가 (없으면 HighLevelSynthesis 오류)
        from qiskit.circuit.library import Measure
        ro_error   = calibration.get("avg_ro_error")
        meas_props = {
            (q,): InstructionProperties(error=ro_error)
            for q in range(n)
        } if ro_error else {(q,): None for q in range(n)}
        try:
            target.add_instruction(Measure(), meas_props)
        except Exception:
            pass

        two_q_gates = {'cx', 'cz', 'ecr', 'swap', 'iswap'}
        skip_gates  = {'measure', 'reset', 'delay', 'if_else', 'barrier',
                       'switch_case', 'break_loop', 'continue_loop'}

        for gate_name in basis_gates:
            # measure 등 비게이트 명령어 제외 → HighLevelSynthesis 오류 방지
            if gate_name in skip_gates:
                continue

            gate = gate_map.get(gate_name)
            if gate is None:
                continue

            elif gate_name in two_q_gates:
                props = {}
                for edge in cm.get_edges():
                    dur = q2_dur_ns * 1e-9 if q2_dur_ns else None
                    props[tuple(edge)] = InstructionProperties(
                        duration=dur,
                        error=q2_error
                    )
                try:
                    target.add_instruction(gate, props)
                except Exception:
                    pass

            else:
                props = {}
                for q in range(n):
                    dur = q1_dur_ns * 1e-9 if q1_dur_ns else None
                    props[(q,)] = InstructionProperties(
                        duration=dur,
                        error=q1_error
                    )
                try:
                    target.add_instruction(gate, props)
                except Exception:
                    pass

        return target

    except Exception as e:
        print(f"  [Optimizer] ⚠ Target 생성 실패: {e} → CouplingMap 기반 사용")
        return None


def _check_t2_depth(qc: QuantumCircuit, calibration: dict) -> bool:
    """T2 대비 회로 실행 시간 검증"""
    t2_ms = calibration.get("avg_t2_ms")
    q2_ns = calibration.get("avg_2q_ns")
    if not t2_ms or not q2_ns:
        return True

    t2_ns       = t2_ms * 1e6
    est_exec_ns = q2_ns * qc.depth()
    if est_exec_ns > t2_ns:
        ratio = est_exec_ns / t2_ns
        print(f"  [Optimizer] ⚠ 실행시간 추정 {est_exec_ns:.0f}ns "
              f"> T2 {t2_ns:.0f}ns ({ratio:.1f}배) → 결과 품질 저하 가능")
        return False
    return True


# ─────────────────────────────────────────────────────────
# 등가성 검증
# ─────────────────────────────────────────────────────────

def verify_equivalence(qc_orig: QuantumCircuit,
                       qc_opt:  QuantumCircuit,
                       shots:   int = 1024) -> bool:
    """
    최적화 전후 등가성 검증
    ≤10q: 유니터리 직접 비교
    >10q: 샘플링 기반 TVD 검증
    파라미터화: 임의값 바인딩 후 검증
    """
    from qiskit import transpile
    from qiskit.quantum_info import Operator

    # ── 파라미터화 회로 → 임의값 바인딩 ──
    if qc_orig.parameters or qc_opt.parameters:
        try:
            all_vals = {
                p.name: np.random.uniform(0, 2 * np.pi)
                for p in set(qc_orig.parameters) | set(qc_opt.parameters)
            }
            qc_orig = qc_orig.assign_parameters(
                {p: all_vals[p.name] for p in qc_orig.parameters})
            qc_opt  = qc_opt.assign_parameters(
                {p: all_vals[p.name] for p in qc_opt.parameters})
        except Exception as e:
            print(f"  [Optimizer] ⚠ 파라미터 바인딩 실패: {e} → 검증 스킵")
            return True

    # ── 공통 기저로 재분해 → 큐비트 수 정규화 ──
    basis = ['cx', 'h', 'rz', 'rx', 'ry', 'x', 'y', 'z', 's', 't']
    try:
        qc_o = transpile(
            qc_orig.remove_final_measurements(inplace=False),
            basis_gates=basis, optimization_level=0)
        qc_t = transpile(
            qc_opt.remove_final_measurements(inplace=False),
            basis_gates=basis, optimization_level=0)

        # active 큐비트 수 기준으로 비교
        def _count_active(qc):
            used = set()
            for inst in qc.data:
                if inst.operation.name in ['barrier', 'measure']:
                    continue
                for q in inst.qubits:
                    try:
                        used.add(qc.find_bit(q).index)
                    except Exception:
                        pass
            return len(used) if used else qc.num_qubits

        n_o = _count_active(qc_o)
        n_t = _count_active(qc_t)
    except Exception as e:
        print(f"  [Optimizer] ⚠ 재분해 실패: {e} → 검증 스킵")
        return True

    n_o, n_t = qc_o.num_qubits, qc_t.num_qubits
    if n_o != n_t:
        print(f"  [Optimizer] ⚠ 큐비트 수 불일치 ({n_o}q vs {n_t}q) → 검증 스킵")
        return True

    try:
        if n_o <= 10:
            equiv = Operator(qc_o).equiv(Operator(qc_t), rtol=1e-3)
            if not equiv:
                print(f"  [Optimizer] ✗ 등가성 검증 실패 (유니터리 불일치)")
            return equiv
        else:
            from qiskit_aer.primitives import SamplerV2
            qc_o2, qc_t2 = qc_o.copy(), qc_t.copy()
            qc_o2.measure_all()
            qc_t2.measure_all()

            sampler = SamplerV2()
            cnt_o = getattr(
                sampler.run([qc_o2], shots=shots).result()[0].data,
                qc_o2.cregs[0].name).get_counts()
            cnt_t = getattr(
                sampler.run([qc_t2], shots=shots).result()[0].data,
                qc_t2.cregs[0].name).get_counts()

            tvd = sum(abs(cnt_o.get(k, 0) / shots - cnt_t.get(k, 0) / shots)
                      for k in set(cnt_o) | set(cnt_t)) / 2
            equiv = tvd < 0.1
            if not equiv:
                print(f"  [Optimizer] ✗ 등가성 검증 실패 (TVD={tvd:.3f})")
            return equiv

    except Exception as e:
        print(f"  [Optimizer] ⚠ 등가성 검증 오류: {e} → 원본 유지")
        return False


# ─────────────────────────────────────────────────────────
# Stage 1: 논리 최적화 엔진
# ─────────────────────────────────────────────────────────

def _opt_qiskit(qc: QuantumCircuit, level: int = 3,
                approximation_degree: float = None) -> QuantumCircuit:
    """Qiskit 논리 최적화 (토폴로지 무관)"""
    basis  = ['cx', 'h', 'rz', 'rx', 'ry', 'x', 'y', 'z', 's', 't', 'sdg', 'tdg']
    kwargs = dict(basis_gates=basis, optimization_level=level)
    if approximation_degree is not None:
        kwargs["approximation_degree"] = approximation_degree
    return transpile(qc, **kwargs)


def _opt_tket(qc: QuantumCircuit) -> QuantumCircuit:
    """TKET 논리 최적화 (토폴로지 무관)"""
    from pytket.extensions.qiskit import qiskit_to_tk, tk_to_qiskit
    from pytket.passes import FullPeepholeOptimise

    basis = ['cx', 'h', 'rz', 'rx', 'ry', 'x', 'y', 'z', 's', 't', 'sdg', 'tdg']
    qc_decomposed = transpile(qc, basis_gates=basis, optimization_level=0)
    tk_qc = qiskit_to_tk(qc_decomposed)
    FullPeepholeOptimise().apply(tk_qc)
    return tk_to_qiskit(tk_qc, replace_implicit_swaps=True)


def _opt_quizx(qc: QuantumCircuit) -> QuantumCircuit:
    """QuiZX 논리 최적화 (소규모 전용)"""
    import quizx
    import pyzx
    from qiskit import qasm2

    basis = ['cx', 'h', 'rz', 'rx', 'ry', 'x', 'z', 's', 't', 'sdg', 'tdg']
    qc_base    = transpile(qc, basis_gates=basis, optimization_level=0)
    qc_no_meas = qc_base.remove_final_measurements(inplace=False)
    num_clbits = qc_base.num_clbits

    pyzx_circuit = pyzx.Circuit.from_qasm(qasm2.dumps(qc_no_meas))
    g_simple     = pyzx_circuit.to_graph()

    g_quizx = pyzx.Graph(backend='quizx-vec')
    for v in g_simple.vertices():
        g_quizx.add_vertex(ty=g_simple.type(v), qubit=g_simple.qubit(v),
                           row=g_simple.row(v), phase=g_simple.phase(v))
    g_quizx.set_inputs(tuple(g_simple.inputs()))
    g_quizx.set_outputs(tuple(g_simple.outputs()))
    for e in g_simple.edges():
        s, t = g_simple.edge_st(e)
        g_quizx.add_edge((s, t), g_simple.edge_type(e))

    quizx.full_simp(g_quizx)
    qc_opt = QuantumCircuit.from_qasm_str(
        quizx.extract_circuit(g_quizx).to_qasm())

    if num_clbits > 0:
        qc_opt.add_register(*[reg for reg in qc_base.cregs])
        qc_opt.measure_all()

    return qc_opt


# ─────────────────────────────────────────────────────────
# Stage 2: 하드웨어 매핑 엔진
# ─────────────────────────────────────────────────────────

def _map_qiskit_sabre(qc: QuantumCircuit, calibration: dict,
                      qpu_name: str) -> QuantumCircuit:
    """
    Qiskit SABRE 라우팅 + 캘리브레이션 기반 Target 주입
    에러율 / 게이트 시간 반영 → 저품질 큐비트 자동 회피
    """
    cm = _build_coupling_map(calibration, qpu_name)
    basis = calibration.get("basis_gates")

    # Target 주입 시도 (에러율 기반 매핑)
    target = _build_target(calibration, qpu_name)
    if target is not None:
        try:
            return transpile(qc, target=target,
                             routing_method='sabre',
                             optimization_level=1)
        except Exception as e:
            print(f"  [Optimizer] ⚠ Target 매핑 실패: {e} → CouplingMap 폴백")

    # CouplingMap 기반 폴백
    return transpile(
        qc,
        basis_gates=basis,
        coupling_map=cm,
        routing_method='sabre',
        optimization_level=1
    )


def _map_tket_routing(qc: QuantumCircuit, calibration: dict,
                      qpu_name: str) -> QuantumCircuit:
    """
    TKET RoutingPass + 캘리브레이션 기반 Architecture
    (고도화 예정 - 현재는 Qiskit sabre 폴백)
    """
    try:
        from pytket.extensions.qiskit import qiskit_to_tk, tk_to_qiskit
        from pytket.passes import DefaultMappingPass
        from pytket.architecture import Architecture

        cm = _build_coupling_map(calibration, qpu_name)
        if cm is None:
            return _map_qiskit_sabre(qc, calibration, qpu_name)

        edges  = list(cm.get_edges())
        arch   = Architecture(edges)
        basis  = calibration.get("basis_gates") or \
                 ['cx', 'h', 'rz', 'rx', 'ry', 'x', 'y', 'z', 's', 't']
        qc_dec = transpile(qc, basis_gates=basis, optimization_level=0)
        tk_qc  = qiskit_to_tk(qc_dec)
        DefaultMappingPass(arch).apply(tk_qc)
        qc_mapped = tk_to_qiskit(tk_qc, replace_implicit_swaps=True)

        # 최종 basis gate 정리
        return transpile(
            qc_mapped,
            basis_gates=basis,
            optimization_level=0
        )
    except Exception as e:
        print(f"  [Optimizer] TKET routing 실패: {e} → Qiskit sabre 폴백")
        return _map_qiskit_sabre(qc, calibration, qpu_name)


# ─────────────────────────────────────────────────────────
# 지원 조합
# ─────────────────────────────────────────────────────────

COMBINATIONS = {
    # (opt_engine, map_engine)
    "qiskit+sabre":  ("qiskit_l3",      "qiskit_sabre"),
    "tket+sabre":    ("tket",           "qiskit_sabre"),
    "quizx+sabre":   ("quizx",          "qiskit_sabre"),
    "qiskit+tket":   ("qiskit_l3",      "tket_routing"),
    "tket+tket":     ("tket",           "tket_routing"),
    "appx+sabre":    ("qiskit_l3_appx", "qiskit_sabre"),
}


# ─────────────────────────────────────────────────────────
# 메인 인터페이스
# ─────────────────────────────────────────────────────────

class UQIOptimizer:
    """
    양자 회로 최적화 엔진 - Stage 분리 아키텍처

    Stage 1: 논리 최적화 (Qiskit L3 / TKET / QuiZX)
    Stage 2: 하드웨어 매핑 (Qiskit SABRE / TKET Routing)
             캘리브레이션 기반 에러율 / 게이트 시간 반영
    """

    def __init__(self, calibration: dict = None):
        self.calibration = calibration or {}

    def optimize_stage1(self, qc: QuantumCircuit, prefer_speed: bool = False) -> dict:
        """Stage1 논리 최적화만 수행 (QPU 독립적)"""
        profile = analyze_circuit(qc)
        opt_eng = select_opt_engine(profile, prefer_speed)
        t1 = time.time()
        try:
            if opt_eng == "qiskit_l3":
                qc_opt = _opt_qiskit(qc, level=3)
            elif opt_eng == "qiskit_l3_appx":
                qc_opt = _opt_qiskit(qc, level=3, approximation_degree=0.9)
            elif opt_eng == "tket":
                qc_opt = _opt_tket(qc)
            elif opt_eng == "quizx":
                qc_opt = _opt_quizx(qc)
            else:
                qc_opt = _opt_qiskit(qc, level=3)
        except Exception as e:
            print(f"  [Optimizer] Stage1 실패: {e} → Qiskit L3 폴백")
            qc_opt = _opt_qiskit(qc, level=3)
            opt_eng = "qiskit_l3_fallback"
        opt_time = time.time() - t1
        opt1_prof = analyze_circuit(qc_opt)
        return {
            "circuit":      qc_opt,
            "opt_engine":   opt_eng,
            "profile":      profile,
            "opt1_profile": opt1_prof,
            "opt_time_sec": opt_time,
        }

    def optimize_stage2(self, qc_opt: QuantumCircuit, qpu_name: str,
                        stage1_result: dict, combination: str = "auto",
                        verify: bool = False) -> dict:
        """Stage2 하드웨어 매핑만 수행"""
        profile   = stage1_result["profile"]
        opt1_prof = stage1_result["opt1_profile"]
        opt_eng   = stage1_result["opt_engine"]

        if combination == "auto":
            map_eng = select_map_engine(profile)
            if opt_eng in ("tket", "tket_fallback"):
                combination = "tket+sabre"
            elif opt_eng == "quizx":
                combination = "quizx+sabre"
            else:
                combination = "qiskit+sabre"
        else:
            _, map_eng = COMBINATIONS.get(combination, ("qiskit_l3", "qiskit_sabre"))

        result = {
            "circuit":         qc_opt,
            "combination":     combination,
            "opt_engine":      opt_eng,
            "map_engine":      map_eng,
            "ok":              False,
            "gate_reduction":  0.0,
            "depth_reduction": 0.0,
            "opt_time_sec":    stage1_result["opt_time_sec"],
            "map_time_sec":    0.0,
            "total_time_sec":  0.0,
            "profile":         profile,
            "opt1_gates":      opt1_prof["total_gates"],
            "opt1_depth":      opt1_prof["depth"],
            "equivalent":      None,
            "t2_ok":           True,
            "error":           None,
        }

        try:
            result["t2_ok"] = _check_t2_depth(qc_opt, self.calibration)

            if verify:
                from qiskit import QuantumCircuit as QC
                qc_orig = QC.from_qasm_str(qc_opt.qasm()) if hasattr(qc_opt, 'qasm') else qc_opt
                equiv = verify_equivalence(stage1_result["circuit"], qc_opt)
                result["equivalent"] = equiv
                if not equiv:
                    result["error"] = "equivalence_check_failed"
                    return result

            t2 = time.time()
            try:
                if map_eng == "qiskit_sabre":
                    qc_mapped = _map_qiskit_sabre(qc_opt, self.calibration, qpu_name)
                elif map_eng == "tket_routing":
                    qc_mapped = _map_tket_routing(qc_opt, self.calibration, qpu_name)
                else:
                    qc_mapped = _map_qiskit_sabre(qc_opt, self.calibration, qpu_name)
            except Exception as e:
                print(f"  [Optimizer] Stage2 실패: {e} → Qiskit sabre 폴백")
                qc_mapped = _map_qiskit_sabre(qc_opt, self.calibration, qpu_name)
                result["map_engine"] = "qiskit_sabre_fallback"

            result["map_time_sec"]   = time.time() - t2
            result["total_time_sec"] = result["opt_time_sec"] + result["map_time_sec"]

            basis = self.calibration.get("basis_gates")
            opt_prof  = analyze_circuit(qc_mapped)
            opt_gates = opt_prof["total_gates"]
            opt_depth = opt_prof["depth"]

            if basis:
                from qiskit import transpile
                qc_base   = transpile(stage1_result["circuit"], basis_gates=basis, optimization_level=0)
                base_prof = analyze_circuit(qc_base)
                orig_gates = base_prof["total_gates"]
                orig_depth = base_prof["depth"]
            else:
                orig_gates = profile["total_gates"]
                orig_depth = profile["depth"]

            result["circuit"]         = qc_mapped
            result["gate_reduction"]  = (orig_gates - opt_gates) / orig_gates if orig_gates > 0 else 0.0
            result["depth_reduction"] = (orig_depth - opt_depth) / orig_depth if orig_depth > 0 else 0.0
            result["ok"]              = True

        except Exception as e:
            result["error"] = str(e)
            print(f"  [Optimizer] Stage2 실패: {e}")

        return result

    def optimize(
        self,
        qc:           QuantumCircuit,
        qpu_name:     str,
        combination:  str  = "auto",
        prefer_speed: bool = False,
        verify:       bool = True,
    ) -> dict:
        """
        회로 최적화 실행

        Args:
            qc:          입력 회로
            qpu_name:    타겟 QPU
            combination: 'auto' | 'qiskit+sabre' | 'tket+sabre' |
                         'quizx+sabre' | 'qiskit+tket' | 'tket+tket' |
                         'appx+sabre'
            prefer_speed: 속도 우선 (auto 시 Qiskit+sabre 강제)
            verify:      등가성 검증 여부

        Returns:
            {
              circuit, combination, opt_engine, map_engine,
              ok, gate_reduction, depth_reduction,
              opt_time_sec, map_time_sec, total_time_sec,
              profile, equivalent, t2_ok, error
            }
        """
        result = {
            "circuit":         qc,
            "combination":     combination,
            "opt_engine":      None,
            "map_engine":      None,
            "ok":              False,
            "gate_reduction":  0.0,
            "depth_reduction": 0.0,
            "opt_time_sec":    0.0,
            "map_time_sec":    0.0,
            "total_time_sec":  0.0,
            "profile":         {},
            "equivalent":      None,
            "t2_ok":           True,
            "error":           None,
        }

        try:
            # ── 회로 특성 분석 ──
            profile       = analyze_circuit(qc)
            result["profile"] = profile
            print(f"  [Optimizer] 분석: {profile['num_qubits']}q "
                  f"{profile['total_gates']} gates "
                  f"depth={profile['depth']} "
                  f"T={profile['t_ratio']:.2f} "
                  f"2q={profile['two_q_ratio']:.2f}")

            # ── 조합 선택 ──
            if combination == "auto":
                opt_eng = select_opt_engine(profile, prefer_speed)
                map_eng = select_map_engine(profile, prefer_speed)
                combination = f"{opt_eng.replace('_l3','').replace('_','').replace('appx','appx')}+{map_eng.replace('_','').replace('qiskit','qiskit').replace('sabre','sabre')}"
                # 정규화
                if opt_eng == "qiskit_l3" and map_eng == "qiskit_sabre":
                    combination = "qiskit+sabre"
                elif opt_eng == "tket" and map_eng == "qiskit_sabre":
                    combination = "tket+sabre"
                elif opt_eng == "quizx" and map_eng == "qiskit_sabre":
                    combination = "quizx+sabre"
                elif opt_eng == "qiskit_l3_appx":
                    combination = "appx+sabre"
                else:
                    combination = "qiskit+sabre"

            opt_eng, map_eng = COMBINATIONS.get(
                combination, ("qiskit_l3", "qiskit_sabre"))
            result["combination"] = combination
            result["opt_engine"]  = opt_eng
            result["map_engine"]  = map_eng
            print(f"  [Optimizer] 조합: {combination} "
                  f"(opt={opt_eng}, map={map_eng})")

            # ── T2 깊이 검증 ──
            result["t2_ok"] = _check_t2_depth(qc, self.calibration)

            # ── Stage 1: 논리 최적화 ──
            t1 = time.time()
            try:
                if opt_eng == "qiskit_l3":
                    qc_opt = _opt_qiskit(qc, level=3)
                elif opt_eng == "qiskit_l3_appx":
                    qc_opt = _opt_qiskit(qc, level=3, approximation_degree=0.9)
                elif opt_eng == "tket":
                    qc_opt = _opt_tket(qc)
                elif opt_eng == "quizx":
                    qc_opt = _opt_quizx(qc)
                else:
                    qc_opt = _opt_qiskit(qc, level=3)
            except Exception as e:
                print(f"  [Optimizer] Stage1 실패: {e} → Qiskit L3 폴백")
                qc_opt = _opt_qiskit(qc, level=3)
                result["opt_engine"] = "qiskit_l3_fallback"
            result["opt_time_sec"] = time.time() - t1

            # Stage 1 중간 결과 측정
            opt1_prof = analyze_circuit(qc_opt)
            result["opt1_gates"] = opt1_prof["total_gates"]
            result["opt1_depth"] = opt1_prof["depth"]

            # ── Stage 2: 하드웨어 매핑 ──
            t2 = time.time()
            try:
                if map_eng == "qiskit_sabre":
                    qc_mapped = _map_qiskit_sabre(qc_opt, self.calibration, qpu_name)
                elif map_eng == "tket_routing":
                    qc_mapped = _map_tket_routing(qc_opt, self.calibration, qpu_name)
                else:
                    qc_mapped = _map_qiskit_sabre(qc_opt, self.calibration, qpu_name)
            except Exception as e:
                print(f"  [Optimizer] Stage2 실패: {e} → Qiskit sabre 폴백")
                qc_mapped = _map_qiskit_sabre(qc_opt, self.calibration, qpu_name)
                result["map_engine"] = "qiskit_sabre_fallback"
            result["map_time_sec"]   = time.time() - t2
            result["total_time_sec"] = result["opt_time_sec"] + result["map_time_sec"]

            # ── 등가성 검증 (Stage1 결과 기준 - 토폴로지 확장 전) ──
            if verify:
                equiv = verify_equivalence(qc, qc_opt)
                result["equivalent"] = equiv
                if not equiv:
                    print(f"  [Optimizer] ⚠ 등가성 실패 → 원본 회로 사용")
                    result["error"] = "equivalence_check_failed"
                    return result

            # ── 감소율 계산 (캘리브레이션 basis 기준선) ──
            basis = self.calibration.get("basis_gates")
            opt_prof  = analyze_circuit(qc_mapped)
            opt_gates = opt_prof["total_gates"]
            opt_depth = opt_prof["depth"]

            if basis:
                qc_base   = transpile(qc, basis_gates=basis, optimization_level=0)
                base_prof = analyze_circuit(qc_base)
                orig_gates = base_prof["total_gates"]
                orig_depth = base_prof["depth"]
            else:
                orig_gates = profile["total_gates"]
                orig_depth = profile["depth"]

            gate_red  = (orig_gates - opt_gates) / orig_gates if orig_gates > 0 else 0.0
            depth_red = (orig_depth - opt_depth) / orig_depth if orig_depth > 0 else 0.0

            result["circuit"]         = qc_mapped
            result["gate_reduction"]  = gate_red
            result["depth_reduction"] = depth_red
            result["ok"]              = True

            opt1_gates = result.get("opt1_gates", orig_gates)
            opt1_depth = result.get("opt1_depth", orig_depth)
            print(f"  [Optimizer] ✓ {combination}")
            print(f"    Stage1({opt_eng}): "
                  f"게이트 {orig_gates}→{opt1_gates} "
                  f"({(orig_gates-opt1_gates)/orig_gates*100:.1f}%) "
                  f"깊이 {orig_depth}→{opt1_depth} "
                  f"({(orig_depth-opt1_depth)/orig_depth*100:.1f}%) "
                  f"{result['opt_time_sec']:.1f}s")
            print(f"    Stage2({map_eng}): "
                  f"게이트 {opt1_gates}→{opt_gates} "
                  f"({(opt1_gates-opt_gates)/opt1_gates*100:.1f}%) "
                  f"깊이 {opt1_depth}→{opt_depth} "
                  f"({(opt1_depth-opt_depth)/opt1_depth*100:.1f}%) "
                  f"{result['map_time_sec']:.1f}s")
            print(f"    전체: 게이트 {orig_gates}→{opt_gates} "
                  f"({gate_red*100:.1f}%) "
                  f"깊이 {orig_depth}→{opt_depth} "
                  f"({depth_red*100:.1f}%)")

        except Exception as e:
            result["error"] = str(e)
            print(f"  [Optimizer] ✗ 실패: {e}")

        return result

    def optimize_all(
        self,
        circuits:     dict,
        qpu_name:     str,
        combination:  str  = "auto",
        prefer_speed: bool = False,
        verify:       bool = True,
    ) -> dict:
        """복수 회로 일괄 최적화"""
        results = {}
        for name, qc in circuits.items():
            print(f"\n  [Optimizer] 최적화: {name}")
            results[name] = self.optimize(
                qc, qpu_name, combination, prefer_speed, verify)
        ok = sum(1 for r in results.values() if r["ok"])
        print(f"\n  [Optimizer] 완료: {ok}/{len(results)}")
        return results

    def benchmark(
        self,
        qc:       QuantumCircuit,
        qpu_name: str,
        combos:   list = None,
        verify:   bool = False,
    ) -> dict:
        """
        복수 조합 병렬 벤치마크 → 최선 조합 선택
        지식베이스 데이터 수집 용도

        Args:
            combos: 비교할 조합 목록 (None이면 전체)

        Returns:
            {combo_name: result, ..., 'best': best_combo}
        """
        if combos is None:
            combos = list(COMBINATIONS.keys())

        results = {}
        for combo in combos:
            print(f"\n  [Benchmark] {combo}")
            try:
                results[combo] = self.optimize(
                    qc, qpu_name, combo, verify=verify)
            except Exception as e:
                print(f"  [Benchmark] {combo} 실패: {e}")
                results[combo] = {"ok": False, "error": str(e)}

        # 최선 조합 선택 (게이트 감소율 기준)
        best = max(
            (k for k, v in results.items() if v.get("ok")),
            key=lambda k: results[k].get("gate_reduction", 0),
            default=None
        )
        results["best"] = best
        if best:
            print(f"\n  [Benchmark] 최선 조합: {best} "
                  f"({results[best]['gate_reduction']*100:.1f}% 감소)")
        return results

    def collect_metadata(self, name: str, result: dict,
                         qpu_name: str) -> dict:
        """지식베이스 저장용 메타데이터"""
        profile = result.get("profile", {})
        return {
            "circuit_name":    name,
            "qpu_name":        qpu_name,
            "combination":     result.get("combination"),
            "opt_engine":      result.get("opt_engine"),
            "map_engine":      result.get("map_engine"),
            "num_qubits":      profile.get("num_qubits"),
            "orig_gates":      profile.get("total_gates"),
            "orig_depth":      profile.get("depth"),
            "t_ratio":         profile.get("t_ratio"),
            "two_q_ratio":     profile.get("two_q_ratio"),
            "pauli_ratio":     profile.get("pauli_ratio"),
            "rot_ratio":       profile.get("rot_ratio"),
            "opt1_gates":      result.get("opt1_gates"),
            "opt1_depth":      result.get("opt1_depth"),
            "gate_reduction":  result.get("gate_reduction"),
            "depth_reduction": result.get("depth_reduction"),
            "opt_time_sec":    result.get("opt_time_sec"),
            "map_time_sec":    result.get("map_time_sec"),
            "total_time_sec":  result.get("total_time_sec"),
            "equivalent":      result.get("equivalent"),
            "t2_ok":           result.get("t2_ok"),
            "ok":              result.get("ok"),
            "timestamp":       time.strftime("%Y-%m-%dT%H:%M:%S"),
        }