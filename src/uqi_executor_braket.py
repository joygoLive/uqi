# uqi_executor_braket.py
# UQIQIRConverter → AWS Braket → IonQ / Rigetti 실행
# IBM executor와 동일 인터페이스 (run_all / _run_single / _submit_single /
# fetch_job_status / cancel_job / print_summary)

import os
from typing import Optional
from uqi_qir_converter import UQIQIRConverter


# Braket 실 QPU 매핑: qpu_name → (ARN env var, region)
_BRAKET_QPU_MAP = {
    "ionq_forte1":     ("IONQ_FORTE_ARN",      "us-east-1"),
    "rigetti_cepheus": ("RIGETTI_CEPHEUS_ARN", "us-west-1"),
}

# Braket managed simulator (저렴한 검증용). SV1은 env 변수 우선 + fallback.
_BRAKET_SIM_MAP = {
    "braket_sv1": (os.getenv("BRAKET_SV1_ARN") or "arn:aws:braket:::device/quantum-simulator/amazon/sv1", "us-east-1"),
    "braket_dm1": ("arn:aws:braket:::device/quantum-simulator/amazon/dm1", "us-east-1"),
    "braket_tn1": ("arn:aws:braket:us-west-2::device/quantum-simulator/amazon/tn1", "us-west-2"),
}

# Braket 디바이스 중 executor에서는 직접 submit 못 하지만 가용성/calibration
# 체크는 가능한 것 (e.g. QuEra Aquila — AHS only, gate 회로 비호환이라
# submit 분기에서 제외하지만 사용자 안내 위해 가용 시간은 노출)
_BRAKET_OTHER_MAP = {
    "quera_aquila": ("QuEra_Aquila_ARN", "us-east-1"),
}

DEFAULT_S3_BUCKET = os.getenv("AWS_BRAKET_S3_BUCKET", "amazon-braket-uqi-sean")


