# uqi_calibration.py
# QPU 캘리브레이션 수집 및 관리
# UQI (Universal Quantum Infrastructure)

import os
import json
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv


CALIBRATION_FILE = "uqi_calibration.json"
CALIBRATION_TTL = {
    "ibm":      timedelta(hours=24),
    "iqm":      timedelta(hours=12),
    "ionq":     timedelta(hours=48),
    "rigetti":  timedelta(hours=24),
    "quera":    timedelta(hours=48),
    "quandela": timedelta(hours=72),
}


class UQICalibration:
    """
    QPU 캘리브레이션 수집 및 관리
    - 벤더별 API에서 실시간 수집
    - JSON 기반 이력 저장 (시계열)
    - TTL 기반 캐시 만료 판단
    - 트랜스파일 전 자동 호출 인터페이스
    """

    _SYNC_CACHE = {}

    def __init__(self, calibration_file: str = CALIBRATION_FILE):
        self.calibration_file = calibration_file
        self.data = self._load()
        env_path = Path(__file__).parent / ".env"
        load_dotenv(dotenv_path=env_path)

    # ─────────────────────────────────────────
    # 저장 / 로드
    # ─────────────────────────────────────────

    def _load(self) -> dict:
        if Path(self.calibration_file).exists():
            try:
                with open(self.calibration_file, 'r') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save(self):
        with open(self.calibration_file, 'w') as f:
            json.dump(self.data, f, indent=2, default=str)

    # ─────────────────────────────────────────
    # TTL 판단
    # ─────────────────────────────────────────

    def _is_expired(self, qpu_name: str) -> bool:
        entry = self.data.get(qpu_name, {})
        last_updated = entry.get("last_updated")
        if not last_updated:
            return True

        try:
            updated_at = datetime.fromisoformat(last_updated)
        except Exception:
            return True

        vendor = self._detect_vendor(qpu_name)
        ttl = CALIBRATION_TTL.get(vendor, timedelta(hours=24))
        return datetime.now() - updated_at > ttl

    def _detect_vendor(self, qpu_name: str) -> str:
        if 'ibm' in qpu_name:
            return 'ibm'
        elif 'iqm' in qpu_name:
            return 'iqm'
        elif 'ionq' in qpu_name:
            return 'ionq'
        elif 'rigetti' in qpu_name or 'ankaa' in qpu_name:
            return 'rigetti'
        elif 'quera' in qpu_name or 'aquila' in qpu_name:
            return 'quera'
        elif qpu_name.startswith('qpu:') or qpu_name.startswith('sim:'):
            return 'quandela'
        return 'unknown'

    # ─────────────────────────────────────────
    # 외부 인터페이스
    # ─────────────────────────────────────────

    def get(self, qpu_name: str, force_sync: bool = False) -> dict:
        """
        캘리브레이션 데이터 반환
        TTL 만료 또는 force_sync 시 API에서 재수집
        트랜스파일 전 자동 호출 용도
        """
        if force_sync or self._is_expired(qpu_name):
            self.sync(qpu_name)
        return self.data.get(qpu_name, {})

    def sync(self, qpu_name: str) -> bool:
        """단일 QPU 캘리브레이션 동기화 (1시간 캐시)"""
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        last = UQICalibration._SYNC_CACHE.get(qpu_name)
        if last and (now - last) < timedelta(hours=1):
            print(f"  [Calibration] {qpu_name} 캐시 유효 ({int((now-last).seconds/60)}분 전 동기화), 스킵")
            return True

        vendor = self._detect_vendor(qpu_name)
        try:
            if vendor == 'ibm':
                ok = self._sync_ibm(qpu_name)
            elif vendor == 'iqm':
                ok = self._sync_iqm(qpu_name)
            elif vendor == 'ionq':
                ok = self._sync_ionq(qpu_name)
            elif vendor == 'rigetti':
                ok = self._sync_rigetti(qpu_name)
            elif vendor == 'quera':
                ok = self._sync_quera(qpu_name)
            elif vendor == 'quandela':
                ok = self._sync_quandela(qpu_name)
            else:
                print(f"  [Calibration] 미지원 벤더: {qpu_name}")
                return False

            if ok:
                self._append_history(qpu_name)
                self._save()
                UQICalibration._SYNC_CACHE[qpu_name] = now
                print(f"  [Calibration] ✓ {qpu_name} 동기화 완료")
            else:
                print(f"  [Calibration] ⚠ {qpu_name} 동기화 실패 → 캐시 사용")
            return ok

        except Exception as e:
            print(f"  [Calibration] ✗ {qpu_name} 오류: {e}")
            return False

    def sync_all(self, qpu_names: list):
        """복수 QPU 일괄 동기화"""
        print(f"  [Calibration] 캘리브레이션 동기화 시작 ({len(qpu_names)}개)")
        for name in qpu_names:
            self.sync(name)
        print()

    # ─────────────────────────────────────────
    # 이력 저장
    # ─────────────────────────────────────────

    def _append_history(self, qpu_name: str):
        """현재 캘리브레이션 스냅샷을 이력에 추가"""
        entry = self.data.get(qpu_name, {})
        if not entry:
            return

        history = self.data.setdefault(f"{qpu_name}__history", [])
        snapshot = {k: v for k, v in entry.items() if k != '__history'}
        history.append(snapshot)

        # 최대 90일치 보존
        if len(history) > 2160:  # 24h/cycle * 90days
            history.pop(0)

    def get_history(self, qpu_name: str) -> list:
        """캘리브레이션 이력 반환"""
        return self.data.get(f"{qpu_name}__history", [])

    # ─────────────────────────────────────────
    # IBM 동기화
    # ─────────────────────────────────────────

    def _sync_ibm(self, qpu_name: str) -> bool:
        from qiskit_ibm_runtime import QiskitRuntimeService

        token = os.getenv("IBM_QUANTUM_TOKEN")
        if not token:
            return False

        try:
            service = QiskitRuntimeService(
                channel="ibm_quantum_platform", token=token
            )
            backend = service.backend(qpu_name)
            props   = backend.properties()
            n       = backend.num_qubits

            # ── per-qubit 수집 ──
            t1_list, t2_list = [], []
            ro_list = []
            q1_err_list, q1_dur_list = [], []

            for i in range(n):
                try:
                    t1 = props.qubit_property(i, 'T1')[0]
                    if t1: t1_list.append(t1 * 1e3)
                except Exception: pass
                try:
                    t2 = props.qubit_property(i, 'T2')[0]
                    if t2 and t2 > 0: t2_list.append(t2 * 1e3)
                except Exception: pass
                try:
                    ro = props.readout_error(i)
                    if ro is not None: ro_list.append(ro)
                except Exception: pass
                for g in ['sx', 'x', 'rz']:
                    try:
                        err = props.gate_error(g, [i])
                        if err is not None:
                            q1_err_list.append(err)
                            break
                    except Exception: pass
                for g in ['sx', 'x', 'rz']:
                    try:
                        dur = props.gate_length(g, [i])
                        if dur and dur > 0:
                            q1_dur_list.append(dur * 1e9)
                            break
                    except Exception: pass

            # ── per-edge 수집 ──
            q2_err_list, q2_dur_list = [], []
            coupling_map = backend.coupling_map
            if coupling_map:
                for edge in coupling_map.get_edges():
                    for g in ['ecr', 'cx', 'cz']:
                        try:
                            err = props.gate_error(g, list(edge))
                            if err is not None:
                                q2_err_list.append(err)
                                break
                        except Exception: pass
                    for g in ['ecr', 'cx', 'cz']:
                        try:
                            dur = props.gate_length(g, list(edge))
                            if dur and dur > 0:
                                q2_dur_list.append(dur * 1e9)
                                break
                        except Exception: pass

            # ── basis gates / coupling map ──
            basis_gates, coupling_edges = [], []
            if hasattr(backend, 'target') and backend.target:
                basis_gates    = list(backend.target.operation_names)
                coupling_edges = [list(e) for e in
                                  backend.target.build_coupling_map().get_edges()]
            elif hasattr(backend, 'configuration'):
                conf = backend.configuration()
                basis_gates    = conf.basis_gates
                coupling_edges = list(conf.coupling_map) if conf.coupling_map else []

            self.data[qpu_name] = {
                "vendor":        "ibm",
                "num_qubits":    n,
                "avg_t1_ms":     float(np.mean(t1_list))     if t1_list    else None,
                "avg_t2_ms":     float(np.mean(t2_list))     if t2_list    else None,
                "avg_ro_error":  float(np.mean(ro_list))     if ro_list    else None,
                "avg_1q_error":  float(np.mean(q1_err_list)) if q1_err_list else None,
                "avg_2q_error":  float(np.mean(q2_err_list)) if q2_err_list else None,
                "avg_1q_ns":     float(np.mean(q1_dur_list)) if q1_dur_list else None,
                "avg_2q_ns":     float(np.mean(q2_dur_list)) if q2_dur_list else None,
                "basis_gates":   basis_gates,
                "coupling_map":  coupling_edges,
                "last_updated":  datetime.now().isoformat(),
            }
            return True

        except Exception as e:
            print(f"      ⚠ IBM sync error: {e}")
            return False

    # ─────────────────────────────────────────
    # IQM 동기화
    # ─────────────────────────────────────────

    def _sync_iqm(self, qpu_name: str) -> bool:
        from iqm.iqm_client import IQMClient

        token = os.getenv("IQM_QUANTUM_TOKEN")
        if not token:
            return False

        try:
            device_name = qpu_name.split('_')[-1]
            base_url    = "https://resonance.meetiqm.com"
            client      = IQMClient(base_url,
                                    quantum_computer=device_name,
                                    token=token)

            import io, contextlib
            _stderr_buf = io.StringIO()
            with contextlib.redirect_stderr(_stderr_buf):
                metrics = client.get_calibration_quality_metrics()
            arch    = client.get_dynamic_quantum_architecture()
            qubits  = arch.qubits

            t1_list, t2_list = [], []
            for q in qubits:
                try:
                    t1_d, t2_d = metrics.get_coherence_times(components=[q])
                    t1 = t1_d.get(q)
                    t2 = t2_d.get(q)
                    if t1: t1_list.append(t1 * 1e3)
                    if t2: t2_list.append(t2 * 1e3)
                except Exception: pass

            # ── CZ 엣지 수집 ──
            qubit_pairs, q2_fid_list, q2_dur_list = [], [], []
            if 'cz' in arch.gates:
                cz = arch.gates['cz']
                override = cz.override_default_implementation
                # 기본 impl_name 동적으로 추출
                default_cz_impl = list(cz.implementations.keys())[0] if cz.implementations else 'crf_crf'
                for impl_name, impl_info in cz.implementations.items():
                    for locus in impl_info.loci:
                        if len(locus) == 2:
                            qubit_pairs.append(tuple(locus))

                for pair in qubit_pairs:
                    impl = override.get(pair, default_cz_impl)
                    try:
                        fid = metrics.get_gate_fidelity('cz', impl, pair)
                        if fid: q2_fid_list.append(fid)
                    except Exception: pass
                    try:
                        dur = metrics.get_gate_duration('cz', impl, pair)
                        if dur and dur > 0:
                            q2_dur_list.append(dur * 1e9)
                    except Exception: pass

            # ── PRX 1Q 수집 ──
            # PRX 기본 impl_name 동적으로 추출
            default_prx_impl = 'drag_crf'
            if 'prx' in arch.gates:
                prx_impls = list(arch.gates['prx'].implementations.keys())
                if prx_impls:
                    default_prx_impl = prx_impls[0]

            q1_fid_list, q1_dur_list = [], []
            for q in qubits:
                try:
                    fid = metrics.get_gate_fidelity('prx', default_prx_impl, (q,))
                    if fid: q1_fid_list.append(fid)
                except Exception: pass
                try:
                    dur = metrics.get_gate_duration('prx', default_prx_impl, (q,))
                    if dur and dur > 0:
                        q1_dur_list.append(dur * 1e9)
                except Exception: pass

            # ── readout 오류 수집 ──
            ro_list = []
            for q in qubits:
                try:
                    err = metrics.get_readout_error(q)
                    if err is not None: ro_list.append(err)
                except Exception: pass

            coupling_edges = [[p[0], p[1]] for p in qubit_pairs]

            self.data[qpu_name] = {
                "vendor":       "iqm",
                "num_qubits":   len(qubits),
                "avg_t1_ms":    float(np.median(t1_list))     if t1_list    else None,
                "avg_t2_ms":    float(np.median(t2_list))     if t2_list    else None,
                "avg_ro_error": float(np.median(ro_list))     if ro_list    else None,
                "avg_1q_error": float(1 - np.median(q1_fid_list)) if q1_fid_list else None,
                "avg_2q_error": float(1 - np.median(q2_fid_list)) if q2_fid_list else None,
                "avg_1q_ns":    float(np.median(q1_dur_list)) if q1_dur_list else None,
                "avg_2q_ns":    float(np.median(q2_dur_list)) if q2_dur_list else None,
                "basis_gates":  ["r", "cz", "id"],
                "coupling_map": coupling_edges,
                "last_updated": datetime.now().isoformat(),
            }
            return True

        except Exception as e:
            print(f"      ⚠ IQM sync error: {e}")
            return False

    # ─────────────────────────────────────────
    # IonQ 동기화
    # ─────────────────────────────────────────

    def _sync_ionq(self, qpu_name: str) -> bool:
        try:
            from braket.aws import AwsDevice, AwsSession
            import boto3

            boto_session = boto3.Session(
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
                region_name="us-east-1"
            )
            aws_session = AwsSession(boto_session=boto_session)
            arn    = os.getenv("IONQ_FORTE_ARN")
            device = AwsDevice(arn, aws_session=aws_session)
            props  = device.properties.dict()
            paradigm = props.get('paradigm', {})

            t1_us        = paradigm.get('coherenceTimes', {}).get('T1', 1e8)
            two_q_fid    = paradigm.get('twoQubitGateFidelity')
            gate_time_us = paradigm.get('twoQubitGateTime', 600)
            n_qubits     = device.properties.paradigm.qubitCount

            self.data[qpu_name] = {
                "vendor":       "ionq",
                "num_qubits":   n_qubits,
                "avg_t1_ms":    t1_us / 1000.0,
                "avg_t2_ms":    None,
                "avg_ro_error": None,
                "avg_1q_error": 0.001,
                "avg_2q_error": float(1 - two_q_fid) if two_q_fid else 0.01,
                "avg_1q_ns":    100.0,
                "avg_2q_ns":    gate_time_us * 1000.0,
                "basis_gates":  ["rz", "sx", "x", "cx"],
                "coupling_map": "all_to_all",
                "last_updated": datetime.now().isoformat(),
            }
            return True

        except Exception as e:
            print(f"      ⚠ IonQ sync error: {e}")
            return False

    # ─────────────────────────────────────────
    # Rigetti 동기화
    # ─────────────────────────────────────────

    def _sync_rigetti(self, qpu_name: str) -> bool:
        try:
            from braket.aws import AwsDevice, AwsSession
            import boto3

            boto_session = boto3.Session(
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
                region_name="us-west-1"
            )
            aws_session = AwsSession(boto_session=boto_session)
            arn    = os.getenv("RIGETTI_ANKAA3_ARN")
            device = AwsDevice(arn, aws_session=aws_session)
            props  = device.properties.dict()

            paradigm = props.get('paradigm', {})
            n_qubits = paradigm.get('qubitCount', 79)

            # Rigetti 1Q/2Q fidelity
            q1_fid = paradigm.get('oneQubitGateFidelity')
            q2_fid = paradigm.get('twoQubitGateFidelity')

            # connectivity
            connectivity = paradigm.get('connectivity', {})
            coupling_map = connectivity.get('connectivityGraph', {})
            edges = []
            for src, dsts in coupling_map.items():
                for dst in dsts:
                    edges.append([int(src), int(dst)])

            self.data[qpu_name] = {
                "vendor":       "rigetti",
                "num_qubits":   n_qubits,
                "avg_t1_ms":    None,
                "avg_t2_ms":    None,
                "avg_ro_error": None,
                "avg_1q_error": float(1 - q1_fid) if q1_fid else 0.005,
                "avg_2q_error": float(1 - q2_fid) if q2_fid else 0.02,
                "avg_1q_ns":    50.0,
                "avg_2q_ns":    180.0,
                "basis_gates":  ["rx", "rz", "cz"],
                "coupling_map": edges,
                "last_updated": datetime.now().isoformat(),
            }
            return True

        except Exception as e:
            print(f"      ⚠ Rigetti sync error: {e}")
            return False

    # ─────────────────────────────────────────
    # QuEra 동기화
    # ─────────────────────────────────────────

    def _sync_quera(self, qpu_name: str) -> bool:
        try:
            from braket.aws import AwsDevice, AwsSession
            import boto3

            boto_session = boto3.Session(
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
                region_name="us-east-1"
            )
            aws_session = AwsSession(boto_session=boto_session)
            arn    = os.getenv("QuEra_Aquila_ARN")
            device = AwsDevice(arn, aws_session=aws_session)
            props  = device.properties.dict()

            paradigm = props.get('paradigm', {})
            n_qubits = paradigm.get('qubitCount', 256)

            # Rydberg 파라미터
            rydberg = paradigm.get('rydberg', {})
            c6_coefficient = rydberg.get('c6Coefficient')
            rydberg_global = rydberg.get('rydbergGlobal', {})
            coherence_time = rydberg_global.get('phaseCoherenceTime')    # seconds (없을 수 있음)

            # rabiFrequencyRange는 tuple 또는 dict, 값은 Decimal일 수 있음
            rabi_range = rydberg_global.get('rabiFrequencyRange')
            rabi_freq_max = None
            if rabi_range is not None:
                if isinstance(rabi_range, (tuple, list)) and len(rabi_range) >= 2:
                    rabi_freq_max = float(rabi_range[1])
                elif isinstance(rabi_range, dict):
                    rabi_freq_max = float(rabi_range.get('max', 0) or 0)

            # performance 필드에서 coherence time 대체값 시도
            performance = paradigm.get('performance', {})
            if not coherence_time:
                coherence_time = performance.get('braggDiffactionEfficiency') or None

            self.data[qpu_name] = {
                "vendor":            "quera",
                "type":              "neutral_atom",
                "num_qubits":        n_qubits,
                "avg_t1_ms":         float(coherence_time * 1e3) if coherence_time else 75.0,
                "avg_t2_ms":         None,
                "avg_1q_error":      None,
                "avg_2q_error":      None,
                "avg_ro_error":      None,
                "avg_1q_ns":         None,
                "avg_2q_ns":         None,
                "basis_gates":       ["rydberg_global"],
                "coupling_map":      "all_to_all",
                "c6_coefficient":    float(c6_coefficient) if c6_coefficient else None,
                "rabi_freq_max_mhz": float(rabi_freq_max) / 1e6 if rabi_freq_max else 15.8,
                "last_updated":      datetime.now().isoformat(),
            }
            return True

        except Exception as e:
            print(f"      ⚠ QuEra sync error: {e}")
            return False

    def _sync_quandela(self, qpu_name: str) -> bool:
        try:
            import perceval as pcvl

            token = os.getenv("QUANDELA_TOKEN")
            if not token:
                return False

            processor = pcvl.RemoteProcessor(qpu_name, token=token)
            specs      = processor.specs
            constraints = specs.get('constraints', {})
            parameters  = specs.get('parameters', {})

            self.data[qpu_name] = {
                "vendor":            "quandela",
                "max_mode_count":    constraints.get('max_mode_count', 24),
                "max_photon_count":  constraints.get('max_photon_count', 6),
                "avg_transmittance": parameters.get('transmittance', 0.06),
                "avg_hom":           parameters.get('HOM', 0.92),
                "avg_g2":            parameters.get('g2', 0.003),
                "last_updated":      datetime.now().isoformat(),
            }
            return True

        except Exception as e:
            print(f"      ⚠ Quandela sync error: {e}")
            return False

    # ─────────────────────────────────────────
    # 트랜스파일 파라미터 반환
    # ─────────────────────────────────────────

    def get_transpile_params(self, qpu_name: str) -> dict:
        """
        트랜스파일러에 전달할 캘리브레이션 파라미터 반환
        uqi_executor_ibm / uqi_executor_iqm에서 호출
        """
        cal = self.get(qpu_name)
        if not cal:
            return {}

        return {
            "num_qubits":    cal.get("num_qubits"),
            "basis_gates":   cal.get("basis_gates"),
            "coupling_map":  cal.get("coupling_map"),
            "avg_1q_error":  cal.get("avg_1q_error"),
            "avg_2q_error":  cal.get("avg_2q_error"),
            "avg_ro_error":  cal.get("avg_ro_error"),
            "avg_t1_ms":     cal.get("avg_t1_ms"),
            "avg_t2_ms":     cal.get("avg_t2_ms"),
            "avg_1q_ns":     cal.get("avg_1q_ns"),
            "avg_2q_ns":     cal.get("avg_2q_ns"),
            "last_updated":  cal.get("last_updated"),
        }

    # ─────────────────────────────────────────
    # 요약 출력
    # ─────────────────────────────────────────

    def print_summary(self, qpu_name: str):
        cal = self.data.get(qpu_name, {})
        if not cal:
            print(f"  [Calibration] {qpu_name}: 데이터 없음")
            return

        vendor = cal.get("vendor", "unknown")
        print(f"  [Calibration] {qpu_name} ({vendor})")
        print(f"    업데이트:    {cal.get('last_updated', 'N/A')}")

        if vendor == 'quandela':
            print(f"    모드 수:     {cal.get('max_mode_count')}")
            print(f"    광자 수:     {cal.get('max_photon_count')}")
            print(f"    투과율:      {cal.get('avg_transmittance')}")
            print(f"    HOM:         {cal.get('avg_hom')}")
        else:
            print(f"    큐비트 수:   {cal.get('num_qubits')}")
            print(f"    T1 (ms):     {cal.get('avg_t1_ms')}")
            print(f"    T2 (ms):     {cal.get('avg_t2_ms')}")
            print(f"    1Q 에러:     {cal.get('avg_1q_error')}")
            print(f"    2Q 에러:     {cal.get('avg_2q_error')}")
            print(f"    RO 에러:     {cal.get('avg_ro_error')}")
            print(f"    1Q 시간(ns): {cal.get('avg_1q_ns')}")
            print(f"    2Q 시간(ns): {cal.get('avg_2q_ns')}")
    
    # ─────────────────────────────────────────
    # 가용 QPU 동적 조회
    # ─────────────────────────────────────────

    def get_available_qpus(self) -> list:
        """
        벤더별 가용 QPU 동적 조회
        IBM: service.backends()로 전체 목록 + 상태 조회
        IQM: 알려진 장비 목록에 연결 시도해서 온라인 여부 확인
        결과를 RAG 이력에 기록
        """
        available = []
        self._qpu_status_cache = {}  # {qpu_name: {available, pending_jobs, note}}

        # ── IBM ──
        try:
            from qiskit_ibm_runtime import QiskitRuntimeService
            token = os.getenv("IBM_QUANTUM_TOKEN")
            if token:
                service = QiskitRuntimeService(
                    channel="ibm_quantum_platform", token=token)
                backends = service.backends()
                for b in backends:
                    try:
                        status = b.status()
                        operational = status.operational
                        pending_jobs = status.pending_jobs
                        note = ""
                        if not operational:
                            note = "offline"
                        elif pending_jobs > 20:
                            note = f"queue {pending_jobs} jobs"
                        self._qpu_status_cache[b.name] = {
                            "available":    operational,
                            "pending_jobs": pending_jobs,
                            "note":         note,
                            "vendor":       "ibm",
                        }
                        if operational:
                            available.append(b.name)
                        print(f"  [QPU] IBM {b.name}: operational={operational} pending={pending_jobs}")
                    except Exception as e:
                        print(f"  [QPU] IBM {b.name} 상태 조회 실패: {e}")
        except Exception as e:
            print(f"  [QPU] IBM 조회 실패: {e}")

        # ── IQM ──
        IQM_KNOWN = ["garnet", "emerald", "sirius"]
        token = os.getenv("IQM_QUANTUM_TOKEN")
        if token:
            for device_name in IQM_KNOWN:
                qpu_name = f"iqm_{device_name}"
                try:
                    from iqm.iqm_client import IQMClient
                    from datetime import datetime, timezone, timedelta
                    client = IQMClient(
                        "https://resonance.meetiqm.com",
                        quantum_computer=device_name,
                        token=token
                    )
                    health = client.get_health()
                    healthy = health.get("healthy", False)

                    # calibration timestamp 기준 staleness 체크 (24시간 이상 미갱신 → offline 판단)
                    stale = True
                    try:
                        from datetime import timezone
                        import io, contextlib
                        _stderr_buf = io.StringIO()
                        with contextlib.redirect_stderr(_stderr_buf):
                            metrics = client.get_calibration_quality_metrics()
                        # controllers -> QB1/TC17 -> flux -> voltage(ObservationLite)
                        cal_time = None
                        for section_val in metrics.values():
                            if not isinstance(section_val, dict):
                                continue
                            for item_val in section_val.values():
                                if not isinstance(item_val, dict):
                                    continue
                                for leaf_val in item_val.values():
                                    # leaf_val이 dict인 경우 (flux: {voltage: ObservationLite})
                                    if isinstance(leaf_val, dict):
                                        for obs in leaf_val.values():
                                            if hasattr(obs, 'created_timestamp'):
                                                cal_time = obs.created_timestamp
                                                break
                                    # leaf_val이 직접 ObservationLite인 경우
                                    elif hasattr(leaf_val, 'created_timestamp'):
                                        cal_time = leaf_val.created_timestamp
                                    if cal_time:
                                        break
                                if cal_time:
                                    break
                            if cal_time:
                                break
                        if cal_time:
                            if cal_time.tzinfo is None:
                                cal_time = cal_time.replace(tzinfo=timezone.utc)
                            stale = (datetime.now(timezone.utc) - cal_time) > timedelta(hours=24)
                    except Exception:
                        stale = True

                    is_available = healthy and not stale
                    note = "" if is_available else (
                        "stale calibration (offline)" if healthy else "unhealthy")

                    arch = client.get_dynamic_quantum_architecture()
                    n_qubits = len(arch.qubits)

                    self._qpu_status_cache[qpu_name] = {
                        "available":    is_available,
                        "pending_jobs": None,
                        "note":         note,
                        "vendor":       "iqm",
                        "num_qubits":   n_qubits,
                    }
                    if is_available:
                        available.append(qpu_name)
                    print(f"  [QPU] IQM {qpu_name}: healthy={healthy} stale={stale} online={is_available}")
                except Exception as e:
                    self._qpu_status_cache[qpu_name] = {
                        "available":    False,
                        "pending_jobs": None,
                        "note":         "offline or unreachable",
                        "vendor":       "iqm",
                    }
                    print(f"  [QPU] IQM {qpu_name}: offline ({e})")

        # ── Braket (IonQ / Rigetti / QuEra) ──
        BRAKET_DEVICES = [
            {"qpu_name": "ionq_forte1",    "arn_env": "IONQ_FORTE_ARN",    "region": "us-east-1", "vendor": "ionq"},
            {"qpu_name": "rigetti_ankaa3", "arn_env": "RIGETTI_ANKAA3_ARN","region": "us-west-1", "vendor": "rigetti"},
            {"qpu_name": "quera_aquila",   "arn_env": "QuEra_Aquila_ARN",  "region": "us-east-1", "vendor": "quera"},
        ]
        aws_key    = os.getenv("AWS_ACCESS_KEY_ID")
        aws_secret = os.getenv("AWS_SECRET_ACCESS_KEY")
        if aws_key and aws_secret:
            for bd in BRAKET_DEVICES:
                qpu_name = bd["qpu_name"]
                try:
                    from braket.aws import AwsDevice, AwsSession
                    import boto3
                    boto_session = boto3.Session(
                        aws_access_key_id=aws_key,
                        aws_secret_access_key=aws_secret,
                        region_name=bd["region"]
                    )
                    aws_session = AwsSession(boto_session=boto_session)
                    arn    = os.getenv(bd["arn_env"])
                    device = AwsDevice(arn, aws_session=aws_session)
                    status = device.status
                    is_available = (status == "ONLINE")
                    self._qpu_status_cache[qpu_name] = {
                        "available":    is_available,
                        "pending_jobs": None,
                        "note":         status,
                        "vendor":       bd["vendor"],
                    }
                    if is_available:
                        available.append(qpu_name)
                    print(f"  [QPU] Braket {qpu_name}: status={status} online={is_available}")
                except Exception as e:
                    self._qpu_status_cache[qpu_name] = {
                        "available":    False,
                        "pending_jobs": None,
                        "note":         "offline or unreachable",
                        "vendor":       bd["vendor"],
                    }
                    print(f"  [QPU] Braket {qpu_name}: offline ({e})")
        quandela_token = os.getenv("QUANDELA_TOKEN")
        if quandela_token:
            for platform in QUANDELA_PLATFORMS:
                try:
                    import perceval as pcvl
                    session = pcvl.QuandelaSession(
                        platform_name=platform, token=quandela_token)
                    session.start()
                    p = session.build_remote_processor()
                    specs = p.specs
                    constraints = specs.get('constraints', {})
                    max_modes   = constraints.get('max_mode_count', 0)
                    max_photons = constraints.get('max_photon_count', 0)
                    session.stop()
                    is_available = max_modes > 0
                    self._qpu_status_cache[platform] = {
                        "available":    is_available,
                        "pending_jobs": None,
                        "note":         f"max_modes={max_modes} max_photons={max_photons}" if is_available else "maintenance (max_modes=0)",
                        "vendor":       "quandela",
                        "max_modes":    max_modes,
                        "max_photons":  max_photons,
                    }
                    if is_available:
                        available.append(platform)
                    print(f"  [QPU] Quandela {platform}: online={is_available} max_modes={max_modes} max_photons={max_photons}")
                except Exception as e:
                    self._qpu_status_cache[platform] = {
                        "available":    False,
                        "pending_jobs": None,
                        "note":         "offline or unreachable",
                        "vendor":       "quandela",
                    }
                    print(f"  [QPU] Quandela {platform}: offline ({e})")

        # RAG 이력 저장
        try:
            from uqi_rag import UQIRAG
            rag = UQIRAG()
            rag.add_qpu_availability(
                available=available,
                status=self._qpu_status_cache,
            )
        except Exception:
            pass

        print(f"  [QPU] 가용 장비 {len(available)}개: {available}")
        return available

    def get_qpu_status(self) -> dict:
        """get_available_qpus() 호출 후 캐시된 상태 반환"""
        return getattr(self, '_qpu_status_cache', {})