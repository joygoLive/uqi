# mcp_server.py
# UQI MCP 서버 - 양자 컴퓨팅 파이프라인 외부 노출
# UQI (Universal Quantum Infrastructure)

import os
import json
import time
import asyncio
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP

import sys
sys.path.insert(0, str(Path(__file__).parent))

from uqi_calibration import UQICalibration
from uqi_optimizer   import UQIOptimizer
from uqi_noise       import UQINoise
from uqi_qec         import UQIQEC
from uqi_rag         import UQIRAG
from uqi_qpu_live_check import live_check_qpu, recommend_alternatives
import uqi_job_store as _job_store
from uqi_messages import (
    MCP_CACHE_EXPIRED,
    MCP_SEMANTIC_NO_QUERY,
    MCP_FILE_EXT_NOT_ALLOWED,
    MCP_FILE_ONLY_PY,
    MCP_FILE_TOO_LARGE,
    MCP_SUBMISSION_NOT_FOUND,
    mcp_qpu_offline,
    mcp_qpu_offline_cached,
    mcp_qpu_offline_live,
    mcp_live_check_unreachable,
    mcp_action_retry_or_cancel,
    mcp_unavailable_qpu,
    mcp_qubit_exceeded_submit,
    mcp_qubit_exceeded_transpile,
    mcp_unsupported_query_type,
    mcp_no_calibration,
    mcp_file_not_found,
    mcp_dir_not_found,
    perceval_run_fail,
    STATUS_AWAITING_CONFIRMATION,
    STATUS_COMPLETED,
    STATUS_CACHE_EXPIRED,
    STATUS_SUBMITTING,
)

import builtins
import os

_original_print = builtins.print
def _stderr_print(*args, **kwargs):
    kwargs['file'] = sys.stderr
    _original_print(*args, **kwargs)
builtins.print = _stderr_print

try:
    import tqdm
    import tqdm.std
    import tqdm.gui

    class _SilentTqdm:
        def __init__(self, *args, **kwargs):
            self.n = 0
            self.total = kwargs.get('total', None)
            self.disable = True
            self.pos = 0
            self.nrows = None
            self.ncols = None
            self.fp = sys.stderr
            self.miniters = 0
            self.mininterval = 0.1
            self.maxinterval = 10
            self.desc = ''
            self.unit = 'it'
            self.unit_scale = False
            self.dynamic_miniters = False
            self.smoothing = 0.3
            self.bar_format = None
            self.postfix = None
            self.unit_divisor = 1000
            self.initial = 0
            self.last_print_n = 0
            self.last_print_t = 0
            self.start_t = 0
            self.avg_time = None
            self._ema_dn = None
            self._ema_dt = None
            self._ema_miniters = None
        def __iter__(self): return iter([])
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def update(self, n=1): pass
        def close(self): pass
        def set_description(self, *a, **kw): pass
        def set_description_str(self, *a, **kw): pass
        def set_postfix(self, *a, **kw): pass
        def set_postfix_str(self, *a, **kw): pass
        def refresh(self, *a, **kw): pass
        def unpause(self): pass
        def reset(self, total=None): pass
        def clear(self, *a, **kw): pass
        def display(self, *a, **kw): pass
        def write(self, *a, **kw): pass
        @classmethod
        def write(cls, s, **kwargs): pass

    tqdm.std.tqdm = _SilentTqdm
    tqdm.tqdm = _SilentTqdm
    tqdm.gui.tqdm = _SilentTqdm
except Exception:
    pass

# ─────────────────────────────────────────────────────────
# MCP 서버 초기화
# ─────────────────────────────────────────────────────────

mcp = FastMCP(
    name="UQI",
    instructions="""
UQI (Universal Quantum Infrastructure) MCP 서버.

양자 컴퓨팅 파이프라인을 제공합니다:
- 회로 추출 및 분석
- 회로 최적화 (Qiskit/TKET/QuiZX)
- 노이즈 시뮬레이션 (IBM/IQM FakeBackend)
- QEC 분석 및 적용
- QPU 제출 (Human-in-the-loop)
- 지식베이스 검색

QPU 제출은 반드시 사용자 확인 후 실행됩니다.
지원 QPU: ibm_fez, iqm_garnet
지원 SDK: PennyLane, Qrisp, CUDAQ, Qiskit, Perceval
""")

_cal  = UQICalibration()
_rag  = UQIRAG()
_noise_cache = {}

from contextvars import ContextVar
import traceback

_request_context: ContextVar[dict] = ContextVar('request_context', default={})

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")
IBM_TOKEN = os.getenv("IBM_QUANTUM_TOKEN")
IQM_TOKEN = os.getenv("IQM_QUANTUM_TOKEN")

SUPPORTED_QPUS = ["ibm_fez", "ibm_marrakesh", "ibm_kingston",
                   "iqm_garnet", "iqm_emerald", "iqm_sirius",
                   "rigetti_cepheus",
                   "ionq_forte1",
                   # Azure Quantum — Pasqal Fresnel 실 QPU만
                   "pasqal_fresnel", "pasqal_fresnel_can1",
                   # Quantinuum — 분석/추천만 가능 (자사 클라우드 통합 전까지 submit 차단)
                   "quantinuum_h2_1", "quantinuum_h2_2", "quantinuum_h1_1",
                   # Braket QuEra (AHS — gate 회로 비호환)
                   "quera_aquila",
                   # Quandela
                   "qpu:ascella", "qpu:belenos",
                   "sim:ascella", "sim:belenos"]
# 정책: 실 QPU 위주. Pasqal 시뮬레이터(Azure emu-*) 제외.
# Quantinuum은 자사 클라우드 통합 예정 — 분석/추천에만 노출, 실 submit은 차단(_SKIP_SUBMIT_QPUS)

_pending_submissions = {}

_PHOTONIC_QPUS = {"qpu:ascella", "qpu:belenos", "sim:ascella", "sim:belenos"}

# Framework → 호환 QPU 매핑
_GATE_BASED_QPUS = [q for q in SUPPORTED_QPUS if q not in _PHOTONIC_QPUS]
_FRAMEWORK_QPU_MAP = {
    "Qiskit":     {"qpus": _GATE_BASED_QPUS, "default": "ibm_fez"},
    "PennyLane":  {"qpus": _GATE_BASED_QPUS, "default": "ibm_fez"},
    "Qrisp":      {"qpus": _GATE_BASED_QPUS, "default": "ibm_fez"},
    "CUDAQ":      {"qpus": _GATE_BASED_QPUS, "default": "ibm_fez"},
    "Perceval":   {"qpus": list(_PHOTONIC_QPUS), "default": "sim:ascella"},
    # AHS (Analog Hamiltonian Simulation) — vendor 별로 SDK 가 분리되어
    # 회로 호환 가능한 QPU 가 *유일*. extractor 가 framework 분류한 시점에
    # 이미 vendor 가 결정되므로 default = 유일한 QPU.
    "Braket-AHS": {"qpus": ["quera_aquila"], "default": "quera_aquila"},
    "Pulser":     {"qpus": ["pasqal_fresnel", "pasqal_fresnel_can1"], "default": "pasqal_fresnel"},
}

def _resolve_qpu(algorithm_file: str, qpu_name: str) -> str:
    """QPU 이름이 'auto'이거나 framework와 불일치할 때 자동 보정.
    예: Perceval 파일 + ibm_fez → sim:ascella, Qiskit 파일 + sim:ascella → ibm_fez

    안전망: 모든 경로에서 'auto' 가 그대로 통과되지 않도록 보장.
            framework 감지 실패 또는 mapping 없을 때도 글로벌 default('ibm_fez') 사용.
    """
    try:
        from uqi_extractor import UQIExtractor
        ext = UQIExtractor(algorithm_file)
        fw = ext.detect_framework()
        mapping = _FRAMEWORK_QPU_MAP.get(fw)
        if mapping:
            if qpu_name == "auto" or qpu_name not in mapping["qpus"]:
                return mapping["default"]
            return qpu_name
    except Exception:
        pass
    # framework 감지 실패 / mapping 없음 — 'auto' 는 글로벌 default 로 고정
    if qpu_name == "auto":
        return "ibm_fez"
    return qpu_name

# ─────────────────────────────────────────────────────────
# AHS (Analog Hamiltonian Simulation) — 공통 헬퍼
#   Braket-AHS (QuEra Aquila) / Pulser (Pasqal Fresnel) 둘 다 처리.
#   gate-based 회로와 워크플로우가 다르므로 analyze/optimize/noise/submit 진입점에서
#   framework 검사 후 별도 path 로 분기.
# ─────────────────────────────────────────────────────────

_AHS_FRAMEWORKS = ("Braket-AHS", "Pulser")


def _analyze_ahs(algorithm_file: str, qpu_name: str, framework: str) -> dict:
    """AHS 회로 분석 — atom count / total duration / 메트릭.

    gate-based 의 'profile' (gates/depth) 대신 AHS-specific 메트릭 반환.
    """
    metrics = {
        "atom_count":         None,
        "register_dimension": None,    # 1D / 2D
        "total_duration_ns":  None,    # pulser ns / braket SI seconds × 1e9
        "shots_recommended":  100,     # 기본권장
    }
    try:
        if framework == "Braket-AHS":
            from uqi_executor_braket import UQIExecutorBraket
            prog = UQIExecutorBraket._extract_ahs_program(algorithm_file)
            try:
                items = list(prog.register)  # AtomArrangementItem 리스트
                metrics["atom_count"] = len(items)
                if items:
                    coord = items[0].coordinate
                    metrics["register_dimension"] = "2D" if len(coord) == 2 else "1D"
            except Exception:
                pass
            try:
                # Braket Hamiltonian: terms 리스트의 첫 DrivingField → amplitude.time_series.times() 마지막 (s)
                terms = prog.hamiltonian.terms
                drive = next((t for t in terms if hasattr(t, 'amplitude')), None)
                if drive is not None:
                    times = drive.amplitude.time_series.times()
                    if times:
                        metrics["total_duration_ns"] = float(times[-1]) * 1e9
            except Exception:
                pass

        elif framework == "Pulser":
            from uqi_executor_azure import UQIExecutorAzure
            seq = UQIExecutorAzure._extract_pulser_sequence(algorithm_file)
            try:
                metrics["atom_count"] = len(seq.register.qubit_ids)
            except Exception:
                pass
            try:
                # pulser Register: .qubits dict 의 좌표 차원 검사 (2-tuple → 2D)
                first = next(iter(seq.register.qubits.values()), None)
                if first is not None:
                    metrics["register_dimension"] = "2D" if len(first) == 2 else "1D"
            except Exception:
                pass
            try:
                metrics["total_duration_ns"] = float(seq.get_duration())
            except Exception:
                pass
    except Exception as e:
        metrics["error"] = str(e)

    return {
        "algorithm_file": algorithm_file,
        "framework":      framework,
        "qpu_name":       qpu_name,
        "circuits": {
            "ahs_main": {
                "profile": {
                    "num_qubits":  metrics["atom_count"] or 0,
                    "total_gates": 0,                        # AHS — gate 개념 없음
                    "depth":       0,
                    "ops":         {"AHS": 1},
                },
                "t2_ratio":  None,
                "qpu_name":  qpu_name,
                "framework": framework,
                "ahs": metrics,
            },
        },
    }


def _noise_simulate_ahs(algorithm_file: str, qpu_name: str, shots: int) -> str:
    """AHS 노이즈 시뮬 — pulser_simulation (Pasqal) / braket LocalSimulator (QuEra).

    counts dict 반환 (gate-based 와 동일 인터페이스). 실제 노이즈 모델은 SDK 자체.
    """
    import json as _json
    from uqi_extractor import UQIExtractor
    try:
        ext = UQIExtractor(algorithm_file)
        framework = ext.detect_framework()
    except Exception as e:
        return _json.dumps({"error": f"framework 감지 실패: {e}"})

    try:
        if framework == "Pulser":
            from uqi_executor_azure import UQIExecutorAzure
            from pulser_simulation import QutipEmulator
            seq = UQIExecutorAzure._extract_pulser_sequence(algorithm_file)
            sim = QutipEmulator.from_sequence(seq)
            sim_result = sim.run()
            counts = dict(sim_result.sample_final_state(N_samples=shots))
        elif framework == "Braket-AHS":
            from uqi_executor_braket import UQIExecutorBraket
            from braket.devices import LocalSimulator
            ahs = UQIExecutorBraket._extract_ahs_program(algorithm_file)
            device = LocalSimulator("braket_ahs")
            task = device.run(ahs, shots=shots)
            res  = task.result()
            # AHS result: measurements list — bitstring counts 변환
            counts = {}
            for m in res.measurements:
                key = "".join(str(int(b)) for b in (m.post_sequence or []))
                counts[key] = counts.get(key, 0) + 1
        else:
            return _json.dumps({"error": f"AHS framework 아님: {framework}"})

        total = sum(counts.values()) or 1
        probs = {k: v/total for k, v in counts.items()}
        return _json.dumps({
            "algorithm_file": algorithm_file,
            "framework":      framework,
            "qpu_name":       qpu_name,
            "shots":          shots,
            "counts":         counts,
            "probs":          probs,
            "note":           f"AHS 로컬 노이즈 시뮬 ({framework}) — SDK 내장 모델",
        }, ensure_ascii=False)
    except Exception as e:
        return _json.dumps({"error": f"AHS noise sim 실패: {e}"})


def _qpu_submit_ahs(algorithm_file: str, qpu_name: str, shots: int,
                    confirmed: bool) -> str:
    """AHS QPU submit — Braket-AHS (QuEra) / Pulser (Pasqal) 분기.

    confirmed=False: 분석/예상 정보 반환 (cost estimate 포함, 실제 제출 X).
    confirmed=True:  실제 제출 → job_id, save_job 으로 RAG 기록.
    """
    import json as _json
    from uqi_extractor import UQIExtractor
    from uqi_pricing import (estimate_cost, format_actual_cost, parse_qpu_full,
                             format_actual_cost_token, get_pricing)

    # framework 감지 (캐시 무관, 단순 소스 정규식)
    try:
        ext = UQIExtractor(algorithm_file)
        framework = ext.detect_framework()
    except Exception as e:
        return _json.dumps({"error": f"framework 감지 실패: {e}"})

    # 분석 단계: 비용/메트릭 + 안내 메시지
    if not confirmed:
        analysis = _analyze_ahs(algorithm_file, qpu_name, framework)
        cost = estimate_cost(qpu_name, shots)
        _pmeta = get_pricing(qpu_name) or {}
        _pricing_vendor = _pmeta.get("vendor", "")
        atom_count = analysis["circuits"]["ahs_main"]["ahs"].get("atom_count")
        duration_ns = analysis["circuits"]["ahs_main"]["ahs"].get("total_duration_ns")
        _meta = parse_qpu_full(qpu_name)
        msg_lines = [
            f"AHS 제출 분석 — {_meta['vendor']} {_meta['model']}",
            f"  framework:      {framework}",
            f"  atom_count:     {atom_count}",
            f"  duration_ns:    {duration_ns}",
            f"  shots:          {shots}",
            f"  est. cost:      {format_actual_cost(_pricing_vendor, qpu_name, cost)}",
            f"  runtime:        {_meta['runtime']}",
            "",
            "💡 confirmed=True 로 다시 호출하면 실제 제출됩니다.",
        ]
        return _json.dumps({
            "ok":             True,
            "confirmed":      False,
            "selected_qpu":   qpu_name,
            "framework":      framework,
            "shots":          shots,
            "atom_count":     atom_count,
            "duration_ns":    duration_ns,
            "cost":           cost,
            "cost_display":   format_actual_cost(_pricing_vendor, qpu_name, cost),
            "cost_token":     format_actual_cost_token(_pricing_vendor, qpu_name, cost),
            "message":        "\n".join(msg_lines),
        }, ensure_ascii=False)

    # 실제 제출 (confirmed=True)
    name = Path(algorithm_file).stem
    try:
        if framework == "Braket-AHS":
            from uqi_executor_braket import UQIExecutorBraket
            ex = UQIExecutorBraket(converter=None, shots=shots)
            sub = ex._submit_single_ahs(name, algorithm_file, backend_name=qpu_name)
        elif framework == "Pulser":
            from uqi_executor_azure import UQIExecutorAzure
            ex = UQIExecutorAzure(converter=None, shots=shots)
            sub = ex._submit_single_ahs(name, algorithm_file, backend_name=qpu_name)
        else:
            return _json.dumps({"error": f"AHS 가 아닌 framework: {framework}"})
    except Exception as e:
        return _json.dumps({"error": f"AHS submit 실패: {e}"})

    if not sub.get("ok"):
        return _json.dumps({"error": sub.get("error", "submit 실패")})

    # save_job — catalog 자동 매핑 (qpu_vendor / qpu_model / runtime / qpu_modality)
    try:
        _job_store.save_job(
            job_id=sub["job_id"],
            qpu_name=qpu_name,
            circuit_name=name,
            shots=shots,
            extra={"framework": framework, "via": sub.get("via"),
                   "backend": qpu_name, "ahs": True},
        )
    except Exception as _se:
        print(f"  [AHS submit] save_job 실패: {_se}", file=sys.stderr)

    return _json.dumps({
        "ok":           True,
        "confirmed":    True,
        "selected_qpu": qpu_name,
        "framework":    framework,
        "shots":        shots,
        "job_id":       sub["job_id"],
        "via":          sub.get("via"),
        "results":      {name: {"ok": True, "job_id": sub["job_id"],
                                "via": sub.get("via")}},
    }, ensure_ascii=False)


# 회로별 순차 제출 진행상황 추적 (submission_id → progress dict)
_submission_progress: dict = {}   # {sid: {status, total, done, results, selected_qpu, shots}}

_qpu_status_cache     = {"data": None, "ts": 0.0}
_qpu_status_info_cache = {"data": None, "ts": 0.0}
_QPU_STATUS_TTL       = 300  # 5분

def _get_available_qpus_cached():
    import time as _time
    import contextlib
    now = _time.time()
    if _qpu_status_cache["data"] is None or now - _qpu_status_cache["ts"] > _QPU_STATUS_TTL:
        try:
            with contextlib.redirect_stdout(sys.stderr):
                available = _cal.get_available_qpus()
            if available:
                _qpu_status_cache["data"] = available
                _qpu_status_cache["ts"]   = now
        except Exception:
            pass
    return _qpu_status_cache["data"] or SUPPORTED_QPUS

def _get_qpu_status_cached() -> dict:
    """_cal.get_qpu_status() 결과를 5분 캐싱 (매 분석마다 전 장비 재조회 방지)"""
    import time as _time
    import contextlib
    now = _time.time()
    if _qpu_status_info_cache["data"] is None or now - _qpu_status_info_cache["ts"] > _QPU_STATUS_TTL:
        try:
            with contextlib.redirect_stdout(sys.stderr):
                status = _cal.get_qpu_status()
            if status:
                _qpu_status_info_cache["data"] = status
                _qpu_status_info_cache["ts"]   = now
        except Exception:
            pass
    return _qpu_status_info_cache["data"] or {}

# ─────────────────────────────────────────────────────────
# 보안: 정적 분석 + 파일 유효성 체크
# ─────────────────────────────────────────────────────────

_ALLOWED_IMPORTS = {
    "qiskit", "qiskit_aer", "qiskit_algorithms","qiskit_finance", "pennylane", "qml", "qrisp", "cudaq", "perceval",
    "numpy", "scipy", "matplotlib", "sympy", "networkx",
    "json", "math", "cmath", "itertools", "functools", "collections",
    "typing", "dataclasses", "enum", "abc", "copy", "time",
    "random", "statistics", "re", "string", "struct",
    "tqdm", "rich", "os",
}

_BLOCKED_PATTERNS = [
    (r'\bos\.(?!getenv\b)', "os module direct call"),
    (r'\bsubprocess\b', "subprocess module"),
    (r'\bsocket\b', "socket module"),
    (r'\beval\s*\(', "eval() usage"),
    (r'\bexec\s*\(', "exec() usage"),
    (r'\b__import__\s*\(', "__import__() usage"),
    (r'\bopen\s*\(.*["\']w["\']', "file write (open write)"),
    (r'\bshutil\b', "shutil module"),
    (r'\burllib\b', "urllib module"),
    (r'\brequests\b', "requests module"),
    (r'\bhttpx\b', "httpx module"),
    (r'\baiohttp\b', "aiohttp module"),
    (r'\bparamiko\b', "paramiko module"),
    (r'\bpickle\b', "pickle module"),
    (r'\bctypes\b', "ctypes module"),
    (r'\bcffi\b', "cffi module"),
    (r'\bimportlib\b', "importlib module"),
    (r'\bpty\b', "pty module"),
    (r'\bsignal\b', "signal module"),
    (r'\bmultiprocessing\b', "multiprocessing module"),
    (r'\bthreading\b', "threading module"),
    (r'\bconcurrent\b', "concurrent module"),
]

