# uqi_noise.py
# 양자 노이즈 모델 - 캘리브레이션 기반 다중 SDK 노이즈 시뮬
# UQI (Universal Quantum Infrastructure)
#
# 지원 경로:
#   Qiskit NoiseModel (공통) → AerSimulator / PennyLane / CUDAQ
#   IBM:  FakeFez 등 → AerSimulator.from_backend() (T1/T2 포함)
#   IQM:  IQMFakeGarnet → AerSimulator.from_backend() (게이트 에러)
#   폴백: uqi_calibration 데이터 → NoiseModel 수동 구성

import sys
import numpy as np
from typing import Optional
from qiskit import QuantumCircuit, transpile


# ─────────────────────────────────────────────────────────
# Qiskit NoiseModel 구성
# ─────────────────────────────────────────────────────────

def build_noise_model_ibm(qpu_name: str):
    """
    IBM Fake 백엔드 → Qiskit NoiseModel
    T1/T2 + 게이트 에러 + RO 에러 완전 반영

    Returns:
        (noise_model, backend) 또는 (None, None)
    """
    from qiskit_aer.noise import NoiseModel

    fake_map = {
        "ibm_fez":       "FakeFez",
        "ibm_torino":    "FakeTorino",
        "ibm_brisbane":  "FakeBrisbane",
        "ibm_kyiv":      "FakeKyiv",
        "ibm_nazca":     "FakeNazca",
        "ibm_sherbrooke":"FakeSherbrooke",
    }

    fake_name = fake_map.get(qpu_name)
    if not fake_name:
        return None, None

    try:
        mod = __import__(
            "qiskit_ibm_runtime.fake_provider",
            fromlist=[fake_name]
        )
        backend = getattr(mod, fake_name)()
        noise_model = NoiseModel.from_backend(backend)
        return noise_model, backend
    except Exception as e:
        print(f"  [Noise] IBM Fake 백엔드 로드 실패: {e}")
        return None, None


def build_noise_model_iqm(qpu_name: str, calibration: dict = None):
    """
    IQM Fake 백엔드 → Qiskit NoiseModel
    게이트 에러 + RO 에러 반영 (T1/T2 미포함 경고)

    Returns:
        (noise_model, backend) 또는 (None, None)
    """
    from qiskit_aer.noise import NoiseModel

    fake_map = {
        "iqm_garnet":    "IQMFakeGarnet",
        "iqm_adonis":    "IQMFakeAdonis",
        "iqm_apollo":    "IQMFakeApollo",
        "iqm_aphrodite": "IQMFakeAphrodite",
        "iqm_deneb":     "IQMFakeDeneb",
    }

    fake_name = fake_map.get(qpu_name)
    if not fake_name:
        return None, None

    try:
        from iqm.qiskit_iqm.fake_backends import fake_garnet, fake_adonis, fake_apollo, fake_aphrodite, fake_deneb

        module_map = {
            "IQMFakeGarnet":    fake_garnet,
            "IQMFakeAdonis":    fake_adonis,
            "IQMFakeApollo":    fake_apollo,
            "IQMFakeAphrodite": fake_aphrodite,
            "IQMFakeDeneb":     fake_deneb,
        }
        mod = module_map.get(fake_name)
        if mod is None:
            return None, None
        backend = getattr(mod, fake_name)()
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            try:
                noise_model = NoiseModel.from_backend(backend)
            except Exception as e:
                print(f"  [Noise] IQM NoiseModel 구성 실패 ({e.__class__.__name__}), calibration 폴백")
                return None, None
            if w:
                print(f"  [Noise] IQM 경고: {w[0].message}")

        # T1/T2 보완 (IQMFakeGarnet QubitProperties 미포함)
        if calibration:
            t1_ms    = calibration.get("avg_t1_ms")
            t2_ms    = calibration.get("avg_t2_ms")
            q1_ns    = calibration.get("avg_1q_ns")
            q2_ns    = calibration.get("avg_2q_ns")
            n        = calibration.get("num_qubits") or 20

            if t1_ms and t2_ms:
                from qiskit_aer.noise import thermal_relaxation_error
                t1_ns = t1_ms * 1e6
                t2_ns = t2_ms * 1e6

                # 1Q thermal relaxation
                if q1_ns:
                    for gate in ['r', 'x', 'sx', 'rz', 'rx', 'ry']:
                        try:
                            th_err = thermal_relaxation_error(
                                t1_ns, t2_ns, q1_ns)
                            noise_model.add_all_qubit_quantum_error(
                                th_err, [gate])
                        except Exception:
                            pass

                # 2Q thermal relaxation
                if q2_ns:
                    try:
                        th_err_2q = thermal_relaxation_error(
                            t1_ns, t2_ns, q2_ns).expand(
                            thermal_relaxation_error(t1_ns, t2_ns, q2_ns))
                        noise_model.add_all_qubit_quantum_error(
                            th_err_2q, ['cz'])
                    except Exception:
                        pass

                print(f"  [Noise] IQM T1/T2 보완 완료 "
                      f"(T1={t1_ms*1000:.1f}μs T2={t2_ms*1000:.1f}μs)")

        return noise_model, backend
    except Exception as e:
        print(f"  [Noise] IQM Fake 백엔드 로드 실패: {e}")
        return None, None