class UQIExecutorBraket:

    def __init__(self, converter: UQIQIRConverter, shots: int = 1024):
        self.converter = converter
        self.shots = shots
        self.results = {}
        self._aws_session_cache = {}  # region → AwsSession

    # ─────────────────────────────────────────
    # 내부 유틸
    # ─────────────────────────────────────────

    def _get_aws_session(self, region: str):
        import boto3
        from braket.aws import AwsSession
        if region not in self._aws_session_cache:
            boto_session = boto3.Session(
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
                region_name=region,
            )
            self._aws_session_cache[region] = AwsSession(boto_session=boto_session)
        return self._aws_session_cache[region]

    def _resolve_device(self, qpu_name: str):
        """qpu_name → (arn, region). ARN 직접 입력도 허용."""
        if qpu_name.startswith("arn:"):
            parts = qpu_name.split(":")
            region = parts[3] if len(parts) >= 4 and parts[3] else "us-east-1"
            return qpu_name, region
        if qpu_name in _BRAKET_QPU_MAP:
            env, region = _BRAKET_QPU_MAP[qpu_name]
            arn = os.getenv(env)
            if not arn:
                raise RuntimeError(f"{qpu_name}: {env} 환경변수 없음")
            return arn, region
        if qpu_name in _BRAKET_SIM_MAP:
            return _BRAKET_SIM_MAP[qpu_name]
        raise RuntimeError(f"Unknown Braket QPU: {qpu_name}")

    def _prepare_circuit(self, name: str, qasm: Optional[str]):
        """Qiskit 회로 → Braket 회로 변환. (braket_circuit, via, error_msg) 리턴."""
        from qiskit import QuantumCircuit
        from qiskit_braket_provider import to_braket

        original_circuit = self.converter.extractor.circuits.get(name)
        if original_circuit is not None:
            circuit = original_circuit.copy()
            via = "Qiskit-direct"
        elif qasm is not None:
            filtered = "\n".join(
                line for line in qasm.splitlines()
                if not line.strip().startswith("gphase")
            )
            circuit = QuantumCircuit.from_qasm_str(filtered)
            via = "QASM"
        else:
            return None, None, "회로/QASM 모두 없음"

        # 커스텀 게이트 분해 (IBM executor와 동일 패턴)
        for _ in range(10):
            ops = set(circuit.count_ops().keys())
            standard = {"cx","cy","cz","h","x","y","z","s","t","sdg","tdg",
                        "rx","ry","rz","u","u1","u2","u3","swap","ccx",
                        "measure","reset","barrier","id"}
            if ops <= standard:
                break
            try:
                circuit = circuit.decompose()
            except Exception:
                break

        if not circuit.cregs:
            circuit.measure_all(add_bits=True)

        try:
            braket_circuit = to_braket(circuit)
        except Exception as e:
            return None, via, f"to_braket 변환 실패: {e}"

        return braket_circuit, via, None

    # ─────────────────────────────────────────
    # 전체 실행
    # ─────────────────────────────────────────

    def run_all(self, use_simulator: bool = True,
                backend_name: str = "ionq_forte1",
                token: str = None) -> dict:
        # token은 인터페이스 호환용 placeholder (AWS는 환경변수 사용)
        circuit_names = (
            list(self.converter.extractor.tapes.keys()) or
            list(self.converter.extractor.sessions.keys()) or
            list(self.converter.extractor.circuits.keys())
        )
        if not circuit_names:
            print("  [Braket] 실행할 회로 없음")
            return {}

        for name in circuit_names:
            print(f"  [Braket] 실행: {name}")
            qasm = self.converter.qasm_results.get(name)
            self.results[name] = self._run_single(
                name, qasm, use_simulator, backend_name
            )

        ok = [n for n, r in self.results.items() if r["ok"]]
        print(f"  [Braket] 완료: {len(ok)}/{len(self.results)} 실행 성공")
        return self.results

    # ─────────────────────────────────────────
    # 단일 회로 실행 (동기, blocking)
    # ─────────────────────────────────────────

    def _run_single(self, name: str, qasm: Optional[str],
                    use_simulator: bool, backend_name: str) -> dict:
        result = {
            "ok": False, "counts": None, "probs": None,
            "backend": None, "via": None, "error": None,
        }

        try:
            bc, via, err = self._prepare_circuit(name, qasm)
            result["via"] = via
            if bc is None:
                result["error"] = err
                return result

            if use_simulator:
                from braket.devices import LocalSimulator
                device = LocalSimulator()
                result["backend"] = "LocalSimulator"
                print(f"    ✓ Braket LocalSimulator 사용")
                task = device.run(bc, shots=self.shots)
            else:
                arn, region = self._resolve_device(backend_name)
                from braket.aws import AwsDevice
                aws_session = self._get_aws_session(region)
                device = AwsDevice(arn, aws_session=aws_session)
                result["backend"] = backend_name
                print(f"    ✓ Braket 디바이스 연결: {backend_name} ({region})")

                s3_dest = (DEFAULT_S3_BUCKET, f"tasks/{backend_name}")
                task = device.run(bc, shots=self.shots,
                                  s3_destination_folder=s3_dest)

            counts = dict(task.result().measurement_counts)
            total = sum(counts.values())
            result["counts"] = counts
            result["probs"] = {k: v / total for k, v in counts.items()}
            result["ok"] = True
            print(f"    ✓ 실행 성공 (backend={result['backend']}, via={via})")

        except Exception as e:
            result["error"] = str(e)
            print(f"    ✗ 실행 실패: {e}")

        return result

    # ─────────────────────────────────────────
    # 비동기 제출 (task_arn만 즉시 리턴)
    # ─────────────────────────────────────────

    def _submit_single(self, name: str, qasm: Optional[str],
                       backend_name: str) -> dict:
        """실 Braket QPU에 task 제출, ARN만 즉시 리턴 (블로킹 없음)."""
        result = {
            "ok": False, "job_id": None, "backend": backend_name,
            "via": None, "error": None,
        }
        try:
            bc, via, err = self._prepare_circuit(name, qasm)
            result["via"] = via
            if bc is None:
                result["error"] = err
                return result

            arn, region = self._resolve_device(backend_name)
            from braket.aws import AwsDevice
            aws_session = self._get_aws_session(region)
            device = AwsDevice(arn, aws_session=aws_session)

            s3_dest = (DEFAULT_S3_BUCKET, f"tasks/{backend_name}")
            task = device.run(bc, shots=self.shots,
                              s3_destination_folder=s3_dest)

            result["job_id"] = task.id  # task ARN
            result["ok"] = True
            print(f"    ✓ Braket task 제출: job_id={task.id}")

        except Exception as e:
            result["error"] = str(e)
            print(f"    ✗ Braket task 제출 실패: {e}")
        return result

    # ─────────────────────────────────────────
    # job 상태 조회 (static)
    # ─────────────────────────────────────────

    @staticmethod
    def fetch_job_status(job_id: str, token: str = None) -> dict:
        """task_arn으로 Braket task 상태 및 결과 조회. 완료 시 counts/probs 포함."""
        result = {
            "job_id": job_id, "status": None, "counts": None,
            "probs": None, "error": None, "done": False,
        }
        try:
            import boto3
            from braket.aws import AwsSession, AwsQuantumTask

            # ARN에서 region 추출: arn:aws:braket:<region>:<account>:quantum-task/<uuid>
            parts = job_id.split(":")
            region = parts[3] if len(parts) >= 4 and parts[3] else "us-east-1"

            boto_session = boto3.Session(
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
                region_name=region,
            )
            aws_session = AwsSession(boto_session=boto_session)
            task = AwsQuantumTask(arn=job_id, aws_session=aws_session)
            state = task.state()  # CREATED/QUEUED/RUNNING/COMPLETED/FAILED/CANCELLED
            result["status"] = state

            done_states      = {"COMPLETED"}
            cancelled_states = {"CANCELLED", "CANCELED"}
            failed_states    = {"FAILED"}

            if state in done_states:
                r = task.result()
                counts = dict(r.measurement_counts)
                total = sum(counts.values())
                result["counts"] = counts
                result["probs"] = {k: v / total for k, v in counts.items()}
                result["done"] = True
                print(f"    ✓ Braket task 완료: {job_id}")
            elif state in cancelled_states:
                result["cancelled"] = True
                print(f"    ✕ Braket task 취소됨: {job_id}")
            elif state in failed_states:
                try:
                    metadata = task.metadata()
                    fail_reason = metadata.get("failureReason", state)
                except Exception:
                    fail_reason = state
                result["error"] = f"[Braket] {fail_reason}"
                result["cloud_failed"] = True
                print(f"    ✗ Braket task 실패: {job_id} — {fail_reason}")
            else:
                print(f"    … Braket task 진행중: {job_id} ({state})")

        except Exception as e:
            result["error"] = str(e)
            print(f"    ✗ Braket task 조회 실패: {e}")
        return result

    # ─────────────────────────────────────────
    # job 취소 (static)
    # ─────────────────────────────────────────

    @staticmethod
    def fetch_job_timing(job_id: str, token: str = None) -> dict:
        """
        Braket task의 timing 조회.
        Braket metadata는 createdAt/endedAt만 제공 → wall-clock (큐 + 실행).
        실 실행 시간만 분리하려면 별도 CloudWatch metric 필요 (현재는 wall-clock만).

        Returns:
            {
              "execution_sec":    None,                    # Braket는 분리 불가
              "wall_clock_sec":   float | None,           # createdAt~endedAt
              "source":           "braket_metadata_wall_clock",
              "accuracy":         "queue_included",        # ⚠️ 큐 포함
              "error":            str | None,
            }
        """
        result = {
            "execution_sec":  None,           # Braket 한계 — 분리 불가
            "wall_clock_sec": None,
            "source":         "braket_metadata_wall_clock",
            "accuracy":       "queue_included",
            "error":          None,
        }
        try:
            import boto3
            from braket.aws import AwsSession, AwsQuantumTask

            parts = job_id.split(":")
            region = parts[3] if len(parts) >= 4 and parts[3] else "us-east-1"
            boto_session = boto3.Session(
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
                region_name=region,
            )
            aws_session = AwsSession(boto_session=boto_session)
            task = AwsQuantumTask(arn=job_id, aws_session=aws_session)

            md = task.metadata()
            created = md.get("createdAt")
            ended   = md.get("endedAt")
            if created and ended:
                # datetime 타입 — boto3는 datetime 반환
                if hasattr(created, "timestamp") and hasattr(ended, "timestamp"):
                    result["wall_clock_sec"] = ended.timestamp() - created.timestamp()
        except Exception as e:
            result["error"] = str(e)
        return result

    @staticmethod
    def cancel_job(job_id: str, token: str = None) -> dict:
        """Braket task 취소 요청. QUEUED 상태에서만 성공 가능."""
        try:
            import boto3
            from braket.aws import AwsSession, AwsQuantumTask

            parts = job_id.split(":")
            region = parts[3] if len(parts) >= 4 and parts[3] else "us-east-1"

            boto_session = boto3.Session(
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
                region_name=region,
            )
            aws_session = AwsSession(boto_session=boto_session)
            task = AwsQuantumTask(arn=job_id, aws_session=aws_session)
            task.cancel()
            print(f"    ✓ Braket task 취소 요청: {job_id}")
            return {"ok": True}
        except Exception as e:
            print(f"    ✗ Braket task 취소 실패: {e}")
            return {"ok": False, "error": str(e)}

    # ─────────────────────────────────────────
    # Utility
    # ─────────────────────────────────────────

    def print_summary(self):
        print("\n  [Braket] 실행 결과 요약")
        for name, r in self.results.items():
            status = "✓" if r["ok"] else "✗"
            if r["ok"]:
                via = r.get("via", "")
                detail = f"backend={r['backend']} via={via}"
                if r["probs"]:
                    top3 = sorted(r["probs"].items(), key=lambda x: -x[1])[:3]
                    detail += f" | top-3: {top3}"
            else:
                detail = r["error"]
            print(f"    {status} {name:<20} {detail}")


# ──────────────────────────────────────────────────────────────
# 디바이스 가용성 체크 (모듈 레벨 함수, executor와 별도)
# ──────────────────────────────────────────────────────────────

def _matches_day(exec_day_str: str, weekday: int) -> bool:
    """ExecutionDay enum string과 datetime weekday(Mon=0...Sun=6) 매칭."""
    ed = exec_day_str.split('.')[-1] if '.' in exec_day_str else exec_day_str
    if ed == "EVERYDAY":
        return True
    if ed == "WEEKDAYS":
        return weekday < 5
    if ed == "WEEKEND":
        return weekday >= 5
    day_map = {
        "MONDAY": 0, "TUESDAY": 1, "WEDNESDAY": 2, "THURSDAY": 3,
        "FRIDAY": 4, "SATURDAY": 5, "SUNDAY": 6,
    }
    return day_map.get(ed) == weekday


def check_device_availability(qpu_name: str) -> dict:
    """
    Braket 디바이스의 execution window 기반 현재 가용성 + 다음 가용 시간 조회.

    Args:
        qpu_name: QPU 이름 (e.g. "ionq_forte1", "rigetti_cepheus")

    Returns:
        {
          "qpu":                   str,
          "device_status":         str | None,   # ONLINE/OFFLINE/RETIRED
          "available_now":         bool | None,
          "current_window_end_kst":str | None,
          "next_window_start_kst": str | None,
          "wait_minutes":          int | None,
          "windows":               list[dict],
          "message":               str,         # 사람이 읽을 안내
          "warnings":              list[str],
        }

    Non-Braket QPU는 "체크 미지원" 메시지 반환.
    """
    from datetime import datetime, timedelta, timezone

    result = {
        "qpu": qpu_name,
        "device_status": None,
        "available_now": None,
        "current_window_end_kst": None,
        "next_window_start_kst": None,
        "wait_minutes": None,
        "windows": [],
        "message": "",
        "warnings": [],
    }

    # Braket이 아닌 QPU
    if (qpu_name not in _BRAKET_QPU_MAP
            and qpu_name not in _BRAKET_SIM_MAP
            and qpu_name not in _BRAKET_OTHER_MAP):
        result["message"] = "Non-Braket QPU — 가용 시간 체크 미지원 (벤더별 정책 확인)"
        return result

    # 시뮬레이터: 24/7 가용
    if qpu_name in _BRAKET_SIM_MAP:
        result["device_status"] = "ONLINE"
        result["available_now"] = True
        result["message"] = "시뮬레이터 — 24/7 가용"
        return result

    # Braket 실 QPU (executor 지원 + other 모두 포함)
    try:
        env_var, region = (
            _BRAKET_QPU_MAP[qpu_name] if qpu_name in _BRAKET_QPU_MAP
            else _BRAKET_OTHER_MAP[qpu_name]
        )
        arn = os.getenv(env_var)
        if not arn:
            result["message"] = f"{env_var} 환경변수 미설정"
            result["warnings"].append("ARN 누락")
            return result

        import boto3
        from braket.aws import AwsDevice, AwsSession
        boto_session = boto3.Session(
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=region,
        )
        aws_session = AwsSession(boto_session=boto_session)
        device = AwsDevice(arn, aws_session=aws_session)

        result["device_status"] = device.status

        if device.status == "RETIRED":
            result["available_now"] = False
            result["message"] = "❌ 디바이스 RETIRED — 사용 불가 (코드에서 제거 권장)"
            result["warnings"].append("Retired device")
            return result

        if device.status == "OFFLINE":
            result["available_now"] = False
            result["message"] = "❌ 디바이스 OFFLINE — 일시 점검/장애 가능성"
            result["warnings"].append("Offline status")
            return result

        # 가용 시간 분석
        windows = list(device.properties.service.executionWindows)
        result["windows"] = [
            {
                "day": str(ew.executionDay).split('.')[-1],
                "start_utc": str(ew.windowStartHour),
                "end_utc": str(ew.windowEndHour),
            }
            for ew in windows
        ]

        # 현재 UTC 시각
        now_utc = datetime.now(timezone.utc)
        cur_weekday = now_utc.weekday()
        cur_time = now_utc.time()
        kst = timezone(timedelta(hours=9))

        # 현재 가용 윈도우 찾기
        current_window = None
        for ew in windows:
            if (_matches_day(str(ew.executionDay), cur_weekday)
                    and ew.windowStartHour <= cur_time <= ew.windowEndHour):
                current_window = ew
                break

        if current_window:
            result["available_now"] = True
            end_dt = datetime.combine(
                now_utc.date(), current_window.windowEndHour, tzinfo=timezone.utc
            )
            end_kst = end_dt.astimezone(kst)
            result["current_window_end_kst"] = end_kst.strftime("%H:%M KST")
            result["message"] = (
                f"✅ 현재 가용 (윈도우 종료: {result['current_window_end_kst']})"
            )
        else:
            result["available_now"] = False
            # 향후 7일 검사로 가장 빠른 다음 가용 시각 찾기
            best_dt = None
            for offset_days in range(0, 8):
                check_date = now_utc.date() + timedelta(days=offset_days)
                check_weekday = check_date.weekday()
                for ew in windows:
                    if _matches_day(str(ew.executionDay), check_weekday):
                        start_dt = datetime.combine(
                            check_date, ew.windowStartHour, tzinfo=timezone.utc
                        )
                        if start_dt > now_utc:
                            if best_dt is None or start_dt < best_dt:
                                best_dt = start_dt

            if best_dt:
                next_kst = best_dt.astimezone(kst)
                result["next_window_start_kst"] = next_kst.strftime(
                    "%m-%d (%a) %H:%M KST"
                )
                wait_min = int((best_dt - now_utc).total_seconds() / 60)
                result["wait_minutes"] = wait_min
                if wait_min < 60:
                    wait_str = f"{wait_min}분"
                elif wait_min < 1440:
                    wait_str = f"{wait_min // 60}시간 {wait_min % 60}분"
                else:
                    wait_str = f"{wait_min // 1440}일 {(wait_min % 1440) // 60}시간"
                result["message"] = (
                    f"❌ 현재 비가용. 다음 윈도우: "
                    f"{result['next_window_start_kst']} (대기 {wait_str})"
                )
            else:
                result["message"] = "❌ 비가용 + 다음 윈도우 계산 실패"
                result["warnings"].append("execution window 형식 이상")

    except Exception as e:
        result["warnings"].append(f"가용성 체크 실패: {e}")
        result["message"] = f"가용성 체크 오류: {e}"

    return result