_MAX_QUBITS = 200
_MAX_GATES  = 2_000_000


def _safe_file_check(algorithm_file: str, tool: str = None) -> Optional[str]:
    import re as _re
    path = Path(algorithm_file)
    if not path.exists():
        return mcp_file_not_found(algorithm_file)
    if path.suffix != ".py":
        return f"Only Python (.py) files are allowed: {algorithm_file}"
    if path.stat().st_size > 1 * 1024 * 1024:
        return f"File size exceeded (max 1MB): {algorithm_file}"
    try:
        source = path.read_text(encoding="utf-8")
    except Exception as e:
        return f"Failed to read file: {e}"
    _src_lines = source.splitlines()
    for pattern, desc in _BLOCKED_PATTERNS:
        m = _re.search(pattern, source)
        if m:
            msg = f"Security policy violation - {desc} detected (pattern: {pattern})"
            line_no    = source[:m.start()].count('\n') + 1
            match_line = _src_lines[line_no - 1].strip() if _src_lines else ''
            _rag.add_security_block(algorithm_file, reason=desc, pattern=pattern, tool=tool,
                                    match_lineno=line_no, match_line=match_line)
            return msg
    import_lines = _re.findall(
        r'^\s*(?:import|from)\s+([\w\.]+)', source, _re.MULTILINE
    )
    for mod in import_lines:
        root = mod.split(".")[0]
        if root not in _ALLOWED_IMPORTS:
            m2         = _re.search(rf'^\s*(?:import|from)\s+{_re.escape(root)}\b', source, _re.MULTILINE)
            line_no    = (source[:m2.start()].count('\n') + 1) if m2 else None
            match_line = _src_lines[line_no - 1].strip() if (line_no and _src_lines) else ''
            msg = f"Unauthorized module import: '{root}' (allowed: {sorted(_ALLOWED_IMPORTS)})"
            _rag.add_security_block(algorithm_file, reason=f"Unauthorized module import: {root}",
                                    pattern=f"import {root}", tool=tool,
                                    match_lineno=line_no, match_line=match_line)
            return msg
    return None

# ─────────────────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────────────────

# Qiskit 표준 게이트 허용 목록
_QISKIT_STD_GATES = {
    'x','y','z','h','s','sdg','t','tdg','sx','sxdg',
    'rx','ry','rz','r','u','u1','u2','u3','p',
    'cx','cy','cz','ch','crx','cry','crz','cp','cu','cu1','cu3',
    'swap','iswap','ecr','dcx','ccx','cswap',
    'id','barrier','measure','reset','delay','store','snapshot',
    'if_else','for_loop','while_loop','break_loop','continue_loop',
}

def _get_calibration(qpu_name: str) -> dict:
    cal = _cal.get_transpile_params(qpu_name) or {}
    # basis_gates에서 비표준 게이트 제거
    if cal.get('basis_gates') and isinstance(cal['basis_gates'], list):
        cal['basis_gates'] = [g for g in cal['basis_gates'] if g in _QISKIT_STD_GATES]
    return cal


def _safe_json(obj) -> str:
    def _convert(o):
        if hasattr(o, 'item'):
            return o.item()
        if hasattr(o, '__float__'):
            return float(o)
        return str(o)
    return json.dumps(obj, default=_convert, indent=2, ensure_ascii=False)


def _file_hash(algorithm_file: str) -> str:
    """파일 내용 기반 해시 (캐시 키용)"""
    import hashlib
    try:
        content = Path(algorithm_file).read_bytes()
        return hashlib.md5(content).hexdigest()[:12]
    except Exception:
        return "nohash"


def _extract_and_convert(algorithm_file: str):
    """추출 + 변환 공통 헬퍼 (동기, 스레드에서 실행) — QASM 결과 캐시 포함"""
    import contextlib
    from uqi_extractor     import UQIExtractor
    from uqi_qir_converter import UQIQIRConverter

    cache_key = f"extract:{_file_hash(algorithm_file)}"
    cached = _rag.get_cache(cache_key)
    if cached:
        try:
            cached_data = json.loads(cached)
            framework    = cached_data["framework"]
            qasm_results = cached_data["qasm_results"]
            qir_results  = cached_data.get("qir_results", {})
            extractor = UQIExtractor(algorithm_file)
            extractor.framework = framework
            extractor.circuits  = {}
            # perceval_circuits 복원 (유니터리+input_state 직렬화 데이터)
            pcvl_cached = cached_data.get("perceval_circuits", {})
            for k, v in pcvl_cached.items():
                extractor.perceval_circuits[k] = tuple(v)
            converter = UQIQIRConverter(extractor)
            converter.qasm_results = qasm_results
            converter.qir_results  = qir_results
            print(f"  [Cache] 추출 캐시 히트: {Path(algorithm_file).name}", file=sys.stderr)
            return extractor, converter, framework
        except Exception:
            pass

    extractor = UQIExtractor(algorithm_file)
    framework = extractor.detect_framework()
    with contextlib.redirect_stdout(sys.stderr):
        extractor.extract_circuits()
    converter = UQIQIRConverter(extractor)
    converter.convert_all()

    try:
        # perceval_circuits: {name: (unitary, input_state, num_modes)} — JSON 직렬화 가능
        pcvl_serializable = {k: list(v) for k, v in extractor.perceval_circuits.items()}
        cache_data = json.dumps({
            "framework":          framework,
            "qasm_results":       converter.qasm_results,
            "qir_results":        {k: v.hex() if isinstance(v, bytes) else str(v)
                                   for k, v in (converter.qir_results or {}).items()},
            "perceval_circuits":  pcvl_serializable,
        }, ensure_ascii=False)
        _rag.set_cache(cache_key, cache_data)
    except Exception as e:
        print(f"  [Cache] 추출 캐시 저장 실패: {e}", file=sys.stderr)

    return extractor, converter, framework


# ─────────────────────────────────────────────────────────
# 툴 1: 회로 분석
# ─────────────────────────────────────────────────────────

@mcp.tool()
async def uqi_analyze(
    algorithm_file: str,
    qpu_name:       str = "ibm_fez",
) -> str:
    """회로 추출 및 특성 분석. algorithm_file: .py 경로, qpu_name: ibm_fez|iqm_garnet"""
    _check_err = _safe_file_check(algorithm_file, tool="uqi_analyze")
    if _check_err:
        return json.dumps({"error": _check_err})
    qpu_name = _resolve_qpu(algorithm_file, qpu_name)

    def _run():
        from uqi_optimizer import analyze_circuit
        from qiskit        import QuantumCircuit
        try:
            _cache_key = f"analyze:{_file_hash(algorithm_file)}:{qpu_name}"
            _cached = _rag.get_cache(_cache_key)
            if _cached:
                print(f"  [Cache] analyze 캐시 히트: {Path(algorithm_file).name}", file=sys.stderr)
                try:
                    _cached_obj = json.loads(_cached)
                    _cached_obj["_cached"] = True
                    return json.dumps(_cached_obj, ensure_ascii=False)
                except Exception:
                    return _cached

            extractor, converter, framework = _extract_and_convert(algorithm_file)

            # ── AHS framework 분기 (Braket-AHS / Pulser) — gate 회로 무관 ──
            if framework in _AHS_FRAMEWORKS:
                _ahs_result = _safe_json(_analyze_ahs(algorithm_file, qpu_name, framework))
                _rag.set_cache(_cache_key, _ahs_result)
                return _ahs_result

            calibration = _get_calibration(qpu_name)
            results = {}
            _should_cache = False
            is_photonic_qpu = qpu_name in _PHOTONIC_QPUS

            # ── Perceval 회로 전용 경로: QASM 없이 perceval_circuits에서 직접 분석 ──
            if is_photonic_qpu and not converter.qasm_results and extractor.perceval_circuits:
                if not extractor.perceval_circuits:
                    extractor._extract_perceval_circuits()
                for cname, entry_data in extractor.perceval_circuits.items():
                    unitary_data, is_list, num_modes = entry_data
                    n_photons = sum(is_list)
                    max_modes   = calibration.get("max_mode_count") or 0
                    max_photons = calibration.get("max_photon_count") or 0
                    transmit    = calibration.get("avg_transmittance")
                    hom         = calibration.get("avg_hom")
                    g2          = calibration.get("avg_g2")
                    clock_mhz   = calibration.get("clock_mhz")
                    mode_ok     = (num_modes <= max_modes) if max_modes else None
                    notes = []
                    if mode_ok is False:
                        notes.append(f"⚠ Circuit uses {num_modes} modes but {qpu_name} supports max {max_modes} modes — submission will fail.")
                    elif mode_ok:
                        notes.append(f"✓ Mode count ({num_modes}/{max_modes}) within QPU limit.")
                    if transmit is not None:
                        eta_pct = round(transmit * 100, 1)
                        notes.append(f"{'⚠ Low t' if eta_pct < 5 else '✓ T'}ransmittance: {eta_pct}% per optical component." + (" Use min_detected_photons_filter to reduce noise." if eta_pct < 5 else ""))
                    if hom is not None:
                        hom_pct = round(hom * 100, 1)
                        notes.append(f"{'⚠' if hom_pct < 85 else '✓'} HOM visibility: {hom_pct}%" + (" — photon indistinguishability is reduced." if hom_pct < 85 else " — good photon indistinguishability."))
                    if g2 is not None:
                        notes.append(f"{'⚠' if g2 > 0.05 else '✓'} g²(0)={g2:.4f}" + (" — multi-photon contamination." if g2 > 0.05 else " — near-ideal single-photon source."))
                    if max_photons:
                        notes.append(f"ℹ Max photon input: {max_photons}. Verify input state n ≤ {max_photons}.")
                    if clock_mhz:
                        notes.append(f"ℹ Clock: {clock_mhz} MHz — each sample takes ~{round(1000/clock_mhz,1)} µs.")
                    results[cname] = {
                        "profile": {
                            "num_qubits":  num_modes,
                            "total_gates": 1,
                            "depth":       1,
                            "ops":         {"PhotonicCircuit": 1},
                        },
                        "t2_ratio":  None,
                        "qpu_name":  qpu_name,
                        "framework": framework,
                        "photonic": {
                            "num_modes":        num_modes,
                            "max_modes":        max_modes,
                            "max_photons":      max_photons,
                            "n_input_photons":  n_photons,
                            "avg_transmittance":transmit,
                            "avg_hom":          hom,
                            "avg_g2":           g2,
                            "clock_mhz":        clock_mhz,
                            "mode_ok":          mode_ok,
                            "notes":            notes,
                        },
                    }
                    _should_cache = True

                _result = _safe_json({
                    "algorithm_file": algorithm_file,
                    "framework":      framework,
                    "circuits":       results,
                })
                if _should_cache:
                    _rag.set_cache(_cache_key, _result)
                return _result

            for name, qasm in converter.qasm_results.items():
                try:
                    qc      = QuantumCircuit.from_qasm_str(qasm)
                    profile = analyze_circuit(qc)
                    t2_ms   = calibration.get("avg_t2_ms")
                    q2_ns   = calibration.get("avg_2q_ns")
                    t2_ratio = None
                    if t2_ms and q2_ns:
                        t2_ns    = t2_ms * 1e6
                        est_ns   = q2_ns * qc.depth()
                        t2_ratio = round(est_ns / t2_ns, 2)
                    entry = {
                        "profile":   profile,
                        "t2_ratio":  t2_ratio,
                        "qpu_name":  qpu_name,
                        "framework": framework,
                        "qasm":      qasm,
                    }
                    # ── 광자 QPU 전용 분석 정보 ──
                    if is_photonic_qpu:
                        num_modes   = qc.num_qubits   # Perceval 모드 → Qiskit 큐비트 매핑
                        max_modes   = calibration.get("max_mode_count") or 0
                        max_photons = calibration.get("max_photon_count") or 0
                        transmit    = calibration.get("avg_transmittance")
                        hom         = calibration.get("avg_hom")
                        g2          = calibration.get("avg_g2")
                        clock_mhz   = calibration.get("clock_mhz")
                        mode_ok     = (num_modes <= max_modes) if max_modes else None
                        notes = []
                        if mode_ok is False:
                            notes.append(
                                f"⚠ Circuit uses {num_modes} modes but {qpu_name} supports "
                                f"max {max_modes} modes — submission will fail."
                            )
                        elif mode_ok:
                            notes.append(
                                f"✓ Mode count ({num_modes}/{max_modes}) within QPU limit."
                            )
                        if transmit is not None:
                            eta_pct = round(transmit * 100, 1)
                            if eta_pct < 5:
                                notes.append(
                                    f"⚠ Low transmittance ({eta_pct}%) — expect high photon loss. "
                                    "Use min_detected_photons_filter to reduce noise."
                                )
                            else:
                                notes.append(
                                    f"✓ Transmittance: {eta_pct}% per optical component."
                                )
                        if hom is not None:
                            hom_pct = round(hom * 100, 1)
                            if hom_pct < 85:
                                notes.append(
                                    f"⚠ HOM visibility {hom_pct}% (< 85%) — photon "
                                    "indistinguishability is reduced, affecting interference quality."
                                )
                            else:
                                notes.append(
                                    f"✓ HOM visibility: {hom_pct}% — good photon indistinguishability."
                                )
                        if g2 is not None:
                            if g2 > 0.05:
                                notes.append(
                                    f"⚠ g²(0)={g2:.4f} > 0.05 — photon source has multi-photon contamination."
                                )
                            else:
                                notes.append(
                                    f"✓ g²(0)={g2:.4f} — near-ideal single-photon source purity."
                                )
                        if max_photons:
                            notes.append(
                                f"ℹ Max photon input: {max_photons}. "
                                f"Verify input state n ≤ {max_photons}."
                            )
                        if clock_mhz:
                            notes.append(
                                f"ℹ Clock: {clock_mhz} MHz — each sample takes ~{round(1000/clock_mhz,1)} µs."
                            )
                        entry["photonic"] = {
                            "num_modes":        num_modes,
                            "max_modes":        max_modes,
                            "max_photons":      max_photons,
                            "avg_transmittance":transmit,
                            "avg_hom":          hom,
                            "avg_g2":           g2,
                            "clock_mhz":        clock_mhz,
                            "mode_ok":          mode_ok,
                            "notes":            notes,
                        }
                    results[name] = entry
                    _should_cache = True
                except Exception as e:
                    results[name] = {"error": str(e)}

            _result = _safe_json({
                "algorithm_file": algorithm_file,
                "framework":      framework,
                "circuits":       results,
            })
            if not results:
                ctx = _request_context.get()
                _rag.add_pipeline_issue(
                    stage="uqi_analyze",
                    sdk=framework,
                    issue="Circuit extraction failed — circuits empty (kernel compile error or execution failure)",
                    solution="",
                    qpu_name=qpu_name,
                    severity="error",
                    extra={
                        "algorithm_file": algorithm_file,
                        "client_ip":  ctx.get("client_ip", "unknown"),
                        "transport":  ctx.get("transport", "unknown"),
                    }
                )
            if _should_cache:
                _rag.set_cache(_cache_key, _result)
            return _result
        except Exception as e:
            ctx = _request_context.get()
            _rag.add_pipeline_issue(
                stage="uqi_analyze",          # 예: "uqi_analyze", "uqi_optimize" 등
                sdk=framework if 'framework' in dir() else "",
                issue=str(e),
                solution="",
                qpu_name=qpu_name if 'qpu_name' in dir() else "",
                severity="error",
                extra={
                    "traceback":    traceback.format_exc(),
                    "algorithm_file": algorithm_file if 'algorithm_file' in dir() else "",
                    "client_ip":    ctx.get("client_ip", "unknown"),
                    "transport":    ctx.get("transport", "unknown"),
                }
            )
            return json.dumps({"error": str(e)})

    return await asyncio.to_thread(_run)


# ─────────────────────────────────────────────────────────
# 툴 2: 회로 최적화
# ─────────────────────────────────────────────────────────

@mcp.tool(output_schema=None, timeout=300)
async def uqi_optimize(
    algorithm_file: str,
    qpu_name:       str  = "ibm_fez",
    combination:    str  = "auto",
    verify:         bool = False,
) -> str:
    """회로 최적화. combination: auto|qiskit+sabre|tket+sabre|quizx+sabre|appx+sabre"""
    _check_err = _safe_file_check(algorithm_file)
    if _check_err:
        return json.dumps({"error": _check_err})
    qpu_name = _resolve_qpu(algorithm_file, qpu_name)
    if qpu_name in _PHOTONIC_QPUS:
        return json.dumps({
            "error": (
                f"Circuit optimization is not supported for photonic QPUs ({qpu_name}). "
                "Qubit-based transpilation (gate decomposition, routing) does not apply to "
                "photonic circuits. Use Circuit Analysis and QPU Submit steps directly."
            ),
            "photonic": True,
        })
    from uqi_pricing import _ANALOG_QPUS as _ANALOG_SET
    if qpu_name in _ANALOG_SET:
        return json.dumps({
            "error": (
                f"Circuit optimization is not applicable for analog (AHS) QPUs ({qpu_name}). "
                "AHS programs (braket.ahs / pulser) describe time-evolved Hamiltonians, "
                "not gate sequences — there is no transpilation step. Proceed directly to "
                "Analyze (for register/duration metrics) and QPU Submit."
            ),
            "analog": True,
        })

    def _run():
        from qiskit import QuantumCircuit
        try:
            _cache_key = f"optimize:{_file_hash(algorithm_file)}:{qpu_name}:{combination}"
            _cached = _rag.get_cache(_cache_key)
            if _cached:
                print(f"  [Cache] optimize 캐시 히트: {Path(algorithm_file).name}", file=sys.stderr)
                try:
                    _cached_obj = json.loads(_cached)
                    _cached_obj["_cached"] = True
                    return json.dumps(_cached_obj, ensure_ascii=False)
                except Exception:
                    return _cached

            extractor, converter, framework = _extract_and_convert(algorithm_file)
            calibration = _get_calibration(qpu_name)
            device_qubits = calibration.get("num_qubits", 0)
            optimizer   = UQIOptimizer(calibration=calibration)
            results     = {}
            for name, qasm in converter.qasm_results.items():
                try:
                    qc     = QuantumCircuit.from_qasm_str(qasm)
                    if device_qubits and qc.num_qubits > device_qubits:
                        results[name] = {"error": mcp_qubit_exceeded_transpile(name, qc.num_qubits, qpu_name, device_qubits)}
                        continue
                    result = optimizer.optimize(qc, qpu_name, combination=combination, verify=verify)
                    meta   = optimizer.collect_metadata(name, result, qpu_name, algorithm_file)
                    _rag.add_optimization(meta)
                    qc_final = result.get("circuit")
                    opt_qasm = None
                    if qc_final is not None:
                        try:
                            from qiskit import qasm2
                            opt_qasm = qasm2.dumps(qc_final)
                        except Exception:
                            pass
                    results[name] = {
                        "combination":     result.get("combination"),
                        "opt_engine":      result.get("opt_engine"),
                        "map_engine":      result.get("map_engine"),
                        "gate_reduction":  result.get("gate_reduction"),
                        "depth_reduction": result.get("depth_reduction"),
                        "opt1_gates":      result.get("opt1_gates"),
                        "opt1_depth":      result.get("opt1_depth"),
                        "opt_gates":       result.get("opt_gates"),
                        "opt_depth":       result.get("opt_depth"),
                        "opt_time_sec":    result.get("opt_time_sec"),
                        "map_time_sec":    result.get("map_time_sec"),
                        "ok":              result.get("ok"),
                        "qasm":            opt_qasm,
                    }
                except Exception as e:
                    results[name] = {"error": str(e)}

            _result = _safe_json({"qpu_name": qpu_name, "results": results})
            if not results:
                ctx = _request_context.get()
                _rag.add_pipeline_issue(
                    stage="uqi_optimize",
                    sdk=framework,
                    issue="Optimization failed — results empty (circuit extraction or conversion failure)",
                    solution="",
                    qpu_name=qpu_name,
                    severity="error",
                    extra={
                        "algorithm_file": algorithm_file,
                        "client_ip":  ctx.get("client_ip", "unknown"),
                        "transport":  ctx.get("transport", "unknown"),
                    }
                )
            # ok=True인 회로가 하나 이상 있을 때만 캐시 저장 (실패 결과 캐싱 방지)
            _any_ok = any(r.get("ok") for r in results.values() if isinstance(r, dict))
            if _any_ok:
                _rag.set_cache(_cache_key, _result)
            return _result
        except Exception as e:
            ctx = _request_context.get()
            _rag.add_pipeline_issue(
                stage="uqi_optimize",          # 예: "uqi_analyze", "uqi_optimize" 등
                sdk=framework if 'framework' in dir() else "",
                issue=str(e),
                solution="",
                qpu_name=qpu_name if 'qpu_name' in dir() else "",
                severity="error",
                extra={
                    "traceback":    traceback.format_exc(),
                    "algorithm_file": algorithm_file if 'algorithm_file' in dir() else "",
                    "client_ip":    ctx.get("client_ip", "unknown"),
                    "transport":    ctx.get("transport", "unknown"),
                }
            )
            return json.dumps({"error": str(e)})

    return await asyncio.to_thread(_run)


