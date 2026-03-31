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

SUPPORTED_QPUS = ["ibm_fez", "ibm_torino", "ibm_marrakesh", "ibm_kingston",
                   "iqm_garnet", "iqm_emerald", "iqm_sirius",
                   "ionq_forte1", "rigetti_ankaa3", "quera_aquila",
                   "sim:ascella", "sim:belenos", "qpu:ascella", "qpu:belenos"]

_pending_submissions = {}

_qpu_status_cache = {"data": None, "ts": 0.0}
_QPU_STATUS_TTL   = 300  # 5분

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
    (r'\bos\.(?!getenv\b)', "os 모듈 직접 호출"),
    (r'\bsubprocess\b', "subprocess 모듈"),
    (r'\bsocket\b', "socket 모듈"),
    (r'\beval\s*\(', "eval() 사용"),
    (r'\bexec\s*\(', "exec() 사용"),
    (r'\b__import__\s*\(', "__import__() 사용"),
    (r'\bopen\s*\(.*["\']w["\']', "파일 쓰기(open write)"),
    (r'\bshutil\b', "shutil 모듈"),
    (r'\burllib\b', "urllib 모듈"),
    (r'\brequests\b', "requests 모듈"),
    (r'\bhttpx\b', "httpx 모듈"),
    (r'\baiohttp\b', "aiohttp 모듈"),
    (r'\bparamiko\b', "paramiko 모듈"),
    (r'\bpickle\b', "pickle 모듈"),
    (r'\bctypes\b', "ctypes 모듈"),
    (r'\bcffi\b', "cffi 모듈"),
    (r'\bimportlib\b', "importlib 모듈"),
    (r'\bpty\b', "pty 모듈"),
    (r'\bsignal\b', "signal 모듈"),
    (r'\bmultiprocessing\b', "multiprocessing 모듈"),
    (r'\bthreading\b', "threading 모듈"),
    (r'\bconcurrent\b', "concurrent 모듈"),
]

_MAX_QUBITS = 200
_MAX_GATES  = 2_000_000