def build_noise_model_from_calibration(calibration: dict,
                                       qpu_name: str):
    """
    uqi_calibration 데이터 → Qiskit NoiseModel 수동 구성
    Fake 백엔드 없는 QPU 폴백용

    반영 항목:
      depolarizing error  (1Q/2Q 게이트)
      thermal relaxation  (T1/T2 기반, 데이터 있을 때만)
      readout error       (RO 에러율)
    """
    from qiskit_aer.noise import (
        NoiseModel,
        depolarizing_error,
        thermal_relaxation_error,
        ReadoutError,
    )

    noise_model  = NoiseModel()
    basis_gates  = calibration.get("basis_gates") or []
    q1_error     = calibration.get("avg_1q_error")
    q2_error     = calibration.get("avg_2q_error")
    ro_error     = calibration.get("avg_ro_error")
    t1_ms        = calibration.get("avg_t1_ms")
    t2_ms        = calibration.get("avg_t2_ms")
    q1_dur_ns    = calibration.get("avg_1q_ns")
    q2_dur_ns    = calibration.get("avg_2q_ns")

    two_q_gates  = {'cx', 'cz', 'ecr', 'swap', 'iswap'}
    skip_gates   = {'measure', 'reset', 'delay', 'barrier',
                    'if_else', 'switch_case'}

    one_q_gates  = [g for g in basis_gates
                    if g not in two_q_gates and g not in skip_gates]
    two_q_list   = [g for g in basis_gates if g in two_q_gates]

    # ── 1Q 게이트 에러 ──
    for gate in one_q_gates:
        errors = []

        # Thermal relaxation (T1/T2)
        if t1_ms and t2_ms and q1_dur_ns:
            t1_ns = t1_ms * 1e6
            t2_ns = t2_ms * 1e6
            try:
                th_err = thermal_relaxation_error(
                    t1_ns, t2_ns, q1_dur_ns)
                errors.append(th_err)
            except Exception:
                pass

        # Depolarizing
        if q1_error and q1_error > 0:
            try:
                dp_err = depolarizing_error(q1_error, 1)
                errors.append(dp_err)
            except Exception:
                pass

        if errors:
            combined = errors[0]
            for e in errors[1:]:
                combined = combined.compose(e)
            noise_model.add_all_qubit_quantum_error(combined, [gate])

    # ── 2Q 게이트 에러 ──
    for gate in two_q_list:
        errors = []

        if t1_ms and t2_ms and q2_dur_ns:
            t1_ns = t1_ms * 1e6
            t2_ns = t2_ms * 1e6
            try:
                th_err = thermal_relaxation_error(
                    t1_ns, t2_ns, q2_dur_ns).expand(
                    thermal_relaxation_error(t1_ns, t2_ns, q2_dur_ns))
                errors.append(th_err)
            except Exception:
                pass

        if q2_error and q2_error > 0:
            try:
                dp_err = depolarizing_error(q2_error, 2)
                errors.append(dp_err)
            except Exception:
                pass

        if errors:
            combined = errors[0]
            for e in errors[1:]:
                combined = combined.compose(e)
            noise_model.add_all_qubit_quantum_error(combined, [gate])

    # ── Readout 에러 ──
    if ro_error and ro_error > 0:
        ro_err = ReadoutError([
            [1 - ro_error, ro_error],
            [ro_error, 1 - ro_error]
        ])
        noise_model.add_all_qubit_readout_error(ro_err)

    return noise_model