# ─────────────────────────────────────────────────────────
# 툴 3: 노이즈 시뮬
# ─────────────────────────────────────────────────────────

@mcp.tool(output_schema=None, timeout=300)
async def uqi_noise_simulate(
    algorithm_file: str,
    qpu_name:       str = "ibm_fez",
    sdk:            str = "qiskit",
    shots:          int = 1024,
) -> str:
    """노이즈 시뮬레이션. sdk: qiskit|pennylane, shots: 샘플 수"""
    _check_err = _safe_file_check(algorithm_file, tool="uqi_noise_simulate")
    if _check_err:
        return json.dumps({"error": _check_err})
    qpu_name = _resolve_qpu(algorithm_file, qpu_name)
    if qpu_name in _PHOTONIC_QPUS:
        return json.dumps({
            "error": (
                f"Noise simulation is not supported for photonic QPUs ({qpu_name}). "
                "Qubit-based noise models (depolarizing, thermal relaxation) do not apply to "
                "photonic circuits. Use the QPU Submit step with Perceval executor to run "
                "directly on the photonic hardware or simulator."
            ),
            "photonic": True,
        })
    # ── AHS 노이즈 시뮬 — Pulser: pulser_simulation, Braket-AHS: braket LocalSimulator ──
    from uqi_pricing import _ANALOG_QPUS as _ANALOG_SET
    if qpu_name in _ANALOG_SET:
        return await asyncio.to_thread(_noise_simulate_ahs,
                                       algorithm_file, qpu_name, shots)

    def _run():
        from qiskit import QuantumCircuit
        try:
            _cache_key = f"noise_v2:{_file_hash(algorithm_file)}:{qpu_name}:{shots}"
            _cached = _rag.get_cache(_cache_key)
            if _cached:
                print(f"  [Cache] noise 캐시 히트: {Path(algorithm_file).name}", file=sys.stderr)
                try:
                    _cached_obj = json.loads(_cached)
                    _cached_obj["_cached"] = True
                    return json.dumps(_cached_obj, ensure_ascii=False)
                except Exception:
                    return _cached

            extractor, converter, framework = _extract_and_convert(algorithm_file)
            calibration = _get_calibration(qpu_name)
            noise       = UQINoise(qpu_name, calibration)
            results     = {}
            for name, qasm in converter.qasm_results.items():
                try:
                    qc = QuantumCircuit.from_qasm_str(qasm)

                    # A안: auto best-combination transpile 먼저 수행
                    # optimize(auto)와 동일한 전략으로 물리 회로로 변환 후 noise 적용
                    qc_for_noise = qc
                    combination_used = None
                    try:
                        optimizer   = UQIOptimizer(calibration=calibration)
                        opt_result  = optimizer.optimize(
                            qc, qpu_name, combination="auto", verify=False)
                        if opt_result.get("ok") and opt_result.get("circuit") is not None:
                            qc_for_noise     = opt_result["circuit"]
                            combination_used = opt_result.get("combination")
                            print(f"  [Noise] pre-optimize 완료: {combination_used} "
                                  f"gate_reduction={opt_result.get('gate_reduction', 0):.3f}",
                                  file=sys.stderr)
                        else:
                            print(f"  [Noise] pre-optimize 실패, 원본 회로 사용",
                                  file=sys.stderr)
                    except Exception as oe:
                        print(f"  [Noise] pre-optimize 예외, 원본 회로 사용: {oe}",
                              file=sys.stderr)

                    r  = noise.run_comparison(qc_for_noise, shots=shots)
                    _rag.add_execution(
                        circuit_name=name, qpu_name=qpu_name,
                        backend=f"noise_sim_{sdk}", shots=shots,
                        counts=r["noise_counts"], ok=True,
                        extra={"comparison": r["comparison"],
                               "algorithm_file": Path(algorithm_file).name}
                    )
                    results[name] = {
                        "ideal_counts":    r["ideal_counts"],
                        "noise_counts":    r["noise_counts"],
                        "tvd":             r["comparison"]["tvd"],
                        "fidelity":        r["comparison"]["fidelity"],
                        "combination":     combination_used,
                    }
                except Exception as e:
                    results[name] = {"error": str(e)}

            _result = _safe_json({"qpu_name": qpu_name, "sdk": sdk, "shots": shots, "results": results})
            if not results:
                ctx = _request_context.get()
                _rag.add_pipeline_issue(
                    stage="uqi_noise_simulate",
                    sdk=framework,
                    issue="Noise simulation failed — results empty (circuit extraction or conversion failure)",
                    solution="",
                    qpu_name=qpu_name,
                    severity="error",
                    extra={
                        "algorithm_file": algorithm_file,
                        "client_ip":  ctx.get("client_ip", "unknown"),
                        "transport":  ctx.get("transport", "unknown"),
                    }
                )
            _rag.set_cache(_cache_key, _result)
            return _result
        except Exception as e:
            ctx = _request_context.get()
            _rag.add_pipeline_issue(
                stage="uqi_noise_simulate",          # 예: "uqi_analyze", "uqi_optimize" 등
                sdk=framework if 'framework' in dir() else "",
                issue=str(e),
                solution="",
                qpu_name=qpu_name if 'qpu_name' in dir() else "",
                severity="error",
                extra={
                    "traceback":    traceback.format_exc(),
                    "algorithm_file": algorithm_file if 'algorithm_file' in dir() else "",
                    "client_ip":    ctx.get("client_ip", "unknown"),
                    "transport":    ctx.get("transport", "unknown"),
                }
            )
            return json.dumps({"error": str(e)})

    return await asyncio.to_thread(_run)


# ─────────────────────────────────────────────────────────
# 툴 4: QEC 분석
# ─────────────────────────────────────────────────────────

@mcp.tool(output_schema=None, timeout=300)
async def uqi_qec_analyze(
    algorithm_file: str,
    qpu_name:       str = "ibm_fez",
    shots:          int = 1024,
) -> str:
    """QEC 필요성 분석. apply_code 적용은 uqi_qec_apply 툴 사용."""
    _check_err = _safe_file_check(algorithm_file, tool="uqi_qec_analyze")
    if _check_err:
        return json.dumps({"error": _check_err})
    qpu_name = _resolve_qpu(algorithm_file, qpu_name)
    if qpu_name in _PHOTONIC_QPUS:
        return json.dumps({
            "error": (
                f"QEC analysis is not supported for photonic QPUs ({qpu_name}). "
                "Qubit-based QEC codes (bit-flip, phase-flip, Steane) do not apply to "
                "photonic circuits. Photonic error mitigation relies on different techniques "
                "such as photon-number-resolving detection and boson sampling fidelity checks."
            ),
            "photonic": True,
        })

    def _run():
        from qiskit import QuantumCircuit
        try:
            _cache_key = f"qec_analyze:{_file_hash(algorithm_file)}:{qpu_name}:{shots}"
            _cached = _rag.get_cache(_cache_key)
            if _cached:
                print(f"  [Cache] qec_analyze 캐시 히트: {Path(algorithm_file).name}", file=sys.stderr)
                try:
                    _cached_obj = json.loads(_cached)
                    _cached_obj["_cached"] = True
                    return json.dumps(_cached_obj, ensure_ascii=False)
                except Exception:
                    return _cached

            extractor, converter, framework = _extract_and_convert(algorithm_file)
            calibration = _get_calibration(qpu_name)
            noise       = UQINoise(qpu_name, calibration)
            qec         = UQIQEC(calibration=calibration)
            results     = {}
            for name, qasm in converter.qasm_results.items():
                try:
                    qc       = QuantumCircuit.from_qasm_str(qasm)
                    r        = noise.run_comparison(qc, shots=shots)
                    analysis = qec.analyze(r["comparison"], qc)
                    results[name] = {
                        "necessity":         analysis["necessity"],
                        "fidelity":          analysis["fidelity"],
                        "tvd":               analysis["tvd"],
                        "t2_ratio":          analysis["t2_ratio"],
                        "reasons":           analysis["reasons"],
                        "recommended_codes": analysis["recommended_codes"],
                    }
                except Exception as e:
                    results[name] = {"error": str(e)}

            _result = _safe_json({"qpu_name": qpu_name, "results": results})
            if not results:
                ctx = _request_context.get()
                _rag.add_pipeline_issue(
                    stage="uqi_qec_analyze",
                    sdk=framework,
                    issue="QEC analysis failed — results empty (circuit extraction or conversion failure)",
                    solution="",
                    qpu_name=qpu_name,
                    severity="error",
                    extra={
                        "algorithm_file": algorithm_file,
                        "client_ip":  ctx.get("client_ip", "unknown"),
                        "transport":  ctx.get("transport", "unknown"),
                    }
                )
            _rag.set_cache(_cache_key, _result)
            return _result
        except Exception as e:
            ctx = _request_context.get()
            _rag.add_pipeline_issue(
                stage="uqi_qec_analyze",          # 예: "uqi_analyze", "uqi_optimize" 등
                sdk=framework if 'framework' in dir() else "",
                issue=str(e),
                solution="",
                qpu_name=qpu_name if 'qpu_name' in dir() else "",
                severity="error",
                extra={
                    "traceback":    traceback.format_exc(),
                    "algorithm_file": algorithm_file if 'algorithm_file' in dir() else "",
                    "client_ip":    ctx.get("client_ip", "unknown"),
                    "transport":    ctx.get("transport", "unknown"),
                }
            )
            return json.dumps({"error": str(e)})

    return await asyncio.to_thread(_run)


@mcp.tool(output_schema=None, timeout=600)
async def uqi_qec_apply(
    algorithm_file: str,
    apply_code:     str = "bit_flip",
    qpu_name:       str = "ibm_fez",
    shots:          int = 256,
) -> str:
    """QEC 코드 적용 및 효과 측정. apply_code: bit_flip|phase_flip"""
    _check_err = _safe_file_check(algorithm_file, tool="uqi_qec_apply")
    if _check_err:
        return json.dumps({"error": _check_err})
    qpu_name = _resolve_qpu(algorithm_file, qpu_name)
    if qpu_name in _PHOTONIC_QPUS:
        return json.dumps({
            "error": (
                f"QEC apply is not supported for photonic QPUs ({qpu_name}). "
                "Qubit-based QEC encodings (bit-flip, phase-flip) cannot be applied to "
                "photonic circuits. Photonic error mitigation relies on different techniques "
                "such as photon-number-resolving detection and boson sampling fidelity checks."
            ),
            "photonic": True,
        })

    def _run():
        from qiskit import QuantumCircuit
        try:
            _cache_key = f"qec_apply:{_file_hash(algorithm_file)}:{qpu_name}:{apply_code}:{shots}"
            _cached = _rag.get_cache(_cache_key)
            if _cached:
                print(f"  [Cache] qec_apply 캐시 히트: {Path(algorithm_file).name}", file=sys.stderr)
                try:
                    _cached_obj = json.loads(_cached)
                    _cached_obj["_cached"] = True
                    return json.dumps(_cached_obj, ensure_ascii=False)
                except Exception:
                    return _cached

            extractor, converter, framework = _extract_and_convert(algorithm_file)
            calibration = _get_calibration(qpu_name)
            qec         = UQIQEC(calibration=calibration)
            if qpu_name not in _noise_cache:
                _noise_cache[qpu_name] = UQINoise(qpu_name, calibration)
            noise   = _noise_cache[qpu_name]
            results = {}
            for name, qasm in converter.qasm_results.items():
                try:
                    qc  = QuantumCircuit.from_qasm_str(qasm)
                    cmp = qec.compare_fidelity(qc, apply_code, qpu_name, shots=shots, noise=noise)
                    _rag.add_qec_experiment(
                        circuit_name=name, qpu_name=qpu_name, code=apply_code,
                        before=cmp["before"], after=cmp["after"],
                        improvement=cmp["improvement"], overhead=cmp["overhead"],
                    )
                    results[name] = {
                        "code":        apply_code,
                        "improvement": cmp["improvement"],
                        "overhead":    cmp["overhead"],
                        "effective":   cmp["improvement"] > 0,
                    }
                except Exception as e:
                    results[name] = {"error": str(e)}

            _result = _safe_json({"qpu_name": qpu_name, "results": results})
            if not results:
                ctx = _request_context.get()
                _rag.add_pipeline_issue(
                    stage="uqi_qec_apply",
                    sdk=framework,
                    issue="QEC apply failed — results empty (circuit extraction or conversion failure)",
                    solution="",
                    qpu_name=qpu_name,
                    severity="error",
                    extra={
                        "algorithm_file": algorithm_file,
                        "client_ip":  ctx.get("client_ip", "unknown"),
                        "transport":  ctx.get("transport", "unknown"),
                    }
                )
            _rag.set_cache(_cache_key, _result)
            return _result
        except Exception as e:
            ctx = _request_context.get()
            _rag.add_pipeline_issue(
                stage="uqi_qec_apply",          # 예: "uqi_analyze", "uqi_optimize" 등
                sdk=framework if 'framework' in dir() else "",
                issue=str(e),
                solution="",
                qpu_name=qpu_name if 'qpu_name' in dir() else "",
                severity="error",
                extra={
                    "traceback":    traceback.format_exc(),
                    "algorithm_file": algorithm_file if 'algorithm_file' in dir() else "",
                    "client_ip":    ctx.get("client_ip", "unknown"),
                    "transport":    ctx.get("transport", "unknown"),
                }
            )
            return json.dumps({"error": str(e)})

    return await asyncio.to_thread(_run)


# ─────────────────────────────────────────────────────────
# 툴 4.5: GPU 벤치마크
# ─────────────────────────────────────────────────────────

@mcp.tool(output_schema=None, timeout=1800)
async def uqi_gpu_benchmark(
    algorithm_file: str,
) -> str:
    """CPU vs GPU 시뮬레이션 성능 비교. 워밍업 포함, 결과 RAG 저장."""
    _check_err = _safe_file_check(algorithm_file, tool="uqi_gpu_benchmark")
    if _check_err:
        return json.dumps({"error": _check_err})

    def _run():
        from uqi_extractor      import UQIExtractor
        from uqi_gpu_benchmark  import run_benchmark
        try:
            _cache_key = f"gpu_benchmark:{_file_hash(algorithm_file)}"
            _cached = _rag.get_cache(_cache_key)
            if _cached:
                print(f"  [Cache] gpu_benchmark 캐시 히트: {Path(algorithm_file).name}", file=sys.stderr)
                try:
                    _cached_obj = json.loads(_cached)
                    _cached_obj["_cached"] = True
                    return json.dumps(_cached_obj, ensure_ascii=False)
                except Exception:
                    return _cached

            extractor = UQIExtractor(algorithm_file)
            framework = extractor.detect_framework()
            fw_map = {
                "PennyLane": "PennyLane", "Qiskit": "Qiskit",
                "Qrisp": "Qrisp", "CUDAQ": "CUDAQ", "Perceval": "Perceval",
            }
            frameworks = [fw_map.get(framework, framework)]
            print(f"  [GPU Benchmark] 파일: {algorithm_file}")
            print(f"  [GPU Benchmark] Framework: {frameworks}")
            result = run_benchmark(algorithm_file=algorithm_file, frameworks=frameworks)
            _rag.add_gpu_benchmark(
                circuit_name=Path(algorithm_file).stem,
                framework=frameworks[0] if frameworks else "",
                gpu_available=result['gpu_available'],
                gpu_accelerated=result['gpu_accelerated'],
                cpu_time_sec=result['cpu_time_sec'],
                cpu_status=result['cpu_status'],
                gpu_time_sec=result['gpu_time_sec'],
                gpu_status=result['gpu_status'],
                speedup=result['speedup'],
                verdict=result['verdict'],
                cpu_error=result.get('cpu_error'),
                gpu_error=result.get('gpu_error'),
            )
            _result = _safe_json(result)
            _rag.set_cache(_cache_key, _result)
            return _result
        except Exception as e:
            ctx = _request_context.get()
            _rag.add_pipeline_issue(
                stage="uqi_gpu_benchmark",          # 예: "uqi_analyze", "uqi_optimize" 등
                sdk=framework if 'framework' in dir() else "",
                issue=str(e),
                solution="",
                qpu_name="",
                severity="error",
                extra={
                    "traceback":    traceback.format_exc(),
                    "algorithm_file": algorithm_file if 'algorithm_file' in dir() else "",
                    "client_ip":    ctx.get("client_ip", "unknown"),
                    "transport":    ctx.get("transport", "unknown"),
                }
            )
            return json.dumps({"error": str(e)})

    return await asyncio.to_thread(_run)


# ─────────────────────────────────────────────────────────
# 툴 5: 지식베이스 검색
# ─────────────────────────────────────────────────────────

@mcp.tool()
async def uqi_rag_search(
    query_type:  str,
    query:       str = "",
    qpu_name:    str = "",
    sdk:         str = "",
    num_qubits:  int = 0,
    total_gates: int = 0,
    limit:       int = 10,
) -> str:
    """지식베이스 검색. query_type: best_combination|pipeline_issues|transpile_patterns|qec_results|recent|stats"""
    def _run():
        try:
            if query_type == "stats":
                return _safe_json(_rag.stats())
            elif query_type == "best_combination":
                rows = _rag.search_best_combination(num_qubits, qpu_name or "", limit)
                return _safe_json(rows)
            elif query_type == "suspicious_optimizations":
                rows = _rag.search_suspicious_optimizations(qpu_name or "", limit)
                return _safe_json(rows)
            elif query_type == "pipeline_issues":
                records = _rag.search_pipeline_issues(sdk=sdk or None)
                return _safe_json([{**r["data"], "_ts": r["timestamp"]} for r in records[:limit]])
            elif query_type == "transpile_patterns":
                records = _rag.search_transpile_patterns(sdk=sdk or None)
                return _safe_json([{**r["data"], "_ts": r["timestamp"]} for r in records[:limit]])
            elif query_type == "qec_results":
                records = _rag.search_qec_results(qpu_name=qpu_name or None)
                return _safe_json([{**r["data"], "_ts": r["timestamp"]} for r in records[:limit]])
            elif query_type == "gpu_benchmark":
                records = _rag.search(record_type="gpu_benchmark", limit=limit)
                return _safe_json([{**r["data"], "_ts": r["timestamp"]} for r in records])
            elif query_type == "noise_simulation":
                records = _rag.search(record_type="execution", limit=limit * 10)
                result = []
                for r in records:
                    d = r["data"]
                    backend = d.get("backend", "")
                    if not backend.startswith("noise_sim"):
                        continue
                    if qpu_name and d.get("qpu_name") != qpu_name:
                        continue
                    comp = d.get("comparison") or {}
                    alg = d.get("algorithm_file") or ""
                    result.append({
                        "circuit_name":    d.get("circuit_name"),
                        "algorithm_file":  alg,
                        "qpu_name":        d.get("qpu_name"),
                        "sdk":             backend.replace("noise_sim_", ""),
                        "shots":           d.get("shots"),
                        "fidelity":        comp.get("fidelity"),
                        "tvd":             comp.get("tvd"),
                        "dominant_ideal":  comp.get("dominant_a"),
                        "dominant_noise":  comp.get("dominant_b"),
                        "_ts":             r["timestamp"],
                    })
                    if len(result) >= limit:
                        break
                return _safe_json(result)
            elif query_type == "semantic":
                if not query:
                    return json.dumps({"error": MCP_SEMANTIC_NO_QUERY})
                records = _rag.search_semantic(query, limit=limit)
                return _safe_json([{
                    "id":         r["id"],
                    "type":       r["type"],
                    "timestamp":  r["timestamp"],
                    "similarity": r.get("similarity"),
                    "data":       r["data"],
                } for r in records])
            elif query_type == "security_block":
                records = _rag.search(record_type="security_block", limit=limit)
                return _safe_json([{
                    "id": r["id"], "type": r["type"],
                    "timestamp": r["timestamp"],
                    "summary": str(r["data"])[:100],
                    "data": r["data"],
                    "tags": r["tags"],
                } for r in records])
            elif query_type == "recent":
                records = _rag.search(limit=limit)
                return _safe_json([{
                    "id": r["id"], "type": r["type"],
                    "timestamp": r["timestamp"],
                    "summary": str(r["data"])[:100],
                    "data": r["data"],
                    "tags": r["tags"],
                } for r in records])
            else:
                return json.dumps({
                    "error": mcp_unsupported_query_type(query_type),
                    "supported": ["best_combination", "pipeline_issues",
                                  "transpile_patterns", "qec_results", "recent", "stats"]
                })
        except Exception as e:
            return json.dumps({"error": str(e)})

    return await asyncio.to_thread(_run)