def _safe_file_check(algorithm_file: str, tool: str = None) -> Optional[str]:
    import re as _re
    path = Path(algorithm_file)
    if not path.exists():
        return f"파일 없음: {algorithm_file}"
    if path.suffix != ".py":
        return f"Python 파일(.py)만 허용됩니다: {algorithm_file}"
    if path.stat().st_size > 1 * 1024 * 1024:
        return f"파일 크기 초과 (최대 1MB): {algorithm_file}"
    try:
        source = path.read_text(encoding="utf-8")
    except Exception as e:
        return f"파일 읽기 실패: {e}"
    for pattern, desc in _BLOCKED_PATTERNS:
        if _re.search(pattern, source):
            msg = f"보안 정책 위반 - {desc} 감지됨 (패턴: {pattern})"
            _rag.add_security_block(algorithm_file, reason=desc, pattern=pattern, tool=tool)
            return msg
    import_lines = _re.findall(
        r'^\s*(?:import|from)\s+([\w\.]+)', source, _re.MULTILINE
    )
    for mod in import_lines:
        root = mod.split(".")[0]
        if root not in _ALLOWED_IMPORTS:
            msg = f"허용되지 않은 모듈 import: '{root}' (허용 목록: {sorted(_ALLOWED_IMPORTS)})"
            _rag.add_security_block(algorithm_file, reason=f"비허용 모듈 import: {root}", pattern=f"import {root}", tool=tool)
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
        cache_data = json.dumps({
            "framework":    framework,
            "qasm_results": converter.qasm_results,
            "qir_results":  {k: v.hex() if isinstance(v, bytes) else str(v)
                             for k, v in (converter.qir_results or {}).items()},
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
            calibration = _get_calibration(qpu_name)
            results = {}
            _should_cache = False
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
                    results[name] = {
                        "profile":   profile,
                        "t2_ratio":  t2_ratio,
                        "qpu_name":  qpu_name,
                        "framework": framework,
                        "qasm":      qasm,
                    }
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
                    issue="회로 추출 실패 — circuits 비어있음 (커널 컴파일 오류 또는 실행 실패)",
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
            optimizer   = UQIOptimizer(calibration=calibration)
            results     = {}
            _should_cache = False
            for name, qasm in converter.qasm_results.items():
                try:
                    qc     = QuantumCircuit.from_qasm_str(qasm)
                    _should_cache = True
                    result = optimizer.optimize(qc, qpu_name, combination=combination, verify=verify)
                    meta   = optimizer.collect_metadata(name, result, qpu_name)
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
                    issue="최적화 실패 — results 비어있음 (회로 추출 또는 변환 실패)",
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

    def _run():
        from qiskit import QuantumCircuit
        try:
            _cache_key = f"noise:{_file_hash(algorithm_file)}:{qpu_name}:{shots}"
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
                    r  = noise.run_comparison(qc, shots=shots)
                    _rag.add_execution(
                        circuit_name=name, qpu_name=qpu_name,
                        backend=f"noise_sim_{sdk}", shots=shots,
                        counts=r["noise_counts"], ok=True,
                        extra={"comparison": r["comparison"]}
                    )
                    results[name] = {
                        "ideal_counts": r["ideal_counts"],
                        "noise_counts": r["noise_counts"],
                        "tvd":          r["comparison"]["tvd"],
                        "fidelity":     r["comparison"]["fidelity"],
                    }
                except Exception as e:
                    results[name] = {"error": str(e)}

            _result = _safe_json({"qpu_name": qpu_name, "sdk": sdk, "shots": shots, "results": results})
            if not results:
                ctx = _request_context.get()
                _rag.add_pipeline_issue(
                    stage="uqi_noise_simulate",
                    sdk=framework,
                    issue="노이즈 시뮬 실패 — results 비어있음 (회로 추출 또는 변환 실패)",
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
                    issue="QEC 분석 실패 — results 비어있음 (회로 추출 또는 변환 실패)",
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
                    issue="QEC 적용 실패 — results 비어있음 (회로 추출 또는 변환 실패)",
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
                r = _rag.search_best_combination(num_qubits, total_gates, qpu_name or "ibm_fez")
                if r:
                    return _safe_json(r["data"])
                return json.dumps({"result": "데이터 없음"})
            elif query_type == "pipeline_issues":
                records = _rag.search_pipeline_issues(sdk=sdk or None)
                return _safe_json([r["data"] for r in records[:limit]])
            elif query_type == "transpile_patterns":
                records = _rag.search_transpile_patterns(sdk=sdk or None)
                return _safe_json([r["data"] for r in records[:limit]])
            elif query_type == "qec_results":
                records = _rag.search_qec_results(qpu_name=qpu_name or None)
                return _safe_json([r["data"] for r in records[:limit]])
            elif query_type == "gpu_benchmark":
                records = _rag.search(record_type="gpu_benchmark", limit=limit)
                return _safe_json([r["data"] for r in records])
            elif query_type == "semantic":
                if not query:
                    return json.dumps({"error": "semantic 검색은 query 파라미터 필요"})
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
                    "error": f"미지원 query_type: {query_type}",
                    "supported": ["best_combination", "pipeline_issues",
                                  "transpile_patterns", "qec_results", "recent", "stats"]
                })
        except Exception as e:
            return json.dumps({"error": str(e)})

    return await asyncio.to_thread(_run)


# ─────────────────────────────────────────────────────────
# 툴 6: 캘리브레이션 조회
# ─────────────────────────────────────────────────────────

@mcp.tool()
async def uqi_calibration_info(
    qpu_name: str  = "ibm_fez",
    refresh:  bool = False,
) -> str:
    """QPU 캘리브레이션 조회. qpu_name: ibm_fez|iqm_garnet, refresh: 갱신 여부"""
    def _run():
        try:
            if refresh:
                _cal.sync(qpu_name)
            calibration = _get_calibration(qpu_name)
            if not calibration:
                return json.dumps({"error": f"캘리브레이션 없음: {qpu_name}"})
            return _safe_json({
                "qpu_name":     qpu_name,
                "num_qubits":   calibration.get("num_qubits"),
                "basis_gates":  calibration.get("basis_gates"),
                "coupling_map": calibration.get("coupling_map"),
                "avg_t1_ms":    calibration.get("avg_t1_ms"),
                "avg_t2_ms":    calibration.get("avg_t2_ms"),
                "avg_1q_ns":    calibration.get("avg_1q_ns"),
                "avg_2q_ns":    calibration.get("avg_2q_ns"),
                "avg_1q_error": calibration.get("avg_1q_error"),
                "avg_2q_error": calibration.get("avg_2q_error"),
                "avg_ro_error": calibration.get("avg_ro_error"),
                "last_updated": calibration.get("last_updated"),
            })
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
                        'qpu_name':      qpu,
                        'num_qubits':    cal.get('num_qubits'),
                        'avg_t1_ms':     cal.get('avg_t1_ms'),
                        'avg_t2_ms':     cal.get('avg_t2_ms'),
                        'avg_1q_ns':     cal.get('avg_1q_ns'),
                        'avg_2q_ns':     cal.get('avg_2q_ns'),
                        'avg_1q_error':  cal.get('avg_1q_error'),
                        'avg_2q_error':  cal.get('avg_2q_error'),
                        'avg_ro_error':  cal.get('avg_ro_error'),
                        'basis_gates':   cal.get('basis_gates'),
                        'coupling_map':  cal.get('coupling_map'),
                        'last_updated':  cal.get('last_updated'),
                        'type':          _vendor,
                        # QuEra 전용 필드
                        'c6_coefficient':    cal.get('c6_coefficient'),
                        'rabi_freq_max_mhz': cal.get('rabi_freq_max_mhz'),
                    }
                else:
                    results[qpu] = {'qpu_name': qpu, 'error': 'no calibration data'}
            except Exception as e:
                results[qpu] = {'qpu_name': qpu, 'error': str(e)}
        return _safe_json({'qpus': SUPPORTED_QPUS, 'calibrations': results})

    return await asyncio.to_thread(_run)