def build_noise_model(qpu_name: str,
                      calibration: dict = None):
    """
    QPU별 최적 경로로 Qiskit NoiseModel 구성

    우선순위:
      1. IBM Fake 백엔드 (T1/T2 완전 반영)
      2. IQM Fake 백엔드 (게이트 에러)
      3. calibration 수동 구성 (폴백)

    Returns:
        (noise_model, backend, source)
        source: 'ibm_fake' | 'iqm_fake' | 'calibration' | None
    """
    # IBM
    if "ibm" in qpu_name:
        nm, backend = build_noise_model_ibm(qpu_name)
        if nm:
            print(f"  [Noise] IBM Fake 백엔드 로드: {qpu_name}")
            return nm, backend, "ibm_fake"

    # IQM
    if "iqm" in qpu_name:
        nm, backend = build_noise_model_iqm(qpu_name, calibration)
        if nm:
            print(f"  [Noise] IQM Fake 백엔드 로드: {qpu_name}")
            return nm, backend, "iqm_fake"

    # 폴백: calibration 수동 구성
    if calibration:
        nm = build_noise_model_from_calibration(calibration, qpu_name)
        print(f"  [Noise] 캘리브레이션 기반 NoiseModel 구성: {qpu_name}")
        return nm, None, "calibration"

    print(f"  [Noise] ⚠ 노이즈 모델 구성 불가: {qpu_name}")
    return None, None, None


# ─────────────────────────────────────────────────────────
# SDK별 노이즈 시뮬 실행
# ─────────────────────────────────────────────────────────

def simulate_with_noise_qiskit(qc, noise_model, backend=None, shots=1024,
                                basis_gates=None):
    import subprocess
    import tempfile
    import os
    import json
    import pickle
    import base64

    # noise_model과 qc를 pickle로 직렬화해서 subprocess에 전달
    try:
        qc_b64 = base64.b64encode(pickle.dumps(qc)).decode()
        nm_b64 = base64.b64encode(pickle.dumps(noise_model)).decode()
        be_b64 = base64.b64encode(pickle.dumps(backend)).decode() if backend else ""
    except Exception as e:
        raise RuntimeError(f"직렬화 실패: {e}")

    script = f"""
import json, pickle, base64
from qiskit_aer import AerSimulator
from qiskit import transpile

qc           = pickle.loads(base64.b64decode({repr(qc_b64)}))
noise_model  = pickle.loads(base64.b64decode({repr(nm_b64)}))
backend_b64  = {repr(be_b64)}
backend      = pickle.loads(base64.b64decode(backend_b64)) if backend_b64 else None
shots        = {shots}

try:
    # 측정 없으면 추가
    if not any(inst.operation.name == 'measure' for inst in qc.data):
        qc = qc.copy()
        qc.measure_all()

    if backend is not None:
        sim  = AerSimulator(noise_model=noise_model)
        qc_t = transpile(qc, backend=backend)
    else:
        sim  = AerSimulator(noise_model=noise_model)
        qc_t = transpile(qc, sim)

    job    = sim.run(qc_t, shots=shots)
    counts = job.result().get_counts()
    # 비트열 공백 제거
    counts = {{k.replace(' ', ''): v for k, v in counts.items()}}
    print(json.dumps({{"ok": True, "counts": counts}}))
except Exception as e:
    print(json.dumps({{"ok": False, "error": str(e)}}))
"""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(script)
        tmp_path = f.name

    try:
        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True, text=True, timeout=300
        )
        stdout = result.stdout.strip()
        if not stdout:
            err = result.stderr[-300:] if result.stderr else f'returncode={result.returncode}'
            raise RuntimeError(f"노이즈 시뮬 subprocess 실패: {err}")

        # stdout 마지막 JSON 줄 파싱
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if line.startswith('{'):
                try:
                    data = json.loads(line)
                    if data.get('ok'):
                        return data['counts']
                    else:
                        raise RuntimeError(f"노이즈 시뮬 오류: {data.get('error')}")
                except json.JSONDecodeError:
                    continue

        raise RuntimeError(f"노이즈 시뮬 결과 파싱 실패: {stdout[:200]}")

    except subprocess.TimeoutExpired:
        raise RuntimeError("노이즈 시뮬 timeout (300s)")
    finally:
        os.unlink(tmp_path)