# ─────────────────────────────────────────────────────────
# 툴 6: 캘리브레이션 조회
# ─────────────────────────────────────────────────────────

def _build_calibration_response(calibration: dict, qpu_name: str, detail: bool = False) -> str:
    """캘리브레이션 dict → JSON 문자열 변환.
    detail=False(기본): summary avg 데이터만 (~400B).
    detail=True: per-qubit 상세 데이터 포함 (~22KB, Deep Analysis / QPU 상세보기용).
    """
    if cal_bg := calibration.get('basis_gates'):
        if isinstance(cal_bg, list):
            calibration = dict(calibration)
            calibration['basis_gates'] = [g for g in cal_bg if g in _QISKIT_STD_GATES]
    result = {
        "qpu_name":        qpu_name,
        "num_qubits":      calibration.get("num_qubits"),
        "basis_gates":     calibration.get("basis_gates"),
        "avg_t1_ms":       calibration.get("avg_t1_ms"),
        "avg_t2_ms":       calibration.get("avg_t2_ms"),
        "avg_1q_ns":       calibration.get("avg_1q_ns"),
        "avg_2q_ns":       calibration.get("avg_2q_ns"),
        "avg_1q_error":    calibration.get("avg_1q_error"),
        "avg_2q_error":    calibration.get("avg_2q_error"),
        "avg_ro_error":    calibration.get("avg_ro_error"),
        "last_updated":    calibration.get("last_updated"),
    }
    if detail:
        result.update({
            "coupling_map":    calibration.get("coupling_map"),
            "qubit_positions": calibration.get("qubit_positions"),
            "qubit_t1_ms":     calibration.get("qubit_t1_ms"),
            "qubit_t2_ms":     calibration.get("qubit_t2_ms"),
            "qubit_ro_error":  calibration.get("qubit_ro_error"),
            "qubit_1q_error":  calibration.get("qubit_1q_error"),
            "edge_2q_error":   calibration.get("edge_2q_error"),
        })
    result.update({
        # neutral atom 전용
        "rabi_freq_max_mhz":     calibration.get("rabi_freq_max_mhz"),
        "rydberg_level":         calibration.get("rydberg_level"),
        "min_atom_distance_um":  calibration.get("min_atom_distance_um"),
        "max_radial_distance_um":calibration.get("max_radial_distance_um"),
        "c6_coefficient":        calibration.get("c6_coefficient"),
        # photonic 전용
        "max_mode_count":        calibration.get("max_mode_count"),
        "max_photon_count":      calibration.get("max_photon_count"),
        "avg_transmittance":     calibration.get("avg_transmittance"),
        "avg_hom":               calibration.get("avg_hom"),
        "avg_g2":                calibration.get("avg_g2"),
        "clock_mhz":             calibration.get("clock_mhz"),
        # quantinuum 전용
        "memory_error":          calibration.get("memory_error"),
        "quantum_volume":        calibration.get("quantum_volume"),
        "noise_date":            calibration.get("noise_date"),
    })
    return _safe_json(result)


@mcp.tool()
async def uqi_calibration_info(
    qpu_name: str  = "ibm_fez",
    refresh:  bool = False,
    detail:   bool = False,
) -> str:
    """QPU 캘리브레이션 조회. qpu_name: ibm_fez|iqm_garnet, refresh: 갱신 여부, detail: per-qubit 데이터 포함"""
    def _build_response(calibration: dict) -> str:
        return _build_calibration_response(calibration, qpu_name, detail)

    def _run():
        import concurrent.futures as _cf

        def _do_sync():
            _cal.sync(qpu_name)

        def _fire_background_sync():
            """non-blocking: sync를 데몬 스레드에서 실행 (hang해도 응답 지연 없음)."""
            _exe = _cf.ThreadPoolExecutor(max_workers=1)
            _exe.submit(_do_sync)
            _exe.shutdown(wait=False)

        def _blocking_sync(timeout_sec: int):
            """blocking: sync 완료/timeout까지 대기 후 반환."""
            _exe = _cf.ThreadPoolExecutor(max_workers=1)
            try:
                _f = _exe.submit(_do_sync)
                try:
                    _f.result(timeout=timeout_sec)
                except _cf.TimeoutError:
                    print(f"  [CalInfo] {qpu_name} sync timeout ({timeout_sec}s)")
            finally:
                _exe.shutdown(wait=False)  # hang 중인 스레드 기다리지 않음

        try:
            calibration = _cal.data.get(qpu_name, {})

            if refresh:
                if calibration:
                    # 캐시 있음 → 즉시 반환, 만료됐으면 백그라운드 sync (non-blocking)
                    # IBM/Rigetti API가 느려도 UI가 block되지 않는다
                    if _cal._is_expired(qpu_name):
                        print(f"  [CalInfo] {qpu_name} TTL 만료 — 백그라운드 sync 트리거")
                        _fire_background_sync()
                    else:
                        print(f"  [CalInfo] {qpu_name} TTL 유효 — 캐시 반환")
                else:
                    # 캐시 없음 → blocking sync (돌려줄 데이터가 없으므로)
                    print(f"  [CalInfo] {qpu_name} 캐시 없음(refresh) — sync 대기 (max 15s)")
                    _blocking_sync(15)
                    calibration = _cal.data.get(qpu_name, {})
            else:
                if not calibration:
                    # refresh=False + 캐시 없음 → 최초 1회 blocking sync
                    print(f"  [CalInfo] {qpu_name} 캐시 없음 — 최초 동기화 시도 (max 10s)")
                    _blocking_sync(10)
                    calibration = _cal.data.get(qpu_name, {})

            if not calibration:
                return json.dumps({"error": mcp_no_calibration(qpu_name)})
            return _build_response(calibration)
        except Exception as e:
            return json.dumps({"error": str(e)})

    return await asyncio.to_thread(_run)

@mcp.tool()
async def uqi_all_calibrations() -> str:
    """전체 QPU 캘리브레이션 일괄 조회. 웹앱 초기 로딩용."""
    def _run():
        results = {}
        for qpu in SUPPORTED_QPUS:
            if qpu.startswith('sim:') or qpu.startswith('qpu:'):
                results[qpu] = {'qpu_name': qpu, 'type': 'photonic'}
                continue
            try:
                cal = _get_calibration(qpu)
                if cal:
                    _vendor = cal.get('vendor') or (
                        'iqm' if 'iqm' in qpu else
                        'ionq' if 'ionq' in qpu else
                        'rigetti' if 'rigetti' in qpu else
                        'quera' if 'quera' in qpu else
                        'ibm'
                    )
                    results[qpu] = {
                        'qpu_name':          qpu,
                        'num_qubits':        cal.get('num_qubits'),
                        'avg_t1_ms':         cal.get('avg_t1_ms'),
                        'avg_t2_ms':         cal.get('avg_t2_ms'),
                        'avg_1q_ns':         cal.get('avg_1q_ns'),
                        'avg_2q_ns':         cal.get('avg_2q_ns'),
                        'avg_1q_error':      cal.get('avg_1q_error'),
                        'avg_2q_error':      cal.get('avg_2q_error'),
                        'avg_ro_error':      cal.get('avg_ro_error'),
                        'basis_gates':       cal.get('basis_gates'),
                        'coupling_map':      cal.get('coupling_map'),
                        'last_updated':      cal.get('last_updated'),
                        'type':              _vendor,
                        # QuEra 전용 필드
                        'c6_coefficient':    cal.get('c6_coefficient'),
                        'rabi_freq_max_mhz': cal.get('rabi_freq_max_mhz'),
                        # per-qubit 분포 데이터
                        'qubit_t1_ms':       cal.get('qubit_t1_ms'),
                        'qubit_t2_ms':       cal.get('qubit_t2_ms'),
                        'qubit_ro_error':    cal.get('qubit_ro_error'),
                        'qubit_1q_error':    cal.get('qubit_1q_error'),
                        'edge_2q_error':     cal.get('edge_2q_error'),
                    }
                else:
                    results[qpu] = {'qpu_name': qpu, 'error': 'no calibration data'}
            except Exception as e:
                results[qpu] = {'qpu_name': qpu, 'error': str(e)}
        return _safe_json({'qpus': SUPPORTED_QPUS, 'calibrations': results})

    return await asyncio.to_thread(_run)

@mcp.tool()
async def uqi_submit_progress(submission_id: str) -> str:
    """백그라운드 QPU 제출 진행상황 조회. submission_id는 uqi_qpu_submit(confirmed=True) 반환값."""
    prog = _submission_progress.get(submission_id)
    if prog is None:
        return json.dumps({"error": MCP_SUBMISSION_NOT_FOUND, "submission_id": submission_id})
    return json.dumps(prog, ensure_ascii=False)


@mcp.tool()
async def uqi_list_qpus() -> str:
    """지원 QPU 목록 반환"""
    return json.dumps({"qpus": SUPPORTED_QPUS})


@mcp.tool()
async def uqi_detect_framework(algorithm_file: str) -> str:
    """알고리즘 파일의 framework를 감지하고 호환 QPU 목록 반환"""
    _check_err = _safe_file_check(algorithm_file, tool="uqi_detect_framework")
    if _check_err:
        return json.dumps({"error": _check_err})

    def _run():
        from uqi_extractor import UQIExtractor
        try:
            extractor = UQIExtractor(algorithm_file)
            framework = extractor.detect_framework()
            mapping = _FRAMEWORK_QPU_MAP.get(framework, {
                "qpus": SUPPORTED_QPUS, "default": "ibm_fez"
            })
            return json.dumps({
                "ok": True,
                "framework": framework,
                "compatible_qpus": mapping["qpus"],
                "default_qpu": mapping["default"],
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)})

    return await asyncio.to_thread(_run)


# ─────────────────────────────────────────────────────────
# 툴 6.5: 알고리즘 파일 업로드
# ─────────────────────────────────────────────────────────

ALG_FILES_DIR = Path(__file__).parent.parent / "alg-files"

@mcp.tool()
async def uqi_upload_algorithm(
    filename: str,
    content:  str,
) -> str:
    """알고리즘 파일을 DGX에 저장. filename: .py 파일명, content: 파일 내용"""
    def _run():
        try:
            ALG_FILES_DIR.mkdir(parents=True, exist_ok=True)
            if not filename.endswith(".py"):
                return json.dumps({"error": MCP_FILE_EXT_NOT_ALLOWED})
            safe_name = Path(filename).name
            dest = ALG_FILES_DIR / safe_name
            if dest.exists():
                return json.dumps({"error": f"FILE_EXISTS:{safe_name}", "conflict": True})
            dest.write_text(content, encoding="utf-8")
            return _safe_json({
                "ok": True, "path": str(dest),
                "filename": safe_name, "size": len(content),
            })
        except Exception as e:
            return json.dumps({"error": str(e)})

    return await asyncio.to_thread(_run)


# ─────────────────────────────────────────────────────────
# 툴 9: 파일 목록 / 읽기
# ─────────────────────────────────────────────────────────
@mcp.tool()
async def uqi_invalidate_cache(
    algorithm_file: str,
    qpu_name:       str = "",
    stage:          str = "all",
) -> str:
    """파이프라인 캐시 무효화. stage: all|analyze|optimize|noise|qec_analyze|qec_apply|gpu_benchmark|qpu_submit"""
    def _run():
        import hashlib
        try:
            path = Path(algorithm_file)
            if not path.exists():
                return json.dumps({"error": mcp_file_not_found(algorithm_file)})
            h = hashlib.md5(path.read_bytes()).hexdigest()[:12]

            # 웹앱 step 이름 → 서버 stage 키 정규화
            _stage_alias = {
                "qec-analyze":    "qec_analyze",
                "qec-apply":      "qec_apply",
                "gpu":            "gpu_benchmark",
                "gpu-benchmark":  "gpu_benchmark",
                "qpu_submit":     "qpu_submit",
            }
            stage_norm = _stage_alias.get(stage, stage)

            stages = {
                "analyze":     [f"analyze:{h}:{qpu_name or 'ibm_fez'}", f"analyze:{h}:iqm_garnet", f"extract:{h}"],
                "optimize":    [f"optimize:{h}:{qpu_name or 'ibm_fez'}:auto", f"optimize:{h}:{qpu_name or 'ibm_fez'}:qiskit+sabre",
                                f"optimize:{h}:{qpu_name or 'ibm_fez'}:tket+sabre"],
                "noise":       [f"noise_v2:{h}:{qpu_name or 'ibm_fez'}:1024", f"noise_v2:{h}:{qpu_name or 'ibm_fez'}:256",
                                f"noise:{h}:{qpu_name or 'ibm_fez'}:1024", f"noise:{h}:{qpu_name or 'ibm_fez'}:256"],
                "qec_analyze": [f"qec_analyze:{h}:{qpu_name or 'ibm_fez'}:1024"],
                "qec_apply":   [f"qec_apply:{h}:{qpu_name or 'ibm_fez'}:bit_flip:256",
                                f"qec_apply:{h}:{qpu_name or 'ibm_fez'}:phase_flip:256"],
                "gpu_benchmark":[f"gpu_benchmark:{h}"],
                "qpu_submit":  [f"qpu_submit:{h}:{qpu_name or 'auto'}:1024"],
            }

            if stage_norm == "all":
                keys = [k for ks in stages.values() for k in ks]
                # extract 캐시도 포함
                keys.append(f"extract:{h}")
            else:
                keys = stages.get(stage_norm, [])

            # stage별 LIKE 패턴 (버전 prefix 변경 대응)
            stage_prefixes = {
                "analyze":      [f"analyze:{h}", f"extract:{h}"],
                "optimize":     [f"optimize:{h}"],
                "noise":        [f"noise:{h}", f"noise_v2:{h}"],
                "qec_analyze":  [f"qec_analyze:{h}"],
                "qec_apply":    [f"qec_apply:{h}"],
                "gpu_benchmark":[f"gpu_benchmark:{h}"],
                "qpu_submit":   [f"qpu_submit:{h}"],
            }

            import sqlite3
            conn = sqlite3.connect(_rag.cache_file, timeout=5.0)
            deleted = 0
            try:
                for k in keys:
                    cur = conn.execute("DELETE FROM cache WHERE key=?", (k,))
                    deleted += cur.rowcount
                # LIKE로 해시 기반 삭제 (버전/QPU/shots 변형 포함)
                if stage_norm == "all":
                    cur = conn.execute("DELETE FROM cache WHERE key LIKE ?", (f"%{h}%",))
                    deleted += cur.rowcount
                else:
                    for prefix in stage_prefixes.get(stage_norm, []):
                        cur = conn.execute("DELETE FROM cache WHERE key LIKE ?", (f"{prefix}%",))
                        deleted += cur.rowcount
                conn.commit()
            finally:
                conn.close()

            return json.dumps({"ok": True, "deleted": deleted, "hash": h, "stage": stage})
        except Exception as e:
            return json.dumps({"error": str(e)})

    return await asyncio.to_thread(_run)

@mcp.tool()
async def uqi_list_files() -> str:
    """DGX alg-files 디렉토리의 알고리즘 파일 목록 반환"""
    def _run():
        alg_dir = ALG_FILES_DIR
        if not alg_dir.exists():
            return json.dumps({"error": mcp_dir_not_found(str(alg_dir))})
        files = []
        for p in sorted(alg_dir.glob("*.py")):
            if p.name.startswith("_") or p.name.startswith("test_block_"):
                continue
            files.append({"name": p.name, "path": str(p), "size_kb": round(p.stat().st_size / 1024, 1)})
        return _safe_json({"files": files, "count": len(files)})

    return await asyncio.to_thread(_run)


@mcp.tool()
async def uqi_read_file(algorithm_file: str) -> str:
    """알고리즘 파일 내용 read-only 조회"""
    def _run():
        path = Path(algorithm_file)
        if not path.exists():
            return json.dumps({"error": mcp_file_not_found(algorithm_file)})
        if path.suffix != ".py":
            return json.dumps({"error": MCP_FILE_ONLY_PY})
        if path.stat().st_size > 1 * 1024 * 1024:
            return json.dumps({"error": MCP_FILE_TOO_LARGE})
        try:
            content = path.read_text(encoding="utf-8")
            return _safe_json({"name": path.name, "path": str(path), "content": content})
        except Exception as e:
            return json.dumps({"error": str(e)})

    return await asyncio.to_thread(_run)


# ─────────────────────────────────────────────────────────
# QPU 제출 확인 메시지 빌더 (데이터에서 메시지 재생성 — i18n 대응)
# 캐시에는 구조화 데이터만 저장, 메시지는 이 함수로 생성
# ─────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────
# 비용 안전장치 (cost_safeguard)
# ─────────────────────────────────────────────────────────────
# 정책:
#   - admin_override=True (webapp admin 이스터에그) → 무조건 통과
#   - estimated_usd ≥ COST_THRESHOLD_USD (default $50) → 차단
#   - confidence == "verify_required" / "unknown" → 차단 (비용 추정 불가)
#   - 그 외 (free, credits, hqc 무료 한도 내) → 통과
# 차단 메시지: "관리자 컨택" 안내 (이메일 표기 안 함)

COST_SAFEGUARD_THRESHOLD_USD = 50.0


def _check_cost_safeguard(qpu_name: str, shots: int,
                          admin_override: bool = False) -> dict | None:
    """비용 안전장치 검사.

    Returns:
        None : 통과 (제출 허용)
        dict : 차단 정보 (error, blocked_by, reason, message 등)
    """
    if admin_override:
        return None

    try:
        from uqi_pricing import estimate_cost
        cost = estimate_cost(qpu_name, shots)
    except Exception as e:
        # estimate_cost 자체 실패 → 보수적으로 차단
        return {
            "error":      "🛡️ 제출 차단됨 — 비용 안전장치 검사 오류",
            "blocked_by": "cost_safeguard_error",
            "detail":     str(e),
            "qpu":        qpu_name,
            "shots":      shots,
            "message": (
                "🛡️ 비용 안전장치 검사 중 오류 발생.\n"
                "안전을 위해 제출이 차단되었습니다.\n"
                "관리자에게 문의해주세요."
            ),
        }

    threshold = COST_SAFEGUARD_THRESHOLD_USD
    blocked_reason = None
    est_usd = cost.get("estimated_usd")
    confidence = cost.get("confidence")

    if est_usd is not None and est_usd >= threshold:
        blocked_reason = f"예상 비용 ${est_usd:.2f} ≥ 임계값 ${threshold:.0f}"
    elif confidence in ("verify_required", "unknown"):
        blocked_reason = f"비용 추정 불가 (confidence={confidence})"

    if not blocked_reason:
        return None

    return {
        "error":         "🛡️ 제출 차단됨 — 비용 안전장치",
        "blocked_by":    "cost_safeguard",
        "reason":        blocked_reason,
        "qpu":           qpu_name,
        "shots":         shots,
        "estimated_usd": est_usd,
        "cost_details":  cost.get("details"),
        "message": (
            "🛡️ 제출 차단됨 — 비용 안전장치\n\n"
            f"  QPU:         {qpu_name}\n"
            f"  Shots:       {shots}\n"
            f"  예상 비용:    {cost.get('details','-')}\n"
            f"  차단 사유:    {blocked_reason}\n\n"
            "이 QPU 제출은 관리자 권한이 필요합니다.\n"
            "실행이 필요하면 회로 / shot 수 / 예상 비용 정보와 함께\n"
            "관리자에게 문의해주세요."
        ),
    }