@mcp.tool()
async def uqi_list_qpus() -> str:
    """지원 QPU 목록 반환"""
    return json.dumps({"qpus": SUPPORTED_QPUS})


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
                return json.dumps({"error": "허용 확장자: .py"})
            safe_name = Path(filename).name
            dest = ALG_FILES_DIR / safe_name
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
                return json.dumps({"error": f"파일 없음: {algorithm_file}"})
            h = hashlib.md5(path.read_bytes()).hexdigest()[:12]

            stages = {
                "analyze":     [f"analyze:{h}:{qpu_name or 'ibm_fez'}", f"analyze:{h}:iqm_garnet", f"extract:{h}"],
                "optimize":    [f"optimize:{h}:{qpu_name or 'ibm_fez'}:auto", f"optimize:{h}:{qpu_name or 'ibm_fez'}:qiskit+sabre",
                                f"optimize:{h}:{qpu_name or 'ibm_fez'}:tket+sabre"],
                "noise":       [f"noise:{h}:{qpu_name or 'ibm_fez'}:1024", f"noise:{h}:{qpu_name or 'ibm_fez'}:256"],
                "qec_analyze": [f"qec_analyze:{h}:{qpu_name or 'ibm_fez'}:1024"],
                "qec_apply":   [f"qec_apply:{h}:{qpu_name or 'ibm_fez'}:bit_flip:256",
                                f"qec_apply:{h}:{qpu_name or 'ibm_fez'}:phase_flip:256"],
                "gpu_benchmark":[f"gpu_benchmark:{h}"],
                "qpu_submit":  [f"qpu_submit:{h}:{qpu_name or 'auto'}:1024"],
            }

            if stage == "all":
                keys = [k for ks in stages.values() for k in ks]
                # extract 캐시도 포함
                keys.append(f"extract:{h}")
            else:
                keys = stages.get(stage, [])

            import sqlite3
            conn = sqlite3.connect(_rag.cache_file, timeout=5.0)
            deleted = 0
            try:
                for k in keys:
                    cur = conn.execute("DELETE FROM cache WHERE key=?", (k,))
                    deleted += cur.rowcount
                # LIKE로 해시 기반 전체 삭제 (QPU별 변형 포함)
                if stage == "all":
                    cur = conn.execute("DELETE FROM cache WHERE key LIKE ?", (f"%{h}%",))
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
            return json.dumps({"error": f"디렉토리 없음: {alg_dir}"})
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
            return json.dumps({"error": f"파일 없음: {algorithm_file}"})
        if path.suffix != ".py":
            return json.dumps({"error": "Python 파일만 조회 가능합니다"})
        if path.stat().st_size > 1 * 1024 * 1024:
            return json.dumps({"error": "파일 크기 초과 (최대 1MB)"})
        try:
            content = path.read_text(encoding="utf-8")
            return _safe_json({"name": path.name, "path": str(path), "content": content})
        except Exception as e:
            return json.dumps({"error": str(e)})

    return await asyncio.to_thread(_run)


# ─────────────────────────────────────────────────────────
# 툴 7: QPU 제출 (Human-in-the-loop)
# ─────────────────────────────────────────────────────────

@mcp.tool(timeout=600)
async def uqi_qpu_submit(
    algorithm_file: str,
    qpu_name:       str  = "auto",
    shots:          int  = 1024,
    confirmed:      bool = False,
) -> str:
    """QPU 제출 (Human-in-the-loop). confirmed=False: 예상 분석만, confirmed=True: 실제 제출. 비용 발생 주의."""
    _check_err = _safe_file_check(algorithm_file, tool="uqi_qpu_submit")
    if _check_err:
        return json.dumps({"error": _check_err})

    def _run():
        import contextlib
        from qiskit import QuantumCircuit
        try:
            # ── 캐시 히트 시 즉시 반환 (장비 접속 없음) ──
            if not confirmed:
                _submit_cache_key = f"qpu_submit:{_file_hash(algorithm_file)}:{qpu_name}:{shots}"
                _cached = _rag.get_cache(_submit_cache_key)
                if _cached:
                    print(f"  [Cache] qpu_submit analyze 캐시 히트: {Path(algorithm_file).name}", file=sys.stderr)
                    try:
                        _cached_obj = json.loads(_cached)
                        _cached_obj["_cached"] = True
                        return json.dumps(_cached_obj, ensure_ascii=False)
                    except Exception:
                        return _cached

            extractor, converter, framework = _extract_and_convert(algorithm_file)

            available_qpus = _get_available_qpus_cached()

            if qpu_name != "auto":
                qpu_status = _cal.get_qpu_status()
                s = qpu_status.get(qpu_name, {})
                if not s.get("available", True):
                    return json.dumps({
                        "error": f"{qpu_name} 현재 offline 상태입니다.",
                        "queue_note": s.get("note", ""),
                    })

            PERCEVAL_QPUS = ["sim:ascella", "sim:belenos", "qpu:ascella", "qpu:belenos"]
            if qpu_name in PERCEVAL_QPUS:
                if not confirmed:
                    return _safe_json({
                        "status": "awaiting_confirmation",
                        "message": f"⚠️  Perceval QPU 제출 확인 필요\n\n선택 QPU: {qpu_name}\nshots: {shots}\n\n제출하려면 confirmed=True로 다시 호출하세요.",
                        "selected_qpu": qpu_name,
                    })
                token = os.getenv("QUANDELA_TOKEN")
                from uqi_executor_perceval import UQIExecutorPerceval
                use_sim  = qpu_name.startswith("sim:")
                executor = UQIExecutorPerceval(extractor=extractor, shots=shots)
                executor._token = token
                executor._platform_sim = qpu_name if use_sim else "sim:ascella"
                executor._platform_qpu = qpu_name if not use_sim else "qpu:belenos"
                execution_results = {}
                try:
                    for name, (circuit, input_state) in extractor.perceval_circuits.items():
                        result_dict = executor._run_single(
                            name=name, circuit=circuit,
                            input_state=input_state, use_simulator=use_sim,
                        )
                        execution_results[name] = result_dict
                except Exception as e:
                    return json.dumps({"error": f"Perceval 실행 실패: {str(e)}"})
                return _safe_json({
                    "status": "completed", "selected_qpu": qpu_name,
                    "shots": shots, "results": execution_results,
                })

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
            # 아날로그, 포토닉, Braket 전용 장비는 Qiskit 트랜스파일 불가 → 스킵
            _SKIP_SUBMIT_QPUS = {'quera_aquila', 'ionq_forte1', 'rigetti_ankaa3'}
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
                        n_qubits = qc_opt.num_qubits
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

                        # 캘리브레이션 기반 예상 fidelity (1Q + 2Q + Readout 통합)
                        q1_error = calibration.get("avg_1q_error") or 0
                        q2_error = calibration.get("avg_2q_error") or 0
                        ro_error = calibration.get("avg_ro_error") or 0

                        ops = s1["qc"].count_ops()
                        _2Q_GATES = {'cx','cz','ecr','swap','iswap','cp','crz','crx','cry','cu','cu3','mcx'}
                        _1Q_GATES = {'x','y','z','h','s','sdg','t','tdg','sx','sxdg','rx','ry','rz','u','u1','u2','u3','r','p'}
                        _MEAS     = {'measure'}

                        orig_n_2q  = sum(v for k,v in ops.items() if k in _2Q_GATES)
                        orig_n_1q  = sum(v for k,v in ops.items() if k in _1Q_GATES)
                        orig_n_meas= sum(v for k,v in ops.items() if k in _MEAS)
                        # readout 횟수가 없으면 큐비트 수 기준으로 추정
                        if orig_n_meas == 0:
                            orig_n_meas = s1["qc"].num_qubits

                        import math
                        # T2 디코히어런스 항: exp(-t_circuit / T2)
                        # t_circuit = depth * avg_2q_ns (2Q 게이트가 지배적)
                        t2_ms  = calibration.get("avg_t2_ms")
                        q2_ns  = calibration.get("avg_2q_ns") or 0
                        depth  = s1["qc"].depth()
                        t2_decay = 1.0
                        if t2_ms and q2_ns and depth:
                            t_circuit_ms = (depth * q2_ns) / 1e6  # ns → ms
                            t2_decay = math.exp(-t_circuit_ms / t2_ms)

                        est_fidelity = max(0.0, round(
                            (1.0 - q1_error) ** orig_n_1q *
                            (1.0 - q2_error) ** orig_n_2q *
                            (1.0 - ro_error) ** orig_n_meas *
                            t2_decay,
                            4
                        ))

                        qpu_analysis[qpu]["circuits"][name] = {
                            "num_qubits":     qc_opt.num_qubits,
                            "total_gates":    sum(qc_opt.count_ops().values()),
                            "depth":          qc_opt.depth(),
                            "two_q_gates":    n_2q,
                            "exec_time_ms":   total_exec_ms,
                            "exec_time_s":    round(total_exec_ms / 1000, 3),
                            "single_shot_ns": round(single_shot_ns, 1),
                            "fidelity":       est_fidelity,
                            "t2_ratio":       t2_ratio,
                            "t2_warning":     (t2_ratio or 0) > 1,
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

            # ── Phase 2: 추천 QPU 결정 (캘리브레이션 기반) ──
            recommended_qpu = max(
                (q for q in qpu_analysis
                 if qpu_analysis[q]["avg_fidelity"] and not qpu_analysis[q].get("_skip")),
                key=lambda q: qpu_analysis[q]["avg_fidelity"] or 0,
                default=SUPPORTED_QPUS[0]
            )

            # Phase 3 제거 — 모든 QPU Fidelity를 캘리브레이션 기반으로 일관성 있게 계산
            # 노이즈 시뮬은 별도 Pipeline > Noise Simulation 스텝에서 확인

            if qpu_name == "auto":
                selected_qpu   = recommended_qpu
                selection_note = f"UQI 추천: {recommended_qpu} (최고 Fidelity)"
                disadvantages  = []
            else:
                selected_qpu = qpu_name
                if selected_qpu not in available_qpus:
                    qpu_status = _cal.get_qpu_status()
                    s = qpu_status.get(selected_qpu, {})
                    if s and not s.get("available", True):
                        return json.dumps({"error": f"{selected_qpu} 현재 offline 상태입니다."})
                    return json.dumps({"error": f"미지원 또는 가용하지 않은 QPU: {selected_qpu}"})

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
                    selection_note = f"사용자 선택: {selected_qpu} (UQI 추천: {recommended_qpu})"
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

                qpu_status_info = _cal.get_qpu_status()
                qpu_summary = {}
                for qpu in available_qpus:
                    a = qpu_analysis.get(qpu, {})
                    s = qpu_status_info.get(qpu, {})
                    qpu_summary[qpu] = {
                        "avg_fidelity":  a.get("avg_fidelity"),
                        "total_exec_ms": a.get("total_cost"),
                        "total_exec_s":  round((a.get("total_cost") or 0) / 1000, 2),
                        "avg_2q_error":  a.get("calibration", {}).get("avg_2q_error"),
                        "avg_t2_ms":     a.get("calibration", {}).get("avg_t2_ms"),
                        "recommended":   qpu == recommended_qpu,
                        "selected":      qpu == selected_qpu,
                        "online":        s.get("available", True),
                        "pending_jobs":  s.get("pending_jobs"),
                        "queue_note":    s.get("note", ""),
                    }

                _result = _safe_json({
                    "status":          "awaiting_confirmation",
                    "message":         "\n".join(msg_lines),
                    "selected_qpu":    selected_qpu,
                    "recommended_qpu": recommended_qpu,
                    "selection_note":  selection_note,
                    "disadvantages":   disadvantages,
                    "qpu_comparison":  qpu_summary,
                    "circuit_info":    sel_info.get("circuits", {}),
                })
                _rag.set_cache(_submit_cache_key, _result)
                return _result

            # confirmed=True → 실제 제출
            cal = _get_calibration(selected_qpu)
            device_qubits = cal.get("num_qubits", 0)
            for name, v in qpu_analysis.get(selected_qpu, {}).get("circuits", {}).items():
                if isinstance(v, dict) and device_qubits:
                    if v.get("num_qubits", 0) > device_qubits:
                        return json.dumps({
                            "error": f"{name}: 회로 큐비트({v['num_qubits']}q)가 {selected_qpu} 장비({device_qubits}q)를 초과합니다. 제출 불가."
                        })

            circuits = qpu_circuits.get(selected_qpu, {})
            execution_results = {}
            for name, qc_opt in circuits.items():
                try:
                    t_start = time.time()
                    if "ibm" in selected_qpu:
                        from uqi_executor_ibm import UQIExecutorIBM
                        executor = UQIExecutorIBM(converter=converter, shots=shots)
                        executor._token = IBM_TOKEN
                        result_dict = executor._run_single(
                            name=name, qir_bitcode=None,
                            qasm=converter.qasm_results.get(name),
                            use_simulator=False, backend_name=selected_qpu,
                        )
                        if not result_dict["ok"]:
                            raise Exception(result_dict["error"])
                        counts = result_dict["counts"]
                    elif "iqm" in selected_qpu:
                        from uqi_executor_iqm import UQIExecutorIQM
                        executor = UQIExecutorIQM(converter=converter, shots=shots)
                        executor._token = IQM_TOKEN
                        device_name = selected_qpu.split('_')[-1]
                        result_dict = executor._run_single(
                            name=name, qasm=converter.qasm_results.get(name),
                            use_simulator=False,
                            backend_url=f"https://resonance.meetiqm.com/computers/{device_name}",
                        )
                        if not result_dict["ok"]:
                            raise Exception(result_dict["error"])
                        counts = result_dict["counts"]
                    elif selected_qpu.startswith("sim:") or selected_qpu.startswith("qpu:"):
                        from uqi_executor_perceval import UQIExecutorPerceval
                        token    = os.getenv("QUANDELA_TOKEN")
                        use_sim  = selected_qpu.startswith("sim:")
                        executor = UQIExecutorPerceval(extractor=extractor, shots=shots)
                        executor._token = token
                        result_dict = executor._run_single(
                            name=name,
                            circuit=extractor.perceval_circuits.get(name, (None, None))[0],
                            input_state=extractor.perceval_circuits.get(name, (None, None))[1],
                            use_simulator=use_sim,
                        )
                        if not result_dict["ok"]:
                            raise Exception(result_dict["error"])
                        counts = result_dict["counts"]
                    else:
                        raise ValueError(f"미지원 QPU: {selected_qpu}")

                    exec_time = time.time() - t_start
                    _rag.add_execution(
                        circuit_name=name, qpu_name=selected_qpu, backend=selected_qpu,
                        shots=shots, counts=counts, ok=True, exec_time_sec=exec_time,
                    )
                    execution_results[name] = {"counts": counts, "ok": True, "exec_time": round(exec_time, 2)}

                except Exception as e:
                    execution_results[name] = {"ok": False, "error": str(e)}
                    _rag.add_execution(
                        circuit_name=name, qpu_name=selected_qpu, backend=selected_qpu,
                        shots=shots, counts={}, ok=False, extra={"error": str(e)},
                    )

            return _safe_json({
                "status":          "completed",
                "selected_qpu":    selected_qpu,
                "recommended_qpu": recommended_qpu,
                "shots":           shots,
                "results":         execution_results,
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

        mcp_app = mcp.http_app(transport="sse")

        async def homepage(request: Request):
            html_path = Path(__file__).parent.parent / "webapp" / "uqi_webapp.html"
            if html_path.exists():
                content = html_path.read_text(encoding="utf-8")
            else:
                content = "<h1>uqi_webapp.html not found</h1>"
            return HTMLResponse(content)

        from starlette.applications import Starlette
        from starlette.routing import Mount, Route
        from starlette.middleware import Middleware

        app = Starlette(
            routes=[Route("/", homepage), Mount("/", app=mcp_app)],
            middleware=[
                Middleware(CORSMiddleware, allow_origins=["*"],
                           allow_methods=["*"], allow_headers=["*"]),
                Middleware(NgrokBypassMiddleware),
                Middleware(RequestContextMiddleware),  # 추가
                Middleware(SessionExpiredMiddleware),
            ],
        )

        uvicorn.run(app, host=args.host, port=args.port, loop="asyncio")
    else:
        mcp.run()