def simulate_with_noise_pennylane(qc: QuantumCircuit,
                                   noise_model,
                                   shots: int = 1024,
                                   calibration: dict = None) -> dict:
    """
    PennyLane default.mixed 노이즈 시뮬
    IBM FakeFez 등 복잡한 NoiseModel 변환 실패 시
    calibration 기반 depolarizing 노이즈로 폴백
    """
    import pennylane as qml
    from pennylane_qiskit import load

    n = qc.num_qubits
    dev = qml.device("default.mixed", wires=n, shots=shots)
    pl_circuit = load(qc)

    # PennyLane 노이즈 모델 구성 시도
    # IBM FakeFez 등 156q 노이즈 모델은 wire 범위 불일치로 실패
    # → calibration 기반 depolarizing으로 폴백
    pl_noise = None
    if calibration:
        print(f"  [Noise] PennyLane depolarizing 폴백 사용")
    else:
        try:
            pl_noise = qml.from_qiskit_noise(noise_model)
        except Exception as e:
            print(f"  [Noise] PennyLane 변환 실패: {e}")

    # 변환 실패 시 calibration 기반 depolarizing으로 폴백
    if pl_noise is None and calibration:
        q1_error = calibration.get("avg_1q_error", 0.001)
        q2_error = calibration.get("avg_2q_error", 0.01)
        print(f"  [Noise] PennyLane depolarizing 폴백 "
              f"(1q={q1_error:.4f} 2q={q2_error:.4f})")

        fcond_1q = qml.noise.op_in(
            ["RZ", "RX", "RY", "Hadamard", "PauliX"])
        fcond_2q = qml.noise.op_in(["CNOT", "CZ"])

        def noise_1q(op, **kwargs):
            qml.DepolarizingChannel(q1_error, wires=op.wires)

        def noise_2q(op, **kwargs):
            qml.DepolarizingChannel(q2_error, wires=op.wires[0])
            qml.DepolarizingChannel(q2_error, wires=op.wires[1])

        pl_noise = qml.NoiseModel(
            {fcond_1q: noise_1q, fcond_2q: noise_2q}
        )

    if pl_noise is None:
        raise ValueError("PennyLane 노이즈 모델 구성 실패")

    @qml.qnode(dev)
    def circuit():
        pl_circuit(wires=range(n))
        return qml.counts(wires=range(n))

    noisy_circuit = qml.add_noise(circuit, pl_noise)
    result = noisy_circuit()

    return {str(k): int(v) for k, v in result.items()}