def _build_qpu_submit_message(d: dict) -> str:
    """캐시 데이터 또는 분析 결과 dict에서 QPU 제출 확인 메시지 생성"""
    selected_qpu    = d.get("selected_qpu", "")
    recommended_qpu = d.get("recommended_qpu", "")
    avg_fidelity    = d.get("avg_fidelity")
    total_cost      = d.get("total_cost") or 0
    shots           = d.get("shots", 0)
    qubit_warnings  = d.get("qubit_warnings", [])
    disadvantages   = d.get("disadvantages", [])
    t2_warnings     = d.get("t2_warnings", [])

    lines = [
        "⚠️  QPU 제출 확인 필요", "",
        f"선택 QPU:    {selected_qpu}",
        f"추천 QPU:    {recommended_qpu}",
        f"예상 Fidelity: {avg_fidelity}",
        f"예상 실행시간: {total_cost}ms ({round(total_cost/1000, 2)}s) × {shots} shots",
        "※ QPU 큐 대기시간 별도",
        f"shots:       {shots}",
    ]

    # 💰 비용 추정 (selected_qpu + shots 있으면 자동 계산)
    if selected_qpu and shots:
        try:
            from uqi_pricing import estimate_cost
            cost = estimate_cost(selected_qpu, shots)
            lines += ["", "💰 예상 비용:"]
            if cost.get("estimated_usd") is not None and cost["currency"] != "free":
                lines.append(
                    f"  {cost['details']}  →  ${cost['estimated_usd']} "
                    f"(~{cost['estimated_krw']:,}원)"
                )
            elif cost.get("estimated_credits") is not None:
                lines.append(f"  {cost['details']}")
            elif cost["currency"] == "free":
                lines.append(f"  무료 — {cost['details']}")
            elif cost["currency"] == "hqc":
                lines.append(f"  {cost['details']}")
            else:
                lines.append(f"  {cost['details']}")
            lines.append(f"  신뢰도: {cost['confidence']}")
            for w in cost.get("warnings", []):
                lines.append(f"  ⚠ {w}")
        except Exception as _ce:
            lines += ["", f"💰 비용 추정 실패: {_ce}"]

    # 🕐 디바이스 가용성 (Braket 디바이스 — AWS execution window)
    if selected_qpu and (
        selected_qpu.startswith("ionq_")
        or selected_qpu.startswith("rigetti_")
        or selected_qpu.startswith("quera_")
        or selected_qpu.startswith("braket_")
    ):
        try:
            from uqi_executor_braket import check_device_availability
            avail = check_device_availability(selected_qpu)
            lines += ["", "🕐 디바이스 가용성:"]
            lines.append(f"  {avail['message']}")
            if avail.get("device_status"):
                lines.append(f"  device_status: {avail['device_status']}")
            for w in avail.get("warnings", []):
                lines.append(f"  ⚠ {w}")
        except Exception as _ae:
            lines += ["", f"🕐 가용성 체크 실패: {_ae}"]

    # 🕐 디바이스 가용성 (Azure Quantum — target.current_availability)
    if selected_qpu and selected_qpu.startswith("pasqal_"):
        try:
            from uqi_executor_azure import check_device_availability_azure
            avail = check_device_availability_azure(selected_qpu)
            lines += ["", "🕐 디바이스 가용성 (Azure Quantum):"]
            lines.append(f"  {avail['message']}")
            if avail.get("device_status"):
                lines.append(f"  device_status: {avail['device_status']}")
            qt = avail.get("average_queue_time_sec")
            if qt is not None:
                lines.append(f"  average_queue_time: {qt}초")
            for w in avail.get("warnings", []):
                lines.append(f"  ⚠ {w}")
        except Exception as _ae:
            lines += ["", f"🕐 Azure 가용성 체크 실패: {_ae}"]

    # 🔄 캘리브레이션 데이터 입수 경로 (게이트웨이 경유 또는 정적 데이터 시 표시)
    if selected_qpu:
        try:
            from uqi_calibration import UQICalibration
            _cal = UQICalibration()
            _cal_entry = _cal.data.get(selected_qpu, {})
            _src = _cal_entry.get("data_source")
            if _src:
                _src_label = {
                    "aws_braket":            "AWS Braket 게이트웨이 경유",
                    "azure_quantum":         "Azure Quantum 게이트웨이 경유",
                    "pytket_offline_static": "정적 번들 (pytket-quantinuum OFFLINE)",
                }.get(_src, _src)
                lines += ["", "🔄 캘리브레이션 출처:"]
                lines.append(f"  {_src_label}")
                # 정적 데이터인 경우 신선도 평가 + 신뢰도 경고
                if _src == "pytket_offline_static":
                    nd = _cal_entry.get("noise_date")
                    if nd:
                        try:
                            from datetime import datetime as _dt
                            _d = _dt.fromisoformat(nd).date()
                            _today = _dt.now().date()
                            _days = (_today - _d).days
                            if _days > 365:
                                _months = _days // 30
                                lines.append(
                                    f"  ⚠ noise_date={nd} ({_months}개월 경과 — "
                                    f"⚠️ 신뢰도 낮음, 실 디바이스와 차이 클 수 있음)"
                                )
                            elif _days > 180:
                                lines.append(
                                    f"  ⚠ noise_date={nd} ({_days}일 경과 — "
                                    f"신뢰도 보통)"
                                )
                            else:
                                lines.append(f"  ⚠ noise_date={nd}")
                        except Exception:
                            lines.append(f"  ⚠ noise_date={nd}")
                    lines.append(
                        "  ⚠ 정적 데이터 — pytket-quantinuum 패키지 내장 (live 갱신 안 됨)"
                    )
                    lines.append(
                        "  ⚠ Live 데이터는 Quantinuum Nexus(qnexus) 계약 후 가능"
                    )
                    lines.append(
                        "  ⚠ 분석/추천 결과는 참고용 — 실 제출은 Nexus 통합 후 권장"
                    )
        except Exception:
            pass

    if qubit_warnings:
        lines += ["", "⚠️  큐비트 수 초과 (제출 불가):"]
        lines.extend(f"  • {w}" for w in qubit_warnings)
    if disadvantages:
        lines += ["", "⚠️  선택 QPU의 예상 불리한 점:"]
        lines.extend(f"  • {d_}" for d_ in disadvantages)
    if t2_warnings:
        lines += ["", "⚠️  T2 코히어런스 경고:"]
        lines.extend(f"  • {w}" for w in t2_warnings)
    lines += ["", "제출하려면 confirmed=True로 다시 호출하세요."]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────
# 툴 7: QPU 제출 (Human-in-the-loop)
# ─────────────────────────────────────────────────────────

