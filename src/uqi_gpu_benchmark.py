# uqi_gpu_benchmark.py
# CPU vs GPU 시뮬레이션 성능 비교 + RAG 저장
# subprocess 격리 기반 (메모리 누적 방지)

import os
import re
import sys
import time
import subprocess
import tempfile
import json
import numpy as np
from pathlib import Path
from typing import Optional


# ─────────────────────────────────────────
# GPU 가용성 확인
# ─────────────────────────────────────────

def check_gpu_available() -> dict:
    result = {
        'pennylane':      False,
        'qiskit':         False,
        'cudaq':          False,
        'cuda_available': False,
    }
    try:
        subprocess.run(['nvidia-smi'], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True)
        result['cuda_available'] = True
    except Exception:
        return result

    try:
        import pennylane as qml
        qml.device("lightning.gpu", wires=1)
        result['pennylane'] = True
    except Exception:
        pass

    try:
        from qiskit_aer import AerSimulator
        AerSimulator(method='statevector', device='GPU')
        result['qiskit'] = True
    except Exception:
        pass

    try:
        res = subprocess.run(
            [sys.executable, '-c',
             'import cudaq; cudaq.set_target("nvidia"); print("ok")'],
            capture_output=True, text=True, timeout=10
        )
        if res.returncode == 0 and 'ok' in res.stdout:
            result['cudaq'] = True
    except Exception:
        pass

    return result


def resolve_gpu_usable(frameworks: list, gpu_status: dict) -> tuple:
    if not gpu_status['cuda_available']:
        return False, []
    gpu_accelerated = []
    for fw in frameworks:
        fw_lower = fw.lower()
        if fw_lower == 'pennylane' and gpu_status['pennylane']:
            gpu_accelerated.append('PennyLane(lightning.gpu)')
        elif fw_lower in ('qiskit', 'qrisp') and gpu_status['qiskit']:
            label = 'Qiskit(AerSimulator GPU)' if fw_lower == 'qiskit' else 'Qrisp(AerSimulator GPU)'
            gpu_accelerated.append(label)
        elif fw_lower == 'cudaq' and gpu_status['cudaq']:
            gpu_accelerated.append('CUDA-Q(nvidia)')
    return len(gpu_accelerated) > 0, gpu_accelerated


# ─────────────────────────────────────────
# subprocess 격리 실행기
# ─────────────────────────────────────────