def simulate_with_noise_cudaq(kernel_file: str,
                               qpu_name:    str,
                               calibration: dict,
                               shots:       int = 1024) -> dict:
    """
    CUDAQ KrausChannel 기반 노이즈 시뮬
    Qiskit NoiseModel → CUDAQ KrausChannel 변환

    Note: CUDAQ density-matrix-cpu 타겟 필요
    """
    import cudaq
    import importlib.util

    # ── Qiskit NoiseModel에서 Kraus 행렬 추출 ──
    nm, _, source = build_noise_model(qpu_name, calibration)
    cudaq_noise = cudaq.NoiseModel()

    if nm is not None:
        q1_error = calibration.get("avg_1q_error", 0.001)
        q2_error = calibration.get("avg_2q_error", 0.01)

        # Depolarizing 채널로 근사
        # 1Q 게이트 노이즈
        if q1_error > 0:
            dp1 = cudaq.DepolarizationChannel(q1_error)
            for gate in ['h', 'x', 'y', 'z', 'rx', 'ry', 'rz', 's', 't']:
                try:
                    cudaq_noise.add_all_qubit_channel(gate, dp1)
                except Exception:
                    pass

        if q2_error > 0:
            # 2Q depolarizing → 16개 Pauli 텐서곱 Kraus 채널
            p = q2_error
            I = np.eye(2, dtype=np.complex128)
            X = np.array([[0,1],[1,0]], dtype=np.complex128)
            Y = np.array([[0,-1j],[1j,0]], dtype=np.complex128)
            Z = np.array([[1,0],[0,-1]], dtype=np.complex128)
            paulis = [I, X, Y, Z]
            kraus_ops = [(np.sqrt(1 - 15*p/16) * np.kron(I, I)).astype(np.complex128)]
            for i in range(4):
                for j in range(4):
                    if i == 0 and j == 0:
                        continue
                    kraus_ops.append(
                        (np.sqrt(p/16) * np.kron(paulis[i], paulis[j])).astype(np.complex128))
            kraus_2q = cudaq.KrausChannel(kraus_ops)
            for gate in ['cx', 'cz']:
                try:
                    cudaq_noise.add_all_qubit_channel(gate, kraus_2q)
                except Exception:
                    pass

    # ── CUDAQ 커널 로드 및 실행 ──
    prev_target = None
    try:
        cudaq.set_target("density-matrix-cpu")
        cudaq.set_noise(cudaq_noise)

        spec   = importlib.util.spec_from_file_location("_algo", kernel_file)
        module = importlib.util.module_from_spec(spec)

        _blocked = False
        _orig_set_target = cudaq.set_target
        def _block(*a, **kw): pass
        cudaq.set_target = _block

        try:
            spec.loader.exec_module(module)
        finally:
            cudaq.set_target = _orig_set_target

        kernel = getattr(module, "kernel", None)
        if kernel is None:
            raise ValueError("kernel 함수를 찾을 수 없음")

        result = cudaq.sample(kernel, shots_count=shots)
        return dict(result.items())

    finally:
        cudaq.unset_noise()
        cudaq.reset_target()


# ─────────────────────────────────────────────────────────
# 메인 인터페이스
# ─────────────────────────────────────────────────────────