@mcp.tool(timeout=600)
async def uqi_qpu_submit(
    algorithm_file: str,
    qpu_name:       str  = "auto",
    shots:          int  = 1024,
    confirmed:      bool = False,
    admin_override: bool = False,
) -> str:
    """QPU 제출 (Human-in-the-loop). confirmed=False: 예상 분석만, confirmed=True: 실제 제출. 비용 발생 주의. admin_override: webapp admin 모드에서만 활성, 비용 안전장치 우회용."""
    _check_err = _safe_file_check(algorithm_file, tool="uqi_qpu_submit")
    if _check_err:
        return json.dumps({"error": _check_err})
    qpu_name = _resolve_qpu(algorithm_file, qpu_name)

    # IonQ는 shot당 \$0.08 → 기본값 1024이면 task당 ~\$82. 사용자가 default를
    # 그대로 사용한 경우(=1024)에만 100으로 자동 다운조정. 명시적으로 다른 값을
    # 넘긴 경우는 사용자 의도 존중. (IonQ Forte-1 최소 100, 최대 5000)
    if qpu_name.startswith("ionq_") and shots == 1024:
        shots = 100

    # ── 비용 안전장치: confirmed=True 시점에서 검사 ──
    # admin_override=True (webapp admin 이스터에그) 시 우회.
    # 분석 단계(confirmed=False)는 모두 통과 — 누구나 비용 추정/회로 분석 가능.
    if confirmed:
        _block = _check_cost_safeguard(qpu_name, shots, admin_override)
        if _block:
            return json.dumps(_block, ensure_ascii=False)

    # ── AHS 분기 (Braket-AHS / Pulser) — 별도 path ──
    from uqi_pricing import _ANALOG_QPUS as _ANALOG_SET
    if qpu_name in _ANALOG_SET:
        return await asyncio.to_thread(
            _qpu_submit_ahs, algorithm_file, qpu_name, shots, confirmed)

    def _run():
        import contextlib
        from qiskit import QuantumCircuit
        try:
            _submit_cache_key = f"qpu_submit:{_file_hash(algorithm_file)}:{qpu_name}:{shots}"

            # ── analyze 캐시 확인 (confirmed=False: 즉시 리턴, confirmed=True: 분석 skip 후 제출) ──
            _cached = _rag.get_cache(_submit_cache_key)
            if _cached:
                try:
                    _cached_obj = json.loads(_cached)
                    if not confirmed:
                        print(f"  [Cache] qpu_submit analyze 캐시 히트: {Path(algorithm_file).name}", file=sys.stderr)
                        _cached_obj["_cached"] = True
                        # 캐시에 message가 없는 경우 구조화 데이터에서 재생성
                        if "message" not in _cached_obj:
                            _cached_obj["message"] = _build_qpu_submit_message(_cached_obj)
                        return json.dumps(_cached_obj, ensure_ascii=False)
                    else:
                        # confirmed=True: 캐시에서 selected_qpu 복원 후 분석 skip → 바로 제출
                        print(f"  [Cache] qpu_submit 분석 캐시 재사용, 제출 진행: {Path(algorithm_file).name}", file=sys.stderr)
                        _cached_selected_qpu = _cached_obj.get("selected_qpu", qpu_name)
                        # 제출에 필요한 converter만 준비
                        extractor, converter, framework = _extract_and_convert(algorithm_file)
                        selected_qpu    = _cached_selected_qpu if qpu_name == "auto" else qpu_name
                        recommended_qpu = _cached_obj.get("recommended_qpu", selected_qpu)
                        # qpu_circuits는 QASM에서 재빌드 (캐시에 circuit 객체 없음)
                        qpu_analysis  = {}
                        qpu_circuits  = {selected_qpu: {n: None for n in converter.qasm_results}}
                        # 제출 단계로 바로 점프
                        goto_submit = True
                except Exception:
                    goto_submit = False
            else:
                goto_submit = False

            # ── Perceval QPU: 전체 QPU 조회 없이 바로 처리 ──
            PERCEVAL_QPUS = ["qpu:ascella", "qpu:belenos", "sim:ascella", "sim:belenos"]
            if qpu_name in PERCEVAL_QPUS:
                extractor, converter, framework = _extract_and_convert(algorithm_file)

                if not confirmed:
                    from uqi_executor_perceval import UQIExecutorPerceval

                    # perceval_circuits에서 회로 분석 정보 추출
                    if not extractor.perceval_circuits:
                        extractor._extract_perceval_circuits()

                    # 회로 요구사항 계산
                    _max_circuit_modes = 0
                    _max_circuit_photons = 0
                    _pcvl_circuit_info = {}
                    for cname, entry in extractor.perceval_circuits.items():
                        unitary_data, is_list, num_modes = entry
                        n_photons = sum(is_list)
                        _max_circuit_modes = max(_max_circuit_modes, num_modes)
                        _max_circuit_photons = max(_max_circuit_photons, n_photons)
                        _pcvl_circuit_info[cname] = {
                            "num_qubits":   num_modes,
                            "total_gates":  1,
                            "depth":        1,
                            "fidelity":     None,
                            "combination":  f"Photonic · {num_modes} modes · {n_photons} photon{'s' if n_photons != 1 else ''}",
                        }

                    # ── 광자 QPU 비교 분석 ──
                    _ALL_PCVL_PLATFORMS = ["sim:ascella", "sim:belenos", "qpu:ascella", "qpu:belenos"]
                    _ptoken = os.getenv("QUANDELA_TOKEN")
                    _pcvl_comparison = {}
                    _best_qpu = qpu_name  # 기본값
                    _best_score = -1

                    for pqpu in _ALL_PCVL_PLATFORMS:
                        _cache_key_spec = f"pcvl_specs:{pqpu}"
                        _cached_spec = _rag.get_cache(_cache_key_spec)
                        if _cached_spec:
                            try:
                                spec = json.loads(_cached_spec)
                            except Exception:
                                spec = None
                        else:
                            spec = None

                        if not spec:
                            print(f"  [Perceval] 플랫폼 스펙 조회: {pqpu}", file=sys.stderr)
                            spec = UQIExecutorPerceval.get_platform_specs(pqpu, _ptoken)
                            try:
                                _rag.set_cache(_cache_key_spec, json.dumps(spec, ensure_ascii=False))
                            except Exception:
                                pass

                        is_sim = pqpu.startswith("sim:")
                        max_m = spec.get("max_modes", 12)
                        max_p = spec.get("max_photons", 6)
                        fits = _max_circuit_modes <= max_m and _max_circuit_photons <= max_p
                        available = spec.get("ok", False)

                        # 스코어: 실행 가능 + 실제 QPU 우선 + 가용성
                        score = 0.0
                        if fits:
                            score += 0.5
                        if available:
                            score += 0.3
                        if not is_sim:
                            score += 0.2
                        if score > _best_score:
                            _best_score = score
                            _best_qpu = pqpu

                        _pcvl_comparison[pqpu] = {
                            "recommended":     False,  # 아래에서 설정
                            "selected":        pqpu == qpu_name,
                            "online":          available,
                            "avg_fidelity":    1.0 if fits else None,
                            "composite_score": round(score, 3) if fits else 0.0,
                            "avg_2q_error":    None,
                            "avg_t2_ms":       None,
                            "total_exec_s":    None,
                            "pending_jobs":    None,
                            "queue_note":      f"{'Simulator' if is_sim else 'QPU'} · {max_m} modes · {max_p} photons"
                                               + ("" if fits else f" · ⚠ {_max_circuit_modes}m/{_max_circuit_photons}p 초과"),
                        }

                    if _best_qpu in _pcvl_comparison:
                        _pcvl_comparison[_best_qpu]["recommended"] = True

                    # analyze 결과를 submit 캐시에 저장 → 2번째 호출부터 즉시 반환
                    _pcvl_cache = {
                        "status":           STATUS_AWAITING_CONFIRMATION,
                        "selected_qpu":     qpu_name,
                        "recommended_qpu":  _best_qpu,
                        "shots":            shots,
                        "circuit_info":     _pcvl_circuit_info,
                        "qpu_comparison":   _pcvl_comparison,
                        "selection_note":   f"Photonic QPU ({qpu_name})",
                        "disadvantages":    [] if qpu_name == _best_qpu else [
                            f"추천 QPU는 {_best_qpu}입니다 (선택: {qpu_name})",
                        ],
                    }
                    _pcvl_cache["message"] = _build_qpu_submit_message(_pcvl_cache)
                    _rag.set_cache(_submit_cache_key, json.dumps(_pcvl_cache, ensure_ascii=False))
                    return _safe_json(_pcvl_cache)

                # confirmed=True → 실제 제출
                # 제출 직전 실시간 상태 확인 (캐시 우회, 3회 재시도)
                _live = live_check_qpu(qpu_name)
                if not _live["ok"]:
                    return json.dumps({
                        "error":    mcp_live_check_unreachable(qpu_name, _live["attempts"]),
                        "status":   "live_check_unreachable",
                        "qpu":      qpu_name,
                        "attempts": _live["attempts"],
                    }, ensure_ascii=False)
                if not _live["available"]:
                    _co = locals().get("_cached_obj")
                    _qpu_cmp = _co.get("qpu_comparison", {}) if isinstance(_co, dict) else {}
                    _alts = recommend_alternatives(qpu_name, _qpu_cmp)
                    return json.dumps({
                        "error":           mcp_qpu_offline_live(qpu_name, _live["status"]),
                        "status":          "qpu_offline",
                        "qpu":             qpu_name,
                        "current_status":  _live["status"],
                        "alternatives":    _alts,
                        "action_required": mcp_action_retry_or_cancel(),
                    }, ensure_ascii=False)

                # perceval_circuits: 유니터리+input_state 직렬화 데이터
                if not extractor.perceval_circuits:
                    print(f"  [Submit] Perceval 회로 재추출 (캐시 히트로 인한 빈 상태)",
                          file=sys.stderr)
                    extractor._extract_perceval_circuits()

                if not extractor.perceval_circuits:
                    return json.dumps({"error": "Perceval circuit extraction failed — no circuits found. "
                                                "Check that the algorithm file uses pcvl.Processor / pcvl.RemoteProcessor."})

                # ── 백그라운드 제출: IBM/IQM과 동일한 submission_id 방식 ──
                import uuid as _uuid, threading as _threading
                from uqi_executor_perceval import UQIExecutorPerceval

                _pcircuit_names = list(extractor.perceval_circuits.keys())
                sid = _uuid.uuid4().hex[:10]
                _submission_progress[sid] = {
                    "status":          "submitting",
                    "total":           len(_pcircuit_names),
                    "done":            0,
                    "selected_qpu":    qpu_name,
                    "recommended_qpu": qpu_name,
                    "shots":           shots,
                    "results":         {},
                }

                _perceval_entries = dict(extractor.perceval_circuits)
                _ptoken = os.getenv("QUANDELA_TOKEN")
                _use_sim = qpu_name.startswith("sim:")

                def _bg_perceval_submit(sid=sid):
                    prog = _submission_progress[sid]
                    _pexec = UQIExecutorPerceval(extractor=extractor, shots=shots)
                    _pexec._token = _ptoken
                    _pexec._platform_sim = qpu_name if _use_sim else "sim:ascella"
                    _pexec._platform_qpu = qpu_name if not _use_sim else "qpu:belenos"

                    for name in _pcircuit_names:
                        _saved_jid = {"id": None}  # on_submit 콜백과 공유

                        def _on_submit(jid, platform, _name=name):
                            # Quandela 에 job 생성된 직후 호출 — 즉시 로컬 DB에 기록
                            _saved_jid["id"] = jid
                            try:
                                _job_store.save_job(
                                    job_id=jid, qpu_name=qpu_name, circuit_name=_name, shots=shots,
                                    extra={"backend": qpu_name, "platform": platform},
                                )
                            except Exception as _se:
                                print(f"  [BgSubmit] save_job 실패({jid}): {_se}",
                                      file=sys.stderr)

                        try:
                            _entry = _perceval_entries[name]
                            _pcircuit, _pinput = UQIExecutorPerceval._restore_perceval_objects(_entry)
                            t_start = time.time()
                            result_dict = _pexec._run_single(
                                name=name, circuit=_pcircuit,
                                input_state=_pinput, use_simulator=_use_sim,
                                on_submit=_on_submit,
                            )
                            exec_time = time.time() - t_start
                            # 콜백에서 저장됐으면 그 id 사용, 아니면 fallback(UUID)
                            import uuid as _juuid
                            _jid = (_saved_jid["id"]
                                    or result_dict.get("cloud_job_id")
                                    or _juuid.uuid4().hex)
                            if not _saved_jid["id"]:
                                # 콜백 미실행 (제출 자체 실패 등) — 지금이라도 등록
                                _job_store.save_job(
                                    job_id=_jid, qpu_name=qpu_name, circuit_name=name, shots=shots,
                                    extra={"backend": qpu_name, "exec_time": round(exec_time, 2)},
                                )

                            if not result_dict["ok"]:
                                # 결과 실패 — error 상태로 마킹 후 예외 전파
                                _job_store.update_job(
                                    _jid, status="error",
                                    error=result_dict.get("error", "unknown"),
                                )
                                raise Exception(result_dict["error"])

                            _job_store.update_job(
                                _jid, status="done",
                                counts=result_dict["counts"],
                            )
                            _rag.add_execution(
                                circuit_name=name, qpu_name=qpu_name, backend=qpu_name,
                                shots=shots, counts=result_dict["counts"], ok=True,
                                exec_time_sec=exec_time,
                            )
                            prog["results"][name] = {
                                "ok": True, "counts": result_dict["counts"],
                                "backend": qpu_name, "exec_time": round(exec_time, 2),
                                "job_id": _jid,
                            }
                        except Exception as e:
                            # 콜백으로 이미 저장된 경우 error 상태 마킹 시도
                            if _saved_jid["id"]:
                                try:
                                    _job_store.update_job(
                                        _saved_jid["id"], status="error", error=str(e),
                                    )
                                except Exception:
                                    pass
                            prog["results"][name] = {
                                "ok": False, "error": str(e),
                                "job_id": _saved_jid["id"],
                            }
                            _rag.add_execution(
                                circuit_name=name, qpu_name=qpu_name, backend=qpu_name,
                                shots=shots, counts={}, ok=False,
                                extra={"error": str(e)},
                            )
                        prog["done"] += 1
                    prog["status"] = "completed"

                _threading.Thread(target=_bg_perceval_submit, daemon=True).start()
                return _safe_json({
                    "status":          "submitting",
                    "submission_id":   sid,
                    "total":           len(_pcircuit_names),
                    "selected_qpu":    qpu_name,
                    "recommended_qpu": qpu_name,
                    "shots":           shots,
                })

            # ── 비-Perceval QPU (IBM/IQM 등) ──
            if not goto_submit:
                extractor, converter, framework = _extract_and_convert(algorithm_file)

                available_qpus = _get_available_qpus_cached()

                if qpu_name != "auto":
                    # analyze 단계: 캐시 기반 사전 필터 (실제 submit 직전에 live check 재수행)
                    qpu_status = _get_qpu_status_cached()
                    s = qpu_status.get(qpu_name, {})
                    if not s.get("available", True):
                        return json.dumps({
                            "error": mcp_qpu_offline(qpu_name),
                            "queue_note": s.get("note", ""),
                        })
            # goto_submit=True 경로는 캐시 우회 — 실제 제출 직전 live_check_qpu() 가 최종 판정

            if not goto_submit:
                # analyze 단계 캐시 키 (confirmed=True일 때 저장용)
                _submit_cache_key = f"qpu_submit:{_file_hash(algorithm_file)}:{qpu_name}:{shots}"
    
                qpu_analysis = {}
                qpu_circuits = {}
    
                stage1_results = {}
                base_optimizer = UQIOptimizer(calibration={})
                for name, qasm in converter.qasm_results.items():
                    try:
                        qc = QuantumCircuit.from_qasm_str(qasm)
                        stage1_results[name] = {"qc": qc, "stage1": base_optimizer.optimize_stage1(qc)}
                    except Exception as e:
                        stage1_results[name] = {"error": str(e)}
    
                # ── Phase 1: 모든 QPU에 대해 캘리브레이션 기반 분석 (노이즈 시뮬 없음) ──
                # 아날로그, 포토닉 장비는 Qiskit 트랜스파일 불가 → 스킵
                # IBM/IQM/Braket(IonQ/Rigetti) 비동기 submit 지원.
                _SKIP_SUBMIT_QPUS = {
                    # AHS analog — gate 회로 비호환 (회로 형식 불일치)
                    'quera_aquila',
                    # Pasqal Fresnel — Pulser pulse program 입력 (Qiskit gate 회로 비호환)
                    # Azure target API: input_data_format='pasqal.pulser.v1'
                    # 향후 Pulser 알고리즘 워크플로우 지원 시 SKIP에서 제거
                    'pasqal_fresnel', 'pasqal_fresnel_can1',
                    # Quantinuum — 자사 클라우드(Nexus) 통합 전까지 submit 차단
                    # (분석/추천은 가능, 단 캘리브레이션은 정적 OFFLINE 데이터)
                    'quantinuum_h2_1', 'quantinuum_h2_2', 'quantinuum_h1_1',
                }
                for qpu in available_qpus:
                    if qpu.startswith("sim:") or qpu.startswith("qpu:"):
                        continue
                    if qpu in _SKIP_SUBMIT_QPUS:
                        sys.stderr.write(f"  [Submit] {qpu} 스킵: 아날로그 장비 (Qiskit 트랜스파일 불가)\n")
                        continue
                    calibration = _get_calibration(qpu)
                    if not calibration:
                        continue
    
                    # 회로 큐비트 수가 QPU 최대 큐비트 수 초과 시 스킵
                    device_qubits = calibration.get("num_qubits", 0)
                    max_circuit_qubits = max(
                        (s1["qc"].num_qubits for s1 in stage1_results.values() if "qc" in s1),
                        default=0
                    )
                    if device_qubits and max_circuit_qubits > device_qubits:
                        sys.stderr.write(f"  [Submit] {qpu} 스킵: 회로 {max_circuit_qubits}q > 장비 {device_qubits}q\n")
                        continue
    
                    optimizer = UQIOptimizer(calibration=calibration)
                    qpu_circuits[qpu] = {}
                    qpu_analysis[qpu] = {
                        "circuits": {}, "total_cost": 0.0, "avg_fidelity": None,
                        "calibration": {
                            "avg_2q_error": calibration.get("avg_2q_error"),
                            "avg_t2_ms":    calibration.get("avg_t2_ms"),
                            "avg_2q_ns":    calibration.get("avg_2q_ns"),
                        }
                    }
    
                    for name, s1 in stage1_results.items():
                        if "error" in s1:
                            qpu_analysis[qpu]["circuits"][name] = {"error": s1["error"]}
                            continue
                        try:
                            qc     = s1["qc"]
                            result = optimizer.optimize_stage2(
                                s1["stage1"]["circuit"], qpu,
                                stage1_result=s1["stage1"], combination="auto", verify=False)
                            qc_opt = result.get("circuit", qc)
    
                            ops      = qc_opt.count_ops()
                            n_1q     = sum(v for k, v in ops.items() if k not in ['cx','cz','ecr','measure','reset','barrier','delay'])
                            n_2q     = sum(v for k, v in ops.items() if k in ['cx','cz','ecr'])
                            # 실제 사용된 큐비트만 카운트 (IBM 156q 가상 할당 문제 우회)
                            _used_q  = set()
                            for _inst in qc_opt.data:
                                if _inst.operation.name in ['barrier']:
                                    continue
                                for _qb in _inst.qubits:
                                    try:
                                        _used_q.add(qc_opt.find_bit(_qb).index)
                                    except Exception:
                                        pass
                            n_qubits = len(_used_q) if _used_q else qc_opt.num_qubits
                            q1_ns    = calibration.get("avg_1q_ns") or 0
                            q2_ns_c  = calibration.get("avg_2q_ns") or 0
                            ro_ms    = calibration.get("avg_ro_ms") or 0
                            single_shot_ns = n_1q * q1_ns + n_2q * q2_ns_c + n_qubits * ro_ms * 1e6
                            total_exec_ms  = round(single_shot_ns * shots / 1e6, 2)
    
                            t2_ms    = calibration.get("avg_t2_ms")
                            q2_ns    = calibration.get("avg_2q_ns")
                            t2_ratio = None
                            if t2_ms and q2_ns:
                                t2_ns    = t2_ms * 1e6
                                est_ns   = q2_ns * qc_opt.depth()
                                t2_ratio = round(est_ns / t2_ns, 2)
    
                            # 캘리브레이션 기반 예상 fidelity — 실제 제출되는 물리 회로(qc_opt) 기준
                            q1_error = calibration.get("avg_1q_error") or 0
                            q2_error = calibration.get("avg_2q_error") or 0
                            ro_error = calibration.get("avg_ro_error") or 0

                            phys_ops = qc_opt.count_ops()
                            _2Q_GATES = {'cx','cz','ecr','swap','iswap','cp','crz','crx','cry','cu','cu3','mcx'}
                            _1Q_GATES = {'x','y','z','h','s','sdg','t','tdg','sx','sxdg','rx','ry','rz','u','u1','u2','u3','r','p'}

                            phys_n_2q  = sum(v for k,v in phys_ops.items() if k in _2Q_GATES)
                            phys_n_1q  = sum(v for k,v in phys_ops.items() if k in _1Q_GATES)
                            phys_n_meas= phys_ops.get('measure', 0) or n_qubits

                            import math
                            # T2 디코히어런스: 물리 회로 깊이 기준
                            t2_ms  = calibration.get("avg_t2_ms")
                            q2_ns  = calibration.get("avg_2q_ns") or 0
                            phys_depth = qc_opt.depth()
                            t2_decay = 1.0
                            if t2_ms and q2_ns and phys_depth:
                                t_circuit_ms = (phys_depth * q2_ns) / 1e6
                                t2_decay = math.exp(-t_circuit_ms / t2_ms)

                            est_fidelity = max(0.0, round(
                                (1.0 - q1_error) ** phys_n_1q *
                                (1.0 - q2_error) ** phys_n_2q *
                                (1.0 - ro_error) ** phys_n_meas *
                                t2_decay,
                                4
                            ))
    
                            qpu_analysis[qpu]["circuits"][name] = {
                                "num_qubits":     n_qubits,  # 실제 사용 큐비트 (가상 할당 제외)
                                "total_gates":    sum(qc_opt.count_ops().values()),
                                "depth":          qc_opt.depth(),
                                "two_q_gates":    n_2q,
                                "exec_time_ms":   total_exec_ms,
                                "exec_time_s":    round(total_exec_ms / 1000, 3),
                                "single_shot_ns": round(single_shot_ns, 1),
                                "fidelity":       est_fidelity,
                                "t2_ratio":       t2_ratio,
                                "t2_warning":     (t2_ratio or 0) > 1,
                                "combination":    result.get("combination"),
                            }
                            qpu_analysis[qpu]["total_cost"] += total_exec_ms
                            qpu_circuits[qpu][name] = qc_opt
    
                        except Exception as e:
                            err_msg = str(e)
                            qpu_analysis[qpu]["circuits"][name] = {"error": err_msg}
                            if "non-standard gates" in err_msg or "basis_gates" in err_msg:
                                sys.stderr.write(f"  [Submit] {qpu} 스킵: {err_msg[:80]}\n")
                                qpu_analysis[qpu]["_skip"] = True
                                break
    
                    fidelities = [
                        v["fidelity"] for v in qpu_analysis[qpu]["circuits"].values()
                        if isinstance(v, dict) and v.get("fidelity") is not None
                    ]
                    qpu_analysis[qpu]["avg_fidelity"] = (
                        round(sum(fidelities) / len(fidelities), 4) if fidelities else None
                    )
    
                # ── Phase 2: 추천 QPU 결정 (Fidelity + 실행시간 복합 점수) ──
                # score = 0.4 * fidelity + 0.6 * (TIME_REF / (TIME_REF + exec_time_s))
                # TIME_REF=30s: 30초 기준 time_score=0.5; 3100초는 ~0.01로 급격히 페널티
                _TIME_REF = 30.0
                def _qpu_composite_score(a):
                    fidelity   = a.get("avg_fidelity") or 0
                    total_ms   = a.get("total_cost") or 0
                    time_s     = total_ms / 1000.0
                    time_score = _TIME_REF / (_TIME_REF + time_s)
                    return 0.4 * fidelity + 0.6 * time_score

                recommended_qpu = max(
                    (q for q in qpu_analysis
                     if qpu_analysis[q].get("avg_fidelity") is not None and not qpu_analysis[q].get("_skip")),
                    key=lambda q: _qpu_composite_score(qpu_analysis[q]),
                    default=SUPPORTED_QPUS[0]
                )
                rec_score = _qpu_composite_score(qpu_analysis.get(recommended_qpu, {}))

                # Phase 3 제거 — 모든 QPU Fidelity를 캘리브레이션 기반으로 일관성 있게 계산
                # 노이즈 시뮬은 별도 Pipeline > Noise Simulation 스텝에서 확인

                if qpu_name == "auto":
                    selected_qpu   = recommended_qpu
                    rec_time_s = (qpu_analysis.get(recommended_qpu, {}).get("total_cost") or 0) / 1000.0
                    selection_note = (
                        f"UQI 추천: {recommended_qpu} "
                        f"(Fidelity {qpu_analysis.get(recommended_qpu,{}).get('avg_fidelity', 0):.4f}, "
                        f"Est. Time {rec_time_s:.1f}s, Score {rec_score:.3f})"
                    )
                    disadvantages  = []
                else:
                    selected_qpu = qpu_name
                    if selected_qpu not in available_qpus:
                        qpu_status = _get_qpu_status_cached()
                        s = qpu_status.get(selected_qpu, {})
                        if s and not s.get("available", True):
                            return json.dumps({"error": mcp_qpu_offline(selected_qpu)})
                        return json.dumps({"error": mcp_unavailable_qpu(selected_qpu)})
    
                    disadvantages = []
                    sel     = qpu_analysis.get(selected_qpu, {})
                    rec     = qpu_analysis.get(recommended_qpu, {})
                    sel_cal = sel.get("calibration", {})
                    rec_cal = rec.get("calibration", {})
    
                    if selected_qpu != recommended_qpu:
                        sel_fid = sel.get("avg_fidelity") or 0
                        rec_fid = rec.get("avg_fidelity") or 0
                        if rec_fid > sel_fid:
                            diff = round((rec_fid - sel_fid) * 100, 2)
                            disadvantages.append(f"예상 Fidelity {diff}% 낮음 ({sel_fid:.4f} vs {rec_fid:.4f} for {recommended_qpu})")
                        sel_err = sel_cal.get("avg_2q_error") or 0
                        rec_err = rec_cal.get("avg_2q_error") or 0
                        if sel_err > rec_err and rec_err > 0:
                            ratio = round(sel_err / rec_err, 1)
                            disadvantages.append(f"2Q 에러율 {ratio}배 높음 ({sel_err:.4f} vs {rec_err:.4f} for {recommended_qpu})")
                        sel_t2 = sel_cal.get("avg_t2_ms") or 0
                        rec_t2 = rec_cal.get("avg_t2_ms") or 0
                        if sel_t2 < rec_t2 and rec_t2 > 0:
                            ratio = round(rec_t2 / sel_t2, 1)
                            disadvantages.append(f"T2 코히어런스 {ratio}배 짧음 ({sel_t2*1000:.1f}μs vs {rec_t2*1000:.1f}μs for {recommended_qpu})")
                        # 실행 시간 비교
                        sel_time_s = (sel.get("total_cost") or 0) / 1000.0
                        rec_time_s = (rec.get("total_cost") or 0) / 1000.0
                        if sel_time_s > rec_time_s * 2 and rec_time_s > 0:
                            ratio = round(sel_time_s / rec_time_s, 1)
                            disadvantages.append(
                                f"실행 시간 {ratio}배 느림 ({sel_time_s:.1f}s vs {rec_time_s:.1f}s for {recommended_qpu})"
                            )
                        sel_score = _qpu_composite_score(sel)
                        selection_note = (
                            f"사용자 선택: {selected_qpu} (Score {sel_score:.3f}) "
                            f"/ UQI 추천: {recommended_qpu} (Score {rec_score:.3f})"
                        )
                    else:
                        selection_note = f"사용자 선택 = UQI 추천: {selected_qpu} ✓"
    
                if not confirmed:
                    sel_info = qpu_analysis.get(selected_qpu, {})
                    t2_warnings = [
                        f"{name}: T2 비율 {v['t2_ratio']}x"
                        for name, v in sel_info.get("circuits", {}).items()
                        if v.get("t2_warning")
                    ]
                    qubit_warnings = []
                    for name, v in sel_info.get("circuits", {}).items():
                        if isinstance(v, dict) and "num_qubits" in v:
                            cal = _get_calibration(selected_qpu)
                            device_qubits  = cal.get("num_qubits", 0)
                            circuit_qubits = v["num_qubits"]
                            if device_qubits and circuit_qubits > device_qubits:
                                qubit_warnings.append(f"{name}: 회로 {circuit_qubits}q > 장비 {device_qubits}q → 제출 불가")
    
                    msg_lines = [
                        "⚠️  QPU 제출 확인 필요", "",
                        f"선택 QPU:    {selected_qpu}",
                        f"추천 QPU:    {recommended_qpu}",
                        f"예상 Fidelity: {sel_info.get('avg_fidelity')}",
                        f"예상 실행시간: {sel_info.get('total_cost')}ms ({round(sel_info.get('total_cost', 0)/1000, 2)}s) × {shots} shots",
                        f"※ QPU 큐 대기시간 별도",
                        f"shots:       {shots}",
                    ]
                    if qubit_warnings:
                        msg_lines += ["", "⚠️  큐비트 수 초과 (제출 불가):"]
                        for w in qubit_warnings:
                            msg_lines.append(f"  • {w}")
                    if disadvantages:
                        msg_lines += ["", "⚠️  선택 QPU의 예상 불리한 점:"]
                        for d in disadvantages:
                            msg_lines.append(f"  • {d}")
                    if t2_warnings:
                        msg_lines += ["", "⚠️  T2 코히어런스 경고:"]
                        for w in t2_warnings:
                            msg_lines.append(f"  • {w}")
                    msg_lines += ["", "제출하려면 confirmed=True로 다시 호출하세요."]
    
                    qpu_status_info = _get_qpu_status_cached()
                    qpu_summary = {}
                    for qpu in available_qpus:
                        a = qpu_analysis.get(qpu)
                        if a is None:
                            # Phase 1에서 스킵된 QPU (아날로그/포토닉 등) 제외
                            continue
                        s = qpu_status_info.get(qpu, {})
                        qpu_summary[qpu] = {
                            "avg_fidelity":  a.get("avg_fidelity"),
                            "total_exec_ms": a.get("total_cost"),
                            "total_exec_s":  round((a.get("total_cost") or 0) / 1000, 2),
                            "composite_score": round(_qpu_composite_score(a), 4),
                            "avg_2q_error":  a.get("calibration", {}).get("avg_2q_error"),
                            "avg_t2_ms":     a.get("calibration", {}).get("avg_t2_ms"),
                            "recommended":   qpu == recommended_qpu,
                            "selected":      qpu == selected_qpu,
                            "online":        s.get("available", True),
                            "pending_jobs":  s.get("pending_jobs"),
                            "queue_note":    s.get("note", ""),
                        }

                    rec_info = qpu_analysis.get(recommended_qpu, {})
                    # 캐시에는 구조화 데이터만 저장 (메시지 텍스트 제외 → i18n 대응)
                    _cache_data = {
                        "status":           STATUS_AWAITING_CONFIRMATION,
                        "selected_qpu":     selected_qpu,
                        "recommended_qpu":  recommended_qpu,
                        "selection_note":   selection_note,
                        "disadvantages":    disadvantages,
                        "t2_warnings":      t2_warnings,
                        "qubit_warnings":   qubit_warnings,
                        "qpu_comparison":   qpu_summary,
                        "circuit_info":     sel_info.get("circuits", {}),
                        "circuit_info_rec": rec_info.get("circuits", {}) if recommended_qpu != selected_qpu else {},
                        "avg_fidelity":     sel_info.get("avg_fidelity"),
                        "total_cost":       sel_info.get("total_cost"),
                        "shots":            shots,
                    }
                    _rag.set_cache(_submit_cache_key, json.dumps(_cache_data, ensure_ascii=False))
                    # 응답에는 메시지 텍스트도 포함 (프론트엔드 표시용)
                    _result = _safe_json({
                        **_cache_data,
                        "message": _build_qpu_submit_message(_cache_data),
                    })
                    return _result
                else:
                    # confirmed=True인데 캐시가 없어서 분析 재실행된 경우
                    # → 재확인 요청
                    return json.dumps({
                        "error": MCP_CACHE_EXPIRED,
                        "recommended_qpu": recommended_qpu,
                        "selected_qpu": selected_qpu,
                        "status": STATUS_CACHE_EXPIRED,
                    }, ensure_ascii=False)
            # confirmed=True → 실제 제출
            # 제출 직전 실시간 상태 확인 (캐시 우회, 3회 재시도)
            _live = live_check_qpu(selected_qpu)
            if not _live["ok"]:
                return json.dumps({
                    "error":   mcp_live_check_unreachable(selected_qpu, _live["attempts"]),
                    "status":  "live_check_unreachable",
                    "qpu":     selected_qpu,
                    "attempts": _live["attempts"],
                }, ensure_ascii=False)
            if not _live["available"]:
                _qpu_cmp = _cached_obj.get("qpu_comparison", {}) if isinstance(_cached_obj, dict) else {}
                _alts = recommend_alternatives(selected_qpu, _qpu_cmp)
                return json.dumps({
                    "error":           mcp_qpu_offline_live(selected_qpu, _live["status"]),
                    "status":          "qpu_offline",
                    "qpu":             selected_qpu,
                    "current_status":  _live["status"],
                    "alternatives":    _alts,
                    "action_required": mcp_action_retry_or_cancel(),
                }, ensure_ascii=False)

            cal = _get_calibration(selected_qpu)
            device_qubits = cal.get("num_qubits", 0)
            for name, v in qpu_analysis.get(selected_qpu, {}).get("circuits", {}).items():
                if isinstance(v, dict) and device_qubits:
                    if v.get("num_qubits", 0) > device_qubits:
                        return json.dumps({
                            "error": mcp_qubit_exceeded_submit(name, v['num_qubits'], selected_qpu, device_qubits)
                        })

            circuits = qpu_circuits.get(selected_qpu, {})
            circuit_names_to_submit = list(circuits.keys() or converter.qasm_results.keys())

            # ── 백그라운드 제출: 즉시 submission_id 반환, 회로별 진행상황 폴링 가능 ──
            import uuid as _uuid, threading as _threading

            sid = _uuid.uuid4().hex[:10]
            _submission_progress[sid] = {
                "status":          "submitting",
                "total":           len(circuit_names_to_submit),
                "done":            0,
                "selected_qpu":    selected_qpu,
                "recommended_qpu": recommended_qpu,
                "shots":           shots,
                "results":         {},
            }

            def _bg_submit(sid=sid):
                prog = _submission_progress[sid]

                # ── 회로 형식 비호환 / 통합 미완료 QPU 사전 차단 ──
                # gate 회로(Qiskit) 알고리즘으로는 제출 불가.
                # 향후 Pulser/AHS 워크플로우 또는 Nexus 통합 시 SKIP에서 제거.
                _SUBMIT_BLOCK_QPUS = {
                    'quera_aquila':         'AHS analog (Qiskit gate 회로 비호환)',
                    'pasqal_fresnel':       'Pulser pulse 입력 (Qiskit gate 회로 비호환)',
                    'pasqal_fresnel_can1':  'Pulser pulse 입력 (Qiskit gate 회로 비호환)',
                    'quantinuum_h2_1':      'Quantinuum Nexus 통합 대기',
                    'quantinuum_h2_2':      'Quantinuum Nexus 통합 대기',
                    'quantinuum_h1_1':      'Quantinuum Nexus 통합 대기',
                }
                if selected_qpu in _SUBMIT_BLOCK_QPUS:
                    _reason = _SUBMIT_BLOCK_QPUS[selected_qpu]
                    prog["status"] = "blocked"
                    prog["error"] = (
                        f"{selected_qpu} 은(는) 현재 제출 차단됨: {_reason}. "
                        f"분석/추천만 가능, 실 제출은 호환 알고리즘/통합 후 가능."
                    )
                    for _name in circuit_names_to_submit:
                        prog["results"][_name] = {
                            "ok": False,
                            "error": f"[{selected_qpu}] {_reason}",
                            "blocked": True,
                        }
                    print(
                        f"  [BgSubmit] {selected_qpu} 차단: {_reason}",
                        file=sys.stderr,
                    )
                    return

                # executor 1회 생성 (backend 재사용)
                _ibm_exec = _iqm_exec = _braket_exec = _azure_exec = None
                if "ibm" in selected_qpu:
                    from uqi_executor_ibm import UQIExecutorIBM
                    _ibm_exec = UQIExecutorIBM(converter=converter, shots=shots)
                    _ibm_exec._token = IBM_TOKEN
                elif "iqm" in selected_qpu:
                    from uqi_executor_iqm import UQIExecutorIQM
                    _iqm_exec = UQIExecutorIQM(converter=converter, shots=shots)
                    _iqm_exec._token = IQM_TOKEN
                elif selected_qpu.startswith("ionq_") or selected_qpu.startswith("rigetti_"):
                    from uqi_executor_braket import UQIExecutorBraket
                    _braket_exec = UQIExecutorBraket(converter=converter, shots=shots)
                elif selected_qpu.startswith("pasqal_"):
                    from uqi_executor_azure import UQIExecutorAzure
                    _azure_exec = UQIExecutorAzure(converter=converter, shots=shots)

                for name in circuit_names_to_submit:
                    _saved_jid = {"id": None}  # Perceval 분기 콜백과 outer except 공유
                    try:
                        if "ibm" in selected_qpu:
                            sub = _ibm_exec._submit_single(
                                name=name,
                                qasm=converter.qasm_results.get(name),
                                backend_name=selected_qpu,
                            )
                            if not sub["ok"]:
                                raise Exception(sub["error"])
                            _job_store.save_job(
                                job_id=sub["job_id"],
                                qpu_name=selected_qpu, circuit_name=name, shots=shots,
                                extra={"via": sub.get("via"), "backend": selected_qpu},
                            )
                            prog["results"][name] = {
                                "ok": True, "job_id": sub["job_id"],
                                "backend": selected_qpu, "via": sub.get("via"),
                            }

                        elif "iqm" in selected_qpu:
                            device_name = selected_qpu.split('_')[-1]
                            backend_url = f"https://resonance.meetiqm.com/computers/{device_name}"
                            sub = _iqm_exec._submit_single(
                                name=name,
                                qasm=converter.qasm_results.get(name),
                                backend_url=backend_url,
                            )
                            if not sub["ok"]:
                                raise Exception(sub["error"])
                            _job_store.save_job(
                                job_id=sub["job_id"],
                                qpu_name=selected_qpu, circuit_name=name, shots=shots,
                                extra={"backend_url": backend_url},
                            )
                            prog["results"][name] = {
                                "ok": True, "job_id": sub["job_id"],
                                "backend": backend_url,
                            }

                        elif _braket_exec is not None:
                            sub = _braket_exec._submit_single(
                                name=name,
                                qasm=converter.qasm_results.get(name),
                                backend_name=selected_qpu,
                            )
                            if not sub["ok"]:
                                raise Exception(sub["error"])
                            _job_store.save_job(
                                job_id=sub["job_id"],
                                qpu_name=selected_qpu, circuit_name=name, shots=shots,
                                extra={"via": sub.get("via"), "backend": selected_qpu},
                            )
                            prog["results"][name] = {
                                "ok": True, "job_id": sub["job_id"],
                                "backend": selected_qpu, "via": sub.get("via"),
                            }

                        elif _azure_exec is not None:
                            sub = _azure_exec._submit_single(
                                name=name,
                                qasm=converter.qasm_results.get(name),
                                backend_name=selected_qpu,
                            )
                            if not sub["ok"]:
                                raise Exception(sub["error"])
                            _job_store.save_job(
                                job_id=sub["job_id"],
                                qpu_name=selected_qpu, circuit_name=name, shots=shots,
                                extra={"via": sub.get("via"), "backend": selected_qpu},
                            )
                            prog["results"][name] = {
                                "ok": True, "job_id": sub["job_id"],
                                "backend": selected_qpu, "via": sub.get("via"),
                            }

                        elif selected_qpu.startswith("sim:") or selected_qpu.startswith("qpu:"):
                            from uqi_executor_perceval import UQIExecutorPerceval
                            _ptoken  = os.getenv("QUANDELA_TOKEN")
                            use_sim  = selected_qpu.startswith("sim:")
                            _pexec   = UQIExecutorPerceval(extractor=extractor, shots=shots)
                            _pexec._token = _ptoken
                            t_start = time.time()
                            _entry = extractor.perceval_circuits.get(name)
                            if _entry is None:
                                raise Exception(f"Perceval 회로 '{name}' 없음 — 추출 실패")
                            _pcircuit, _pinput = UQIExecutorPerceval._restore_perceval_objects(_entry)

                            def _on_submit(jid, platform, _name=name):
                                _saved_jid["id"] = jid
                                try:
                                    _job_store.save_job(
                                        job_id=jid,
                                        qpu_name=selected_qpu, circuit_name=_name, shots=shots,
                                        extra={"backend": selected_qpu, "platform": platform},
                                    )
                                except Exception as _se:
                                    print(f"  [BgSubmit] save_job 실패({jid}): {_se}",
                                          file=sys.stderr)

                            result_dict = _pexec._run_single(
                                name=name,
                                circuit=_pcircuit,
                                input_state=_pinput,
                                use_simulator=use_sim,
                                on_submit=_on_submit,
                            )
                            exec_time = time.time() - t_start
                            import uuid as _juuid
                            _jid = (_saved_jid["id"]
                                    or result_dict.get("cloud_job_id")
                                    or _juuid.uuid4().hex)
                            if not _saved_jid["id"]:
                                _job_store.save_job(
                                    job_id=_jid, qpu_name=selected_qpu, circuit_name=name, shots=shots,
                                    extra={"backend": selected_qpu, "exec_time": round(exec_time, 2)},
                                )

                            if not result_dict["ok"]:
                                _job_store.update_job(
                                    _jid, status="error",
                                    error=result_dict.get("error", "unknown"),
                                )
                                raise Exception(result_dict["error"])

                            _job_store.update_job(
                                _jid, status="done", counts=result_dict["counts"],
                            )
                            _rag.add_execution(
                                circuit_name=name, qpu_name=selected_qpu, backend=selected_qpu,
                                shots=shots, counts=result_dict["counts"], ok=True, exec_time_sec=exec_time,
                            )
                            prog["results"][name] = {
                                "ok": True, "counts": result_dict["counts"],
                                "backend": selected_qpu, "exec_time": round(exec_time, 2),
                                "job_id": _jid,
                            }
                        else:
                            raise ValueError(
                                f"{selected_qpu} 은(는) 현재 직접 submit 미지원 (IBM/IQM/Braket/Azure만 지원)."
                            )

                    except Exception as e:
                        # Perceval on_submit 으로 이미 저장된 경우 error 상태 마킹
                        if _saved_jid["id"]:
                            try:
                                _job_store.update_job(
                                    _saved_jid["id"], status="error", error=str(e),
                                )
                            except Exception:
                                pass
                        prog["results"][name] = {
                            "ok": False, "error": str(e),
                            "job_id": _saved_jid["id"],
                        }
                        _rag.add_execution(
                            circuit_name=name, qpu_name=selected_qpu, backend=selected_qpu,
                            shots=shots, counts={}, ok=False, extra={"error": str(e)},
                        )

                    prog["done"] += 1

                prog["status"] = "completed"
                # 오래된 progress 항목 정리 (최대 50개 유지)
                if len(_submission_progress) > 50:
                    oldest = sorted(_submission_progress.keys())[0]
                    _submission_progress.pop(oldest, None)

            _threading.Thread(target=_bg_submit, daemon=True).start()

            return _safe_json({
                "status":          "submitting",
                "submission_id":   sid,
                "total":           len(circuit_names_to_submit),
                "selected_qpu":    selected_qpu,
                "recommended_qpu": recommended_qpu,
                "shots":           shots,
            })

        except Exception as e:
            ctx = _request_context.get()
            _rag.add_pipeline_issue(
                stage="uqi_qpu_submit",          # 예: "uqi_analyze", "uqi_optimize" 등
                sdk=framework if 'framework' in dir() else "",
                issue=str(e),
                solution="",
                qpu_name=qpu_name if 'qpu_name' in dir() else "",
                severity="error",
                extra={
                    "traceback":    traceback.format_exc(),
                    "algorithm_file": algorithm_file if 'algorithm_file' in dir() else "",
                    "client_ip":    ctx.get("client_ip", "unknown"),
                    "transport":    ctx.get("transport", "unknown"),
                }
            )
            return json.dumps({"error": str(e)})

    return await asyncio.to_thread(_run)