def _build_patch_script(algorithm_file: str, frameworks: list, device_type: str) -> str:
    """framework별 GPU/CPU 패치를 적용하는 subprocess 스크립트 생성"""
    alg_abs = os.path.abspath(algorithm_file)
    alg_dir = os.path.dirname(alg_abs)
    patches = []

    for fw in frameworks:
        fw_lower = fw.lower()

        if fw_lower == 'pennylane':
            if device_type == 'gpu':
                patches.append("""
# PennyLane → lightning.gpu 패치
import pennylane as qml
_orig_qnode_init = qml.QNode.__init__
_orig_qnode_call = qml.QNode.__call__
_gpu_logged = set()
_warned_state = [False]

def _patched_init(self, func, device, *args, **kwargs):
    _orig_qnode_init(self, func, device, *args, **kwargs)
    self._pending_gpu = True

def _patched_call(self, *args, **kwargs):
    if getattr(self, '_pending_gpu', False):
        self._pending_gpu = False
        is_state = False
        try:
            from pennylane.measurements import StateMP
            from pennylane.workflow import construct_batch
            batch_fn = construct_batch(self, level="top")
            batch, _ = batch_fn(*args, **kwargs)
            tapes = batch if isinstance(batch, (list, tuple)) else [batch]
            for tape in tapes:
                if any(isinstance(m, StateMP) for m in tape.measurements):
                    is_state = True
                    break
        except OSError:
            return _orig_qnode_call(self, *args, **kwargs)
        except Exception:
            pass
        if not is_state:
            try:
                wires = list(self.device.wires) if hasattr(self.device, 'wires') and len(self.device.wires) > 0 else list(range(10))
                self.device = qml.device('lightning.gpu', wires=wires)
                if hasattr(self, '_tape'):
                    self._tape = None
                fname = self.func.__name__ if hasattr(self, 'func') else '?'
                if fname not in _gpu_logged:
                    print(f"      ⚡ PennyLane [{fname}] → lightning.gpu", flush=True)
                    _gpu_logged.add(fname)
            except Exception as e:
                print(f"      ⚠  lightning.gpu 전환 실패: {e}", flush=True)
    return _orig_qnode_call(self, *args, **kwargs)

qml.QNode.__init__ = _patched_init
qml.QNode.__call__ = _patched_call
print("      ⚡ PennyLane → lightning.gpu 패치 적용", flush=True)
""")
            else:
                patches.append("""
print("      🖥️  PennyLane → default.qubit (CPU)", flush=True)
""")

        elif fw_lower in ('qiskit', 'qrisp'):
            if device_type == 'gpu':
                patches.append("""
import importlib
_gpu_logged = set()
_patch_targets = [
    ('qiskit_aer',            'AerSimulator'),
    ('qiskit_aer.primitives', 'Sampler'),
    ('qiskit_aer.primitives', 'Estimator'),
    ('qiskit.primitives',     'Sampler'),
    ('qiskit.primitives',     'StatevectorEstimator'),
    ('qiskit_ibm_runtime',    'SamplerV2'),
    ('qiskit_ibm_runtime',    'Estimator'),
]
for _mod_name, _cls_name in _patch_targets:
    try:
        _mod = importlib.import_module(_mod_name)
        _cls = getattr(_mod, _cls_name)
        _orig = _cls.__init__
        def _make_gpu_init(orig, cname):
            def _init(self, *args, **kwargs):
                if cname == 'AerSimulator':
                    kwargs['device'] = 'GPU'
                    try:
                        orig(self, *args, **kwargs)
                    except Exception:
                        kwargs.pop('device')
                        orig(self, *args, **kwargs)
                else:
                    try:
                        from qiskit_aer import AerSimulator as _Aer
                        _gpu = _Aer(method='automatic', device='GPU')
                        orig(self, *args, **kwargs)
                        for attr in ['_backend', 'backend', '_simulator']:
                            if hasattr(self, attr):
                                setattr(self, attr, _gpu)
                                break
                    except Exception:
                        orig(self, *args, **kwargs)
                if cname not in _gpu_logged:
                    print(f"      ⚡ {cname} → GPU 가속 적용", flush=True)
                    _gpu_logged.add(cname)
            return _init
        _cls.__init__ = _make_gpu_init(_orig, _cls_name)
    except (ImportError, AttributeError):
        pass
""")
            else:
                patches.append("""
print("      🖥️  Qiskit primitives → CPU", flush=True)
""")

        elif fw_lower == 'cudaq':
            target = 'nvidia' if device_type == 'gpu' else 'qpp-cpu'
            patches.append(f"""
class _MockBackend:
    def set_backend(self, *a, **kw): pass
    def __getattr__(self, n): return lambda *a, **kw: None
import sys as _sys
_sys.modules['cq_backend'] = _MockBackend()
import cudaq as _cudaq
try:
    _cudaq.set_target("{target}")
    print(f"      ⚡ CUDA-Q target → {target}", flush=True)
except Exception as _e:
    print(f"      ⚠  CUDA-Q target 실패: {{_e}}", flush=True)
_CUDAQ_MODE = True
""")

    patch_block = "\n".join(patches)
    perceval_pat = r"(TARGET\s*=\s*)[\"'][^\"']+[\"']"
    return f"""

import sys, os
sys.path.insert(0, r'{alg_dir}')

import matplotlib
matplotlib.use('Agg')

{patch_block}

# 알고리즘 실행
import runpy as _runpy
_use_cudaq = '_CUDAQ_MODE' in dir()
if _use_cudaq:
    # CUDAQ는 runpy로 실행 (소스 추적 필요)
    _runpy.run_path(r'{alg_abs}', run_name='__main__')
else:
    with open(r'{alg_abs}', 'r') as _f:
        _code = _f.read()
    import re as _re
    _code = _re.sub({repr(perceval_pat)}, r'\g<1>"local"', _code)
    exec(_code, {{'__name__': '__main__'}})
print("__BENCHMARK_OK__", flush=True)
"""


def run_once(algorithm_file: str, frameworks: list, device_type: str, label: str) -> dict:
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")

    script = _build_patch_script(algorithm_file, frameworks, device_type)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(script)
        tmp_path = f.name

    try:
        start = time.time()
        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True, text=True, timeout=600
        )
        elapsed = time.time() - start

        # 출력 로그 stderr로 전달
        if result.stdout:
            for line in result.stdout.splitlines():
                if line.strip():
                    print(f"    {line}", file=sys.stderr)

        if result.returncode == 0 and '__BENCHMARK_OK__' in result.stdout:
            return {'time': elapsed, 'status': 'completed', 'error': None}
        else:
            err = result.stderr[-500:] if result.stderr else f'returncode={result.returncode}'
            return {'time': elapsed, 'status': 'error', 'error': err}

    except subprocess.TimeoutExpired:
        return {'time': 600, 'status': 'timeout', 'error': 'timeout'}
    except Exception as e:
        return {'time': 0, 'status': 'error', 'error': str(e)}
    finally:
        os.unlink(tmp_path)


# ─────────────────────────────────────────
# 메인 벤치마크
# ─────────────────────────────────────────

def run_benchmark(algorithm_file: str, frameworks: list) -> dict:
    """
    CPU vs GPU 벤치마크 실행 후 결과 반환
    GPU는 워밍업 1회 + 측정 1회
    모든 실행은 subprocess 격리 (메모리 누적 없음)
    """
    gpu_status = check_gpu_available()
    gpu_usable, gpu_accelerated = resolve_gpu_usable(frameworks, gpu_status)

    print(f"\n  CUDA: {'✅' if gpu_status['cuda_available'] else '❌'}")
    print(f"  GPU 가속 대상: {' + '.join(gpu_accelerated) if gpu_accelerated else '없음'}")

    cpu_result = run_once(
        algorithm_file=algorithm_file,
        frameworks=frameworks,
        device_type='cpu',
        label="▶ CPU Execution",
    )

    gpu_result = None
    speedup    = None

    if gpu_usable:
        print("\n🔥 GPU Warm-up (결과 폐기)...")
        run_once(
            algorithm_file=algorithm_file,
            frameworks=frameworks,
            device_type='gpu',
            label="🔥 GPU Warm-up (discarded)",
        )
        gpu_result = run_once(
            algorithm_file=algorithm_file,
            frameworks=frameworks,
            device_type='gpu',
            label="▶ GPU Execution (measured)",
        )
        cpu_ok = cpu_result['status'] == 'completed'
        gpu_ok = gpu_result['status'] == 'completed'
        if cpu_ok and gpu_ok and cpu_result['time'] > 0 and gpu_result['time'] > 0:
            speedup = round(cpu_result['time'] / gpu_result['time'], 3)

    return {
        "frameworks":      frameworks,
        "gpu_available":   gpu_usable,
        "gpu_accelerated": gpu_accelerated,
        "cpu_time_sec":    round(cpu_result['time'], 3),
        "cpu_status":      cpu_result['status'],
        "cpu_error":       cpu_result.get('error'),
        "gpu_time_sec":    round(gpu_result['time'], 3) if gpu_result else None,
        "gpu_status":      gpu_result['status'] if gpu_result else None,
        "gpu_error":       gpu_result.get('error') if gpu_result else None,
        "speedup":         speedup,
        "verdict":         (f"GPU {speedup:.2f}x 빠름" if speedup and speedup >= 1.0
                            else f"CPU {round(1/speedup, 2):.2f}x 빠름" if speedup
                            else "비교 불가"),
    }