class UQINoise:
    """
    UQI 노이즈 모델 관리자

    - QPU별 노이즈 모델 구성 (Fake 백엔드 / 캘리브레이션 기반)
    - 다중 SDK 노이즈 시뮬 실행
    - 노이즈 시뮬 vs 이상적 시뮬 비교
    - 노이즈 시뮬 vs QPU 실행 결과 비교 (Fidelity)
    """

    def __init__(self, qpu_name: str, calibration: dict = None):
        self.qpu_name    = qpu_name
        self.calibration = calibration or {}

        # 노이즈 모델 초기화
        self.noise_model, self.backend, self.source = \
            build_noise_model(qpu_name, calibration)

        if self.noise_model:
            print(f"  [Noise] 초기화 완료: {qpu_name} "
                  f"(source={self.source})")
        else:
            print(f"  [Noise] ⚠ 노이즈 모델 없음: {qpu_name}")

    def simulate(self,
                 qc:    QuantumCircuit,
                 sdk:   str = "qiskit",
                 shots: int = 1024,
                 kernel_file: str = None) -> dict:
        """
        노이즈 포함 시뮬 실행

        Args:
            qc:          입력 회로 (CUDAQ는 kernel_file 사용)
            sdk:         'qiskit' | 'pennylane' | 'cudaq'
            shots:       샘플 수
            kernel_file: CUDAQ 커널 파일 경로

        Returns:
            counts dict
        """
        if self.noise_model is None:
            raise ValueError(f"노이즈 모델 없음: {self.qpu_name}")

        print(f"  [Noise] 시뮬 실행: sdk={sdk} shots={shots}")

        if sdk == "qiskit":
            return simulate_with_noise_qiskit(
                qc, self.noise_model, self.backend, shots,
                basis_gates=self.calibration.get("basis_gates"))

        elif sdk == "pennylane":
            return simulate_with_noise_pennylane(
                qc, self.noise_model, shots,
                calibration=self.calibration)

        elif sdk == "cudaq":
            if not kernel_file:
                raise ValueError("CUDAQ 노이즈 시뮬에는 kernel_file 필요")
            return simulate_with_noise_cudaq(
                kernel_file, self.qpu_name,
                self.calibration, shots)

        else:
            raise ValueError(f"미지원 SDK: {sdk}")

    def simulate_ideal(self, qc: QuantumCircuit, shots: int = 1024) -> dict:
        import subprocess
        import tempfile
        import os
        import json
        import pickle
        import base64

        try:
            qc_b64 = base64.b64encode(pickle.dumps(qc)).decode()
        except Exception as e:
            raise RuntimeError(f"직렬화 실패: {e}")

        script = f"""
import json, pickle, base64
from qiskit_aer import AerSimulator
from qiskit import transpile, QuantumCircuit as QC

qc    = pickle.loads(base64.b64decode({repr(qc_b64)}))
shots = {shots}

try:
    basis = ['cx', 'h', 'rz', 'rx', 'ry', 'x', 'y', 'z', 's', 't']
    qc_no_meas = qc.remove_final_measurements(inplace=False)
    qc_norm    = transpile(qc_no_meas, basis_gates=basis, optimization_level=0)

    used = set()
    for inst in qc_norm.data:
        if inst.operation.name == 'barrier':
            continue
        for q in inst.qubits:
            try:
                used.add(qc_norm.find_bit(q).index)
            except Exception:
                pass
    n_active = len(used) if used else qc_norm.num_qubits
    idx_map  = {{old: new for new, old in enumerate(sorted(used))}}

    qc_clean = QC(n_active, n_active)
    for inst in qc_norm.data:
        if inst.operation.name == 'barrier':
            continue
        new_qargs = []
        valid = True
        for q in inst.qubits:
            try:
                old_idx = qc_norm.find_bit(q).index
                new_qargs.append(qc_clean.qubits[idx_map[old_idx]])
            except Exception:
                valid = False
                break
        if valid:
            qc_clean.append(inst.operation, new_qargs)
    qc_clean.measure(range(n_active), range(n_active))

    sim    = AerSimulator()
    qc_t   = transpile(qc_clean, sim)
    job    = sim.run(qc_t, shots=shots)
    counts = job.result().get_counts()
    counts = {{k.replace(' ', ''): v for k, v in counts.items()}}
    print(json.dumps({{"ok": True, "counts": counts}}))
except Exception as e:
    print(json.dumps({{"ok": False, "error": str(e)}}))
"""

        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(script)
            tmp_path = f.name

        try:
            result = subprocess.run(
                [sys.executable, tmp_path],
                capture_output=True, text=True, timeout=300
            )
            stdout = result.stdout.strip()
            if not stdout:
                err = result.stderr[-300:] if result.stderr else f'returncode={result.returncode}'
                raise RuntimeError(f"이상적 시뮬 subprocess 실패: {err}")

            for line in reversed(stdout.splitlines()):
                line = line.strip()
                if line.startswith('{'):
                    try:
                        data = json.loads(line)
                        if data.get('ok'):
                            return data['counts']
                        else:
                            raise RuntimeError(f"이상적 시뮬 오류: {data.get('error')}")
                    except json.JSONDecodeError:
                        continue

            raise RuntimeError(f"이상적 시뮬 결과 파싱 실패: {stdout[:200]}")

        except subprocess.TimeoutExpired:
            raise RuntimeError("이상적 시뮬 timeout (300s)")
        finally:
            os.unlink(tmp_path)

    def compare(self,
                counts_a: dict,
                counts_b: dict,
                shots:    int = 1024,
                label_a:  str = "A",
                label_b:  str = "B") -> dict:
        """
        두 결과 분포 비교 (TVD / Fidelity)
        비트열 형식 정규화 (공백 제거)
        """
        def normalize(counts):
            result = {}
            for k, v in counts.items():
                key = k.replace(" ", "")
                result[key] = result.get(key, 0) + v
            return result

        counts_a = normalize(counts_a)
        counts_b = normalize(counts_b)

        all_keys = set(counts_a) | set(counts_b)
        total_a  = sum(counts_a.values())
        total_b  = sum(counts_b.values())

        tvd = sum(
            abs(counts_a.get(k, 0) / total_a
                - counts_b.get(k, 0) / total_b)
            for k in all_keys
        ) / 2

        fidelity = sum(
            np.sqrt(counts_a.get(k, 0) / total_a
                    * counts_b.get(k, 0) / total_b)
            for k in all_keys
        ) ** 2

        dominant_a = max(counts_a, key=counts_a.get) if counts_a else None
        dominant_b = max(counts_b, key=counts_b.get) if counts_b else None

        result = {
            "tvd":        round(tvd, 4),
            "fidelity":   round(fidelity, 4),
            "dominant_a": dominant_a,
            "dominant_b": dominant_b,
            "label_a":    label_a,
            "label_b":    label_b,
        }

        print(f"  [Noise] 비교 {label_a} vs {label_b}: "
              f"TVD={tvd:.4f} Fidelity={fidelity:.4f}")
        return result

    def run_comparison(self, qc: QuantumCircuit, shots: int = 1024) -> dict:
        # 큐비트 수 상한 체크 (OOM 방지)
        _MAX_NOISE_QUBITS = 20

        # active 큐비트 수 계산 (IBM 가상 할당 우회)
        try:
            from qiskit import transpile as _tpile
            _basis = ['cx', 'h', 'rz', 'rx', 'ry', 'x', 'y', 'z', 's', 't']
            _qc_nm = qc.remove_final_measurements(inplace=False)
            _qc_norm = _tpile(_qc_nm, basis_gates=_basis, optimization_level=0)
            _used = set()
            for _inst in _qc_norm.data:
                if _inst.operation.name == 'barrier':
                    continue
                for _q in _inst.qubits:
                    _used.add(_qc_norm.find_bit(_q).index)
            n_active = len(_used) if _used else qc.num_qubits
        except Exception:
            n_active = qc.num_qubits

        if n_active > _MAX_NOISE_QUBITS:
            raise ValueError(
                f"노이즈 시뮬 큐비트 상한 초과: {n_active}q (active) > {_MAX_NOISE_QUBITS}q "
                f"(statevector 메모리 보호)"
            )

        print(f"  [Noise] 이상적 시뮬 실행...")
        ideal_counts = self.simulate_ideal(qc, shots)

        print(f"  [Noise] 노이즈 시뮬 실행...")
        noise_counts = self.simulate(qc, sdk="qiskit", shots=shots)

        comparison = self.compare(
            ideal_counts, noise_counts, shots,
            label_a="ideal", label_b="noise"
        )

        return {
            "ideal_counts": ideal_counts,
            "noise_counts": noise_counts,
            "comparison":   comparison,
        }