# ─────────────────────────────────────────────────────────
# 툴 9: Job 상태 조회
# ─────────────────────────────────────────────────────────

@mcp.tool()
async def uqi_job_status(job_id: str) -> str:
    """QPU 비동기 제출 job 상태 조회 (IBM/IQM/Braket/Azure 공통). job_id: 제출 시 받은 ID"""
    def _run():
        try:
            # 1) job store에서 캐시 확인
            stored = _job_store.get_job(job_id)
            if stored is None:
                return json.dumps({"error": f"job_id를 찾을 수 없습니다: {job_id}"})

            # 완료/취소/확정실패 상태면 클라우드 재조회 없이 즉시 리턴
            # error는 polling 버그로 인한 오기록일 수 있으므로 클라우드 재조회 허용
            # failed는 IQM 장비 확정 실패 — 재시도 불필요
            qpu_name = stored["qpu_name"]
            # SDK 분기용 vendor 키 ('ibm'/'iqm'/'braket'/'azure'/'quandela') — pricing 모델에서 추출
            from uqi_pricing import get_pricing
            _pmeta = get_pricing(qpu_name) or {}
            vendor = _pmeta.get("vendor", "")
            if stored["status"] in ("done", "cancelled", "failed"):
                return json.dumps({
                    "job_id":  job_id,
                    "vendor":  vendor,
                    "runtime": stored.get("runtime"),
                    "qpu":     qpu_name,
                    "status":  stored["status"],
                    "counts":  stored["counts"],
                    "error":   stored["error"],
                    "done":    stored["status"] == "done",
                    "submitted_at": stored["submitted_at"],
                    "updated_at":   stored["updated_at"],
                }, ensure_ascii=False)

            # 2) 클라우드 API 폴링 (vendor / qpu_name 위에서 추출)

            if vendor == "ibm":
                from uqi_executor_ibm import UQIExecutorIBM
                result = UQIExecutorIBM.fetch_job_status(job_id, token=IBM_TOKEN)
            elif vendor == "iqm":
                from uqi_executor_iqm import UQIExecutorIQM
                backend_url = stored["extra"].get("backend_url", "")
                result = UQIExecutorIQM.fetch_job_status(
                    job_id, token=IQM_TOKEN, backend_url=backend_url)
            elif vendor == "braket":
                from uqi_executor_braket import UQIExecutorBraket
                result = UQIExecutorBraket.fetch_job_status(job_id)
            elif vendor == "azure":
                from uqi_executor_azure import UQIExecutorAzure
                result = UQIExecutorAzure.fetch_job_status(job_id)
            else:
                return json.dumps({"error": f"미지원 vendor: {vendor}"})

            # 3) job store 업데이트
            if result.get("done") and result.get("counts"):
                _job_store.update_job(job_id, status="done", counts=result["counts"])
                _rag.add_execution(
                    circuit_name=stored.get("circuit_name", ""),
                    qpu_name=qpu_name, backend=qpu_name,
                    shots=sum(result["counts"].values()),
                    counts=result["counts"], ok=True,
                )
            elif result.get("cancelled"):
                _job_store.update_job(job_id, status="cancelled")
            elif result.get("cloud_failed"):
                # cloud 확정 실패 (IBM ERROR / IQM FAILED) → "failed" 저장, 이후 재시도 없음
                _job_store.update_job(job_id, status="failed", error=result["error"])
            elif result.get("error"):
                error_msg = result["error"]
                if "rate limit" in error_msg.lower() or "quota exceeded" in error_msg.lower():
                    # 일시적 rate limit → 상태 변경 없이 running 유지 (다음 폴링 때 재시도)
                    _job_store.update_job(job_id, status="running")
                else:
                    _job_store.update_job(job_id, status="error", error=error_msg)
            else:
                _job_store.update_job(job_id, status="running")

            result["vendor"]       = vendor
            result["qpu"]          = qpu_name
            result["submitted_at"] = stored["submitted_at"]
            return json.dumps(result, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"error": str(e), "traceback": traceback.format_exc()})

    return await asyncio.to_thread(_run)


# ─────────────────────────────────────────────────────────
# 툴 10: Job 취소
# ─────────────────────────────────────────────────────────

@mcp.tool()
async def uqi_job_cancel(job_id: str) -> str:
    """QPU job 취소. 클라우드 취소 실패해도 로컬 job store에서 cancelled 처리."""
    def _run():
        try:
            stored = _job_store.get_job(job_id)
            if stored is None:
                return json.dumps({"ok": False, "error": f"job_id 없음: {job_id}"})

            if stored["status"] in ("done", "cancelled"):
                return json.dumps({
                    "ok": False,
                    "error": f"이미 {stored['status']} 상태 — 취소 불가",
                    "status": stored["status"],
                })
            # error 상태는 로컬 버그 오기록일 수 있으므로 클라우드 취소 시도 허용

            # SDK 분기용 vendor 키 — pricing 모델에서 추출 (qpu_name 기반)
            from uqi_pricing import get_pricing
            _pmeta = get_pricing(stored["qpu_name"]) or {}
            vendor = _pmeta.get("vendor", "")
            cloud_result = {"ok": False, "error": "클라우드 취소 미지원"}

            # 클라우드 취소 시도 (실패해도 로컬 취소는 진행)
            try:
                if vendor == "ibm":
                    from uqi_executor_ibm import UQIExecutorIBM
                    cloud_result = UQIExecutorIBM.cancel_job(job_id, token=IBM_TOKEN)
                elif vendor == "iqm":
                    from uqi_executor_iqm import UQIExecutorIQM
                    backend_url = stored["extra"].get("backend_url", "")
                    cloud_result = UQIExecutorIQM.cancel_job(
                        job_id, token=IQM_TOKEN, backend_url=backend_url)
                elif vendor == "braket":
                    from uqi_executor_braket import UQIExecutorBraket
                    cloud_result = UQIExecutorBraket.cancel_job(job_id)
                elif vendor == "azure":
                    from uqi_executor_azure import UQIExecutorAzure
                    cloud_result = UQIExecutorAzure.cancel_job(job_id)
            except Exception as ce:
                cloud_result = {"ok": False, "error": str(ce)}

            # 로컬 job store는 무조건 cancelled 처리
            _job_store.cancel_job(job_id)

            return json.dumps({
                "ok":           True,
                "job_id":       job_id,
                "vendor":       vendor,
                "cloud_cancel": cloud_result,
                "note": "Job cancelled locally. Cloud cancellation: "
                        + ("success" if cloud_result.get("ok") else f"failed ({cloud_result.get('error','unknown')}) — local cancel applied"),
            }, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})

    return await asyncio.to_thread(_run)


# ─────────────────────────────────────────────────────────────
# 비용 요약 — AWS Braket + Azure Quantum 청구 비용 실시간 조회
# ─────────────────────────────────────────────────────────────

def _aws_billing_summary() -> dict:
    """AWS Cost Explorer로 month-to-date 비용 요약."""
    out = {"ok": False, "error": None,
           "total_usd": None, "currency": "USD",
           "by_service": {},
           "period_start": None, "period_end": None}
    try:
        import boto3
        from datetime import date as _d
        ce = boto3.client(
            'ce',
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name='us-east-1',
        )
        today = _d.today()
        start = today.replace(day=1).isoformat()
        end   = today.isoformat()
        out["period_start"] = start
        out["period_end"]   = end
        # 동일 날짜면 +1일 (Cost Explorer는 end exclusive)
        if start == end:
            from datetime import timedelta as _td
            end = (today + _td(days=1)).isoformat()
        resp = ce.get_cost_and_usage(
            TimePeriod={'Start': start, 'End': end},
            Granularity='MONTHLY',
            Metrics=['UnblendedCost'],
            GroupBy=[{'Type': 'DIMENSION', 'Key': 'SERVICE'}],
        )
        total = 0.0
        services = {}
        for day in resp.get('ResultsByTime', []):
            for grp in day.get('Groups', []):
                svc = grp['Keys'][0]
                amt = float(grp['Metrics']['UnblendedCost']['Amount'])
                services[svc] = services.get(svc, 0) + amt
                total += amt
        out["ok"] = True
        out["total_usd"]   = round(total, 4)
        out["by_service"]  = {k: round(v, 4) for k, v in services.items() if v > 0.0001}
    except Exception as e:
        msg = str(e)
        if "AccessDenied" in msg or "not authorized" in msg:
            out["error"] = "ce:GetCostAndUsage 권한 필요 (관리자 문의)"
        else:
            out["error"] = msg[:300]
    return out


def _azure_billing_summary() -> dict:
    """Azure Cost Management로 month-to-date 비용 요약."""
    out = {"ok": False, "error": None,
           "total": None, "currency": None,
           "period": "MonthToDate"}

    # 사전 검사 — token 발급 전 빠른 실패
    sub_id = os.getenv("AZURE_QUANTUM_SUBSCRIPTION_ID")
    if not sub_id:
        out["error"] = "AZURE_QUANTUM_SUBSCRIPTION_ID 환경변수 없음"
        return out

    try:
        from azure.identity import ClientSecretCredential
        import requests as _req

        cred = ClientSecretCredential(
            tenant_id=os.getenv("AZURE_TENANT_ID"),
            client_id=os.getenv("AZURE_CLIENT_ID"),
            client_secret=os.getenv("AZURE_CLIENT_SECRET"),
        )
        token = cred.get_token("https://management.azure.com/.default").token

        # 2023-11-01: 안정 버전, Cost Management Reader 권한으로 동작
        url = (
            f"https://management.azure.com/subscriptions/{sub_id}"
            f"/providers/Microsoft.CostManagement/query?api-version=2023-11-01"
        )
        body = {
            "type":      "ActualCost",
            "timeframe": "MonthToDate",
            "dataset": {
                "granularity": "None",
                "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}}
            }
        }
        r = _req.post(url, json=body, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
        }, timeout=30)

        if r.status_code == 200:
            data = r.json()
            rows = data.get('properties', {}).get('rows', [])
            if rows and len(rows[0]) >= 2:
                cost, currency = rows[0][0], rows[0][1]
                out["ok"] = True
                out["total"]    = round(float(cost), 4)
                out["currency"] = str(currency)
            else:
                out["ok"]      = True
                out["total"]   = 0.0
                out["currency"] = "USD"
        elif r.status_code == 401 or r.status_code == 403:
            out["error"] = (
                "Cost Management Reader 권한 필요 (Subscription scope) — 관리자 문의"
            )
        else:
            out["error"] = f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:
        out["error"] = str(e)[:300]
    return out


@mcp.tool(timeout=30)
async def uqi_billing_summary() -> str:
    """AWS Braket + Azure Quantum 청구 비용 요약 (month-to-date, 실시간 API 호출).

    Returns: JSON
        {
          "fetched_at": ISO 8601,
          "aws":   {ok, total_usd, currency, by_service, period_start, period_end, error?},
          "azure": {ok, total, currency, period, error?},
        }
    """
    def _run():
        from datetime import datetime as _dt, timezone as _tz
        result = {
            "fetched_at": _dt.now(_tz.utc).isoformat(),
            "aws":   _aws_billing_summary(),
            "azure": _azure_billing_summary(),
        }
        return json.dumps(result, ensure_ascii=False, default=str)

    return await asyncio.to_thread(_run)


# ─────────────────────────────────────────────────────────────
# Job enrichment — list 응답에 cost + timing 자동 추가
# ─────────────────────────────────────────────────────────────

def _calc_db_duration_sec(submitted_at: str, updated_at: str) -> float | None:
    """DB의 ISO 8601 timestamp 차이 → 초 (wall-clock fallback용)."""
    try:
        from datetime import datetime as _dt
        sub = _dt.fromisoformat(submitted_at.replace("Z", "+00:00")) if submitted_at else None
        upd = _dt.fromisoformat(updated_at.replace("Z", "+00:00"))   if updated_at   else None
        if sub and upd:
            return (upd - sub).total_seconds()
    except Exception:
        pass
    return None


def _fetch_vendor_timing(vendor: str, job_id: str, extra: dict) -> dict:
    """vendor별 정확한 timing 조회 (큐 제외 가능 시), 실패/미지원 시 fallback dict."""
    try:
        if vendor == "ibm":
            from uqi_executor_ibm import UQIExecutorIBM
            return UQIExecutorIBM.fetch_job_timing(job_id, token=IBM_TOKEN)
        if vendor == "azure":
            from uqi_executor_azure import UQIExecutorAzure
            return UQIExecutorAzure.fetch_job_timing(job_id)
        if vendor == "braket":
            from uqi_executor_braket import UQIExecutorBraket
            return UQIExecutorBraket.fetch_job_timing(job_id)
        # iqm, quandela: vendor API에 timing 정보 미제공 → DB fallback
    except Exception as e:
        return {
            "execution_sec": None, "wall_clock_sec": None,
            "source": "vendor_api_error", "accuracy": "unknown",
            "error": str(e),
        }
    # iqm/quandela 등 fallback
    return {
        "execution_sec":  None,
        "wall_clock_sec": None,        # 호출자가 DB에서 채움
        "source":         "db_wall_clock_fallback",
        "accuracy":       "queue_included",
        "error":          None,
    }


def _enrich_job_with_cost_timing(job: dict) -> dict:
    """job dict에 cost + timing 추가. 취소/에러는 비교 의미 없어 제외.

    캐싱: status=done인 job의 timing은 extra에 저장 → 다음 호출 시 재사용.
    """
    status = job.get("status", "")
    qpu_name = job.get("qpu_name", "")
    shots = int(job.get("shots") or 0)
    extra = job.get("extra") or {}

    # 1. 4축 정체성 — 모든 status 에서 항상 채움 (cancelled/error 도 표시단에서 필요)
    try:
        from uqi_pricing import (parse_qpu_full, _MODALITY_LABELS, _ANALOG_QPUS)
        # DB 의 catalog 컬럼 우선, 없으면 catalog 매핑 fallback
        qpu_vendor   = job.get("qpu_vendor")
        qpu_model    = job.get("qpu_model")
        qpu_family   = job.get("qpu_family")
        qpu_runtime  = job.get("runtime")
        qpu_modality = job.get("qpu_modality")
        if not (qpu_vendor and qpu_model and qpu_runtime and qpu_modality):
            _meta = parse_qpu_full(qpu_name)
            qpu_vendor   = qpu_vendor   or _meta["vendor"]
            qpu_model    = qpu_model    or _meta["model"]
            qpu_family   = qpu_family   if qpu_family is not None else _meta.get("family")
            qpu_runtime  = qpu_runtime  or _meta["runtime"]
            qpu_modality = qpu_modality or _meta["modality"]
        job["qpu_vendor"]         = qpu_vendor
        job["qpu_model"]          = qpu_model
        job["qpu_family"]         = qpu_family
        job["qpu_runtime"]        = qpu_runtime
        job["qpu_modality"]       = qpu_modality
        job["qpu_modality_label"] = _MODALITY_LABELS.get(qpu_modality, qpu_modality or "Unknown")
        job["qpu_is_analog"]      = qpu_name in _ANALOG_QPUS
    except Exception:
        # catalog 매핑 실패 — 'Unknown' 유지 (webapp 에서 안전 fallback)
        pass

    # 2. 비용 추정 — cancelled/error 제외
    if status not in ("cancelled", "canceled", "error", "failed"):
        try:
            from uqi_pricing import (estimate_cost, format_actual_cost,
                                     format_actual_cost_token, get_pricing)
            cost = estimate_cost(qpu_name, shots)
            # pricing 모델의 vendor 키 (cost 표시 분기용 — 'ibm'/'braket'/'azure' 등)
            _pmeta = get_pricing(qpu_name) or {}
            _pricing_vendor = _pmeta.get("vendor", "")
            job["cost"] = {
                "estimated":     cost,
                "display":       format_actual_cost(_pricing_vendor, qpu_name, cost),
                "display_token": format_actual_cost_token(_pricing_vendor, qpu_name, cost),
                "vendor":        _pricing_vendor,         # legacy 호환 (pricing 모델 vendor)
                "source":        job.get("qpu_runtime"),  # billing source = catalog runtime
                "source_vendor": _pricing_vendor,         # legacy 호환
            }
        except Exception as _ce:
            job["cost"] = {"error": str(_ce)}

    # 2. 시간 — done은 정확한 vendor timing + 캐시, running/submitted은 진행 중
    if status == "done":
        # 캐시 우선 — 단 손상 의심(>24h DB fallback)은 폐기 후 재계산
        cached = extra.get("timing")
        if cached and isinstance(cached, dict):
            _wc = cached.get("wall_clock_sec")
            _src = cached.get("source")
            if _wc and _wc > 86400 and _src == "db_wall_clock_fallback":
                cached = None    # 손상 의심 → 재계산
        if cached and isinstance(cached, dict):
            job["timing"] = cached
        else:
            timing = _fetch_vendor_timing(vendor, job["job_id"], extra)
            # vendor API 정확한 wall_clock 받았으면 사용. 못 받았으면 DB fallback.
            # ⚠️ DB fallback 신뢰 주의: enrichment가 캐시 저장하면서 updated_at 덮어쓰면
            #    wall_clock 망가짐 → 별도 update_job_extra() 사용해 updated_at 보존.
            if timing.get("wall_clock_sec") is None:
                db_dur = _calc_db_duration_sec(
                    job.get("submitted_at"), job.get("updated_at")
                )
                timing["wall_clock_sec"] = db_dur
                # source 표시 — db fallback인 경우 사용자가 정확도 인지 가능
                if not timing.get("source"):
                    timing["source"] = "db_wall_clock_fallback"
                    timing["accuracy"] = "queue_included"
            # extra에 캐시 — updated_at 보존 위해 update_job_extra 사용
            try:
                extra["timing"] = timing
                _job_store.update_job_extra(job["job_id"], extra)
            except Exception:
                pass
            job["timing"] = timing
    elif status in ("submitted", "running"):
        # 진행 중 — DB submitted_at 부터 현재까지
        from datetime import datetime as _dt, timezone as _tz
        try:
            sub = _dt.fromisoformat(job.get("submitted_at","").replace("Z","+00:00"))
            now = _dt.now(_tz.utc)
            elapsed = (now - sub).total_seconds()
            job["timing"] = {
                "execution_sec":  None,
                "wall_clock_sec": elapsed,
                "source":         "in_progress_elapsed",
                "accuracy":       "queue_included",
                "in_progress":    True,
                "error":          None,
            }
        except Exception:
            job["timing"] = None
    # cancelled/error는 timing 표시 안 함

    # 3. 표시용 duration (사람 읽기 좋게)
    timing = job.get("timing") or {}
    try:
        from uqi_pricing import format_duration
        # 비정상적으로 큰 wall_clock (24시간 이상)은 손상된 데이터 가능성 → 표시 X
        # (DB fallback에서만 — vendor API 정확값은 그대로 신뢰)
        SUSPICIOUS_HOURS = 24 * 3600    # 24시간
        is_db_fallback = (timing.get("source") == "db_wall_clock_fallback")
        wc = timing.get("wall_clock_sec")
        if wc is not None and is_db_fallback and wc > SUSPICIOUS_HOURS:
            # 손상 가능성 — 표시 안 함 + 메타 표시 (webapp이 인지 가능)
            job["duration_display"] = None
            job["duration_kind"] = "suspicious"
            timing["suspicious"] = True
            timing["accuracy"] = "unreliable"
        elif timing.get("execution_sec") is not None:
            job["duration_display"] = format_duration(timing["execution_sec"])
            job["duration_kind"] = "execution"   # 큐 제외 정확
        elif wc is not None:
            job["duration_display"] = format_duration(wc)
            job["duration_kind"] = "wall_clock"  # 큐 포함
        else:
            job["duration_display"] = None
    except Exception:
        job["duration_display"] = None

    return job


@mcp.tool()
async def uqi_job_list(
    limit:          int  = 50,
    status:         str  = "",    # "" = 전체, "active" = submitted+running, "done", "cancelled", "error"
    days:           int  = 0,     # 0 = 전체, N = 최근 N일
    offset:         int  = 0,     # 페이지네이션 오프셋
    search_id:      str  = "",    # job_id 부분 일치 검색 (대소문자 무관)
    search_vendor:  str  = "",    # vendor 부분 일치 검색
    search_qpu:     str  = "",    # qpu_name 부분 일치 검색
    search_circuit: str  = "",    # circuit_name 부분 일치 검색
    search_modality:str  = "",    # modality 정확 일치 (superconducting/ion-trap/neutral-atom/photonic)
    distinct_values:bool = False, # True: 필터 드롭다운용 고유값 목록만 반환
) -> str:
    """QPU job 이력 조회. distinct_values=True 시 vendor/qpu_name/circuit_name 고유값 목록 반환"""
    def _run():
        try:
            import sqlite3 as _sq
            from datetime import datetime, timezone, timedelta
            from pathlib import Path

            db_path = Path(__file__).parent.parent / "data" / "uqi_jobs.db"
            conn = _sq.connect(str(db_path), timeout=10)
            conn.row_factory = _sq.Row

            # distinct_values 모드: 필터 드롭다운 초기화용 (Phase 2 — DB catalog 컬럼 직접 사용)
            #   vendors    = jobs.qpu_vendor   distinct (예: IBM/IQM/IonQ/...)
            #   qpus       = jobs.qpu_name     distinct (raw id, webapp 에서 라벨 변환)
            #   modalities = jobs.qpu_modality distinct (사용된 modality 만)
            #   circuits   = jobs.circuit_name distinct
            if distinct_values:
                import json as _json
                vendors    = [r[0] for r in conn.execute(
                    "SELECT DISTINCT qpu_vendor FROM jobs WHERE qpu_vendor IS NOT NULL ORDER BY qpu_vendor").fetchall()]
                qpus       = [r[0] for r in conn.execute(
                    "SELECT DISTINCT qpu_name FROM jobs WHERE qpu_name IS NOT NULL ORDER BY qpu_name").fetchall()]
                modalities = [r[0] for r in conn.execute(
                    "SELECT DISTINCT qpu_modality FROM jobs WHERE qpu_modality IS NOT NULL ORDER BY qpu_modality").fetchall()]
                circuits   = [r[0] for r in conn.execute(
                    "SELECT DISTINCT circuit_name FROM jobs WHERE circuit_name IS NOT NULL ORDER BY circuit_name").fetchall()]
                job_ids    = [r[0] for r in conn.execute(
                    "SELECT job_id FROM jobs ORDER BY submitted_at DESC LIMIT 200").fetchall()]
                conn.close()
                return _json.dumps({"ok": True, "distinct": {
                    "vendors": vendors, "qpus": qpus,
                    "circuits": circuits, "job_ids": job_ids,
                    "modalities": modalities,
                }}, ensure_ascii=False)

            conditions = []
            params: list = []

            if status == "active":
                conditions.append("status IN ('submitted','running')")
            elif status in ("done", "cancelled", "error"):
                conditions.append("status = ?")
                params.append(status)

            if days and days > 0:
                cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
                conditions.append("submitted_at >= ?")
                params.append(cutoff)

            # 대소문자 무관 LIKE (LOWER 비교)
            if search_id:
                conditions.append("LOWER(job_id) LIKE LOWER(?)")
                params.append(f"%{search_id}%")
            # Phase 2: 검색은 DB 의 catalog 컬럼 직접 비교 (IN 절 / 매핑 불필요)
            if search_vendor:
                # qpu_vendor 정확 일치 (대소문자 통일 — DB 에 'IBM','IQM' 등 저장)
                conditions.append("qpu_vendor = ?")
                params.append(search_vendor)
            if search_qpu:
                conditions.append("LOWER(qpu_name) LIKE LOWER(?)")
                params.append(f"%{search_qpu}%")
            if search_circuit:
                conditions.append("LOWER(circuit_name) LIKE LOWER(?)")
                params.append(f"%{search_circuit}%")
            if search_modality:
                conditions.append("qpu_modality = ?")
                params.append(search_modality)

            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

            total = conn.execute(
                f"SELECT COUNT(*) FROM jobs {where}", params
            ).fetchone()[0]

            rows = conn.execute(
                f"SELECT * FROM jobs {where} ORDER BY submitted_at DESC LIMIT ? OFFSET ?",
                params + [limit, offset]
            ).fetchall()
            conn.close()

            import json as _json
            result = []
            for row in rows:
                d = dict(row)
                d["counts"] = _json.loads(d["counts"]) if d.get("counts") else None
                d["extra"]  = _json.loads(d["extra"])  if d.get("extra")  else {}
                # cost + timing enrichment (cancelled/error는 자동 skip)
                try:
                    d = _enrich_job_with_cost_timing(d)
                except Exception as _ee:
                    d["enrichment_error"] = str(_ee)
                result.append(d)

            return _json.dumps({
                "ok": True, "jobs": result, "count": len(result),
                "total": total,
                "filter": {
                    "status": status or "all", "days": days, "limit": limit, "offset": offset,
                    "search_id": search_id, "search_vendor": search_vendor,
                    "search_qpu": search_qpu, "search_circuit": search_circuit,
                    "search_modality": search_modality,
                },
            }, ensure_ascii=False, default=str)
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)})
    return await asyncio.to_thread(_run)


# ─────────────────────────────────────────────────────────
# 엔트리포인트
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--transport", default="sse", choices=["stdio", "sse"])
    args = parser.parse_args()

    print(f"UQI MCP 서버 시작... ({args.transport} {args.host}:{args.port})", file=sys.stderr)

    if args.transport == "sse":
        from starlette.middleware.cors import CORSMiddleware
        from starlette.requests import Request
        from starlette.responses import HTMLResponse
        from starlette.routing import Route
        import uvicorn

        class NgrokBypassMiddleware:
            def __init__(self, app):
                self.app = app
            async def __call__(self, scope, receive, send):
                if scope["type"] == "http":
                    async def send_with_header(message):
                        if message["type"] == "http.response.start":
                            headers = list(message.get("headers", []))
                            headers.append((b"ngrok-skip-browser-warning", b"true"))
                            message = {**message, "headers": headers}
                        await send(message)
                    await self.app(scope, receive, send_with_header)
                else:
                    await self.app(scope, receive, send)
        
        from anyio import ClosedResourceError as AnyIOClosedResourceError

        class SessionExpiredMiddleware:
            def __init__(self, app):
                self.app = app
            async def __call__(self, scope, receive, send):
                if scope["type"] == "http":
                    try:
                        await self.app(scope, receive, send)
                    except AnyIOClosedResourceError:
                        # 세션 만료 — 클라이언트에 410 반환하여 재연결 유도
                        await send({
                            "type": "http.response.start",
                            "status": 410,
                            "headers": [[b"content-type", b"application/json"]],
                        })
                        await send({
                            "type": "http.response.body",
                            "body": b'{"error":"session_expired","message":"SSE session closed, please reconnect"}',
                        })
                else:
                    await self.app(scope, receive, send)

        class RequestContextMiddleware:
            def __init__(self, app):
                self.app = app
            async def __call__(self, scope, receive, send):
                if scope["type"] in ("http", "lifespan"):
                    client = scope.get("client")
                    _request_context.set({
                        "client_ip": client[0] if client else "unknown",
                        "transport": "sse",
                    })
                await self.app(scope, receive, send)

        import asyncio as _asyncio

        class SSEKeepaliveMiddleware:
            """SSE /sse 연결에 20초마다 comment 전송 — 브라우저/프록시 idle 타임아웃 방지"""
            def __init__(self, app):
                self.app = app
            async def __call__(self, scope, receive, send):
                if scope["type"] != "http" or not scope.get("path", "").endswith("/sse"):
                    await self.app(scope, receive, send)
                    return
                response_started = False
                async def send_with_keepalive(message):
                    nonlocal response_started
                    if message["type"] == "http.response.start":
                        response_started = True
                    await send(message)
                # 원본 앱을 태스크로 실행하면서 keepalive 코루틴 병렬 실행
                async def keepalive():
                    while True:
                        await _asyncio.sleep(20)
                        if not response_started:
                            continue
                        try:
                            await send({"type": "http.response.body",
                                        "body": b": ping\n\n", "more_body": True})
                        except Exception:
                            return
                ka_task = _asyncio.ensure_future(keepalive())
                try:
                    await self.app(scope, receive, send_with_keepalive)
                finally:
                    ka_task.cancel()

        mcp_app = mcp.http_app(transport="sse")

        async def homepage(request: Request):
            html_path = Path(__file__).parent.parent / "webapp" / "uqi_webapp.html"
            if html_path.exists():
                content = html_path.read_text(encoding="utf-8")
            else:
                content = "<h1>uqi_webapp.html not found</h1>"
            return HTMLResponse(content, headers={"Cache-Control": "no-store"})

        from starlette.applications import Starlette
        from starlette.routing import Mount, Route
        from starlette.middleware import Middleware
        from starlette.staticfiles import StaticFiles
        from starlette.types import Scope

        class QuartzStaticFiles(StaticFiles):
            """Static files with .html extension fallback for Quartz pages.

            Quartz emits pretty paths like 'Foo/Bar.html' but internal links
            reference 'Foo/Bar' (no extension). Starlette's built-in html=True
            only maps directory→index.html, so we add .html fallback on 404.

            Also forces `Cache-Control: no-store` so weekly rebuilds are
            reflected immediately without clients holding stale HTML/assets.
            """

            async def get_response(self, path: str, scope: Scope):
                response = await super().get_response(path, scope)
                if response.status_code == 404:
                    basename = path.rsplit("/", 1)[-1] if "/" in path else path
                    if basename and "." not in basename:
                        response = await super().get_response(path + ".html", scope)
                # Starlette StaticFiles sets its own Cache-Control via max-age;
                # override so notion-backup content never sits in browser cache.
                try:
                    response.headers["Cache-Control"] = "no-store"
                except Exception:
                    pass
                return response

        notion_backup_dir = Path(__file__).parent.parent / "webapp" / "notion-backup"
        notion_backup_routes = []
        if notion_backup_dir.exists():
            notion_backup_routes.append(
                Mount(
                    "/notion-backup",
                    app=QuartzStaticFiles(directory=str(notion_backup_dir), html=True),
                )
            )

        app = Starlette(
            routes=[Route("/", homepage), *notion_backup_routes, Mount("/", app=mcp_app)],
            middleware=[
                Middleware(CORSMiddleware, allow_origins=["*"],
                           allow_methods=["*"], allow_headers=["*"]),
                Middleware(NgrokBypassMiddleware),
                Middleware(SSEKeepaliveMiddleware),
                Middleware(RequestContextMiddleware),
                Middleware(SessionExpiredMiddleware),
            ],
        )

        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            loop="asyncio",
            timeout_keep_alive=300,   # SSE 연결 idle 유지 (기본 5초 → 5분)
            timeout_graceful_shutdown=10,
        )
    else:
        mcp.run()