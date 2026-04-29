# uqi_executor_azure.py
# UQIQIRConverter → Azure Quantum → Pasqal / Quantinuum 실행
# IBM/Braket executor와 동일 인터페이스
#
# ⚠️ 사용자 사전 작업 완료 후 활성화:
#   1. .env에 AZURE_* 환경변수 채우기 (TENANT/CLIENT/SECRET/SUB/RG/WS/LOC)
#   2. requirements.txt에 azure-quantum[qiskit], azure-identity 설치
#   3. mcp_server.py에 Azure 분기 추가
#   4. uqi_pricing.py에 pasqal/quantinuum (Azure) 가격 entry 추가

import os
from typing import Optional
from uqi_qir_converter import UQIQIRConverter


# UQI qpu_name → Azure Quantum target 이름 매핑
# 정책: 실 QPU만 활용. Azure 시뮬레이터(emu-*) 제외, Quantinuum도 Azure 경유 제외
# (Quantinuum은 추후 자사 클라우드로 별도 통합 예정).
_AZURE_TARGET_MAP = {
    "pasqal_fresnel":      "pasqal.qpu.fresnel",       # 실 QPU (West US)
    "pasqal_fresnel_can1": "pasqal.qpu.fresnel-can1",  # 실 QPU (Canada)
}


class UQIExecutorAzure:
    """
    Azure Quantum executor — Braket executor와 동일 인터페이스.

    인증 우선순위 (azure-identity 자동 처리):
      1. 환경변수 (AZURE_CLIENT_ID/SECRET/TENANT_ID) → Service Principal
      2. az login 자격증명 (개발 시)
      3. Managed Identity (Azure 환경 내 실행 시)
    """

    def __init__(self, converter: UQIQIRConverter, shots: int = 1024):
        self.converter = converter
        self.shots = shots
        self.results = {}
        self._workspace = None  # 캐시 (재사용)

    # ─────────────────────────────────────────
    # 내부 유틸
    # ─────────────────────────────────────────

    def _get_workspace(self):
        """Azure Quantum workspace 객체 획득 (lazy + 캐시)."""
        if self._workspace is not None:
            return self._workspace
        from azure.quantum import Workspace
        from azure.identity import ClientSecretCredential, DefaultAzureCredential

        # Service Principal이 .env에 있으면 우선 사용
        tenant = os.getenv("AZURE_TENANT_ID")
        client = os.getenv("AZURE_CLIENT_ID")
        secret = os.getenv("AZURE_CLIENT_SECRET")

        if tenant and client and secret:
            credential = ClientSecretCredential(
                tenant_id=tenant, client_id=client, client_secret=secret
            )
        else:
            # Fallback: az login / Managed Identity
            credential = DefaultAzureCredential()

        self._workspace = Workspace(
            subscription_id=os.getenv("AZURE_QUANTUM_SUBSCRIPTION_ID"),
            resource_group=os.getenv("AZURE_QUANTUM_RESOURCE_GROUP"),
            name=os.getenv("AZURE_QUANTUM_WORKSPACE"),
            location=os.getenv("AZURE_QUANTUM_LOCATION", "westus"),
            credential=credential,
        )
        return self._workspace

    def _resolve_target(self, qpu_name: str) -> str:
        """qpu_name → Azure target 이름. ARN 직접 입력도 허용."""
        if qpu_name in _AZURE_TARGET_MAP:
            return _AZURE_TARGET_MAP[qpu_name]
        if "." in qpu_name:
            # 이미 Azure target 형식 (e.g. "pasqal.qpu.fresnel")
            return qpu_name
        raise RuntimeError(f"Unknown Azure target: {qpu_name}")

    def _prepare_circuit(self, name: str, qasm: Optional[str]):
        """Qiskit 회로 준비. Azure는 Qiskit 회로를 직접 받음 (변환 불필요)."""
        from qiskit import QuantumCircuit

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

        # 커스텀 게이트 분해
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

        return circuit, via, None

    # ─────────────────────────────────────────
    # 전체 실행
    # ─────────────────────────────────────────

    def run_all(self, use_simulator: bool = True,
                backend_name: str = "quantinuum_h2_1sc",
                token: str = None) -> dict:
        # token은 인터페이스 호환용 placeholder
        circuit_names = (
            list(self.converter.extractor.tapes.keys()) or
            list(self.converter.extractor.sessions.keys()) or
            list(self.converter.extractor.circuits.keys())
        )
        if not circuit_names:
            print("  [Azure] 실행할 회로 없음")
            return {}

        for name in circuit_names:
            print(f"  [Azure] 실행: {name}")
            qasm = self.converter.qasm_results.get(name)
            self.results[name] = self._run_single(
                name, qasm, use_simulator, backend_name
            )

        ok = [n for n, r in self.results.items() if r["ok"]]
        print(f"  [Azure] 완료: {len(ok)}/{len(self.results)} 실행 성공")
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
            circuit, via, err = self._prepare_circuit(name, qasm)
            result["via"] = via
            if circuit is None:
                result["error"] = err
                return result

            # use_simulator=True면 Quantinuum syntax checker (저렴)로 실행
            target_name = (
                "quantinuum.sim.h2-1sc" if use_simulator
                else self._resolve_target(backend_name)
            )

            from azure.quantum.qiskit import AzureQuantumProvider
            workspace = self._get_workspace()
            provider = AzureQuantumProvider(workspace=workspace)
            backend = provider.get_backend(target_name)
            result["backend"] = target_name
            print(f"    ✓ Azure target 연결: {target_name}")

            job = backend.run(circuit, shots=self.shots)
            res = job.result()
            counts = res.get_counts()
            total = sum(counts.values())
            result["counts"] = dict(counts)
            result["probs"] = {k: v / total for k, v in counts.items()}
            result["ok"] = True
            print(f"    ✓ 실행 성공 (target={target_name}, via={via})")

        except Exception as e:
            result["error"] = str(e)
            print(f"    ✗ 실행 실패: {e}")

        return result

    # ─────────────────────────────────────────
    # 비동기 제출 (job_id만 즉시 리턴)
    # ─────────────────────────────────────────

    def _submit_single(self, name: str, qasm: Optional[str],
                       backend_name: str) -> dict:
        """Azure Quantum job 제출, job_id만 즉시 리턴 (블로킹 없음)."""
        result = {
            "ok": False, "job_id": None, "backend": backend_name,
            "via": None, "error": None,
        }
        try:
            circuit, via, err = self._prepare_circuit(name, qasm)
            result["via"] = via
            if circuit is None:
                result["error"] = err
                return result

            from azure.quantum.qiskit import AzureQuantumProvider
            workspace = self._get_workspace()
            provider = AzureQuantumProvider(workspace=workspace)
            target_name = self._resolve_target(backend_name)
            backend = provider.get_backend(target_name)

            job = backend.run(circuit, shots=self.shots)
            result["job_id"] = job.id()
            result["backend"] = target_name
            result["ok"] = True
            print(f"    ✓ Azure job 제출: job_id={job.id()}")

        except Exception as e:
            result["error"] = str(e)
            print(f"    ✗ Azure job 제출 실패: {e}")
        return result

    # ─────────────────────────────────────────
    # job 상태 조회 (static)
    # ─────────────────────────────────────────

    @staticmethod
    def fetch_job_status(job_id: str, token: str = None) -> dict:
        """Azure Quantum job 상태 조회. 완료 시 counts/probs 포함."""
        result = {
            "job_id": job_id, "status": None, "counts": None,
            "probs": None, "error": None, "done": False,
        }
        try:
            from azure.quantum import Workspace
            from azure.identity import ClientSecretCredential, DefaultAzureCredential

            tenant = os.getenv("AZURE_TENANT_ID")
            client = os.getenv("AZURE_CLIENT_ID")
            secret = os.getenv("AZURE_CLIENT_SECRET")
            if tenant and client and secret:
                credential = ClientSecretCredential(
                    tenant_id=tenant, client_id=client, client_secret=secret
                )
            else:
                credential = DefaultAzureCredential()

            workspace = Workspace(
                subscription_id=os.getenv("AZURE_QUANTUM_SUBSCRIPTION_ID"),
                resource_group=os.getenv("AZURE_QUANTUM_RESOURCE_GROUP"),
                name=os.getenv("AZURE_QUANTUM_WORKSPACE"),
                location=os.getenv("AZURE_QUANTUM_LOCATION", "westus"),
                credential=credential,
            )
            job = workspace.get_job(job_id)
            status = job.details.status  # Waiting/Executing/Succeeded/Failed/Cancelled
            result["status"] = status

            if status == "Succeeded":
                results_data = job.get_results()
                # results_data 형식은 target별로 다름 — Qiskit 백엔드 통한 경우 dict
                if isinstance(results_data, dict) and "histogram" in results_data:
                    counts = {k: int(v * 100) for k, v in results_data["histogram"].items()}
                else:
                    counts = dict(results_data) if hasattr(results_data, "items") else {}
                total = sum(counts.values()) or 1
                result["counts"] = counts
                result["probs"] = {k: v / total for k, v in counts.items()}
                result["done"] = True
                print(f"    ✓ Azure job 완료: {job_id}")
            elif status in ("Cancelled", "Canceling"):
                result["cancelled"] = True
                print(f"    ✕ Azure job 취소됨: {job_id}")
            elif status == "Failed":
                err_msg = getattr(job.details, "error_data", None) or status
                result["error"] = f"[Azure] {err_msg}"
                result["cloud_failed"] = True
                print(f"    ✗ Azure job 실패: {job_id} — {err_msg}")
            else:
                print(f"    … Azure job 진행중: {job_id} ({status})")

        except Exception as e:
            result["error"] = str(e)
            print(f"    ✗ Azure job 조회 실패: {e}")
        return result

    # ─────────────────────────────────────────
    # job 취소 (static)
    # ─────────────────────────────────────────

    @staticmethod
    def fetch_job_timing(job_id: str, token: str = None) -> dict:
        """
        Azure Quantum job의 정확한 실행 시간 조회.
        beginExecutionTime ~ endExecutionTime = 실 실행 시간 (큐 제외).
        creationTime ~ endExecutionTime = wall-clock (큐 + 실행).

        Returns:
            {
              "execution_sec":    float | None,    # 실 실행 시간 (정확)
              "wall_clock_sec":   float | None,    # 전체 (큐 + 실행)
              "source":           "azure_begin_end_execution",
              "accuracy":         "execution_only",
              "error":            str | None,
            }
        """
        result = {
            "execution_sec":  None,
            "wall_clock_sec": None,
            "source":         "azure_begin_end_execution",
            "accuracy":       "execution_only",
            "error":          None,
        }
        try:
            from azure.quantum import Workspace
            from azure.identity import ClientSecretCredential, DefaultAzureCredential

            tenant = os.getenv("AZURE_TENANT_ID")
            client = os.getenv("AZURE_CLIENT_ID")
            secret = os.getenv("AZURE_CLIENT_SECRET")
            if tenant and client and secret:
                credential = ClientSecretCredential(
                    tenant_id=tenant, client_id=client, client_secret=secret
                )
            else:
                credential = DefaultAzureCredential()

            workspace = Workspace(
                subscription_id=os.getenv("AZURE_QUANTUM_SUBSCRIPTION_ID"),
                resource_group=os.getenv("AZURE_QUANTUM_RESOURCE_GROUP"),
                name=os.getenv("AZURE_QUANTUM_WORKSPACE"),
                location=os.getenv("AZURE_QUANTUM_LOCATION", "westus"),
                credential=credential,
            )
            job = workspace.get_job(job_id)
            details = job.details

            # 다양한 attribute 이름 시도 (SDK 버전 차이 대응)
            def _get_dt(*keys):
                for k in keys:
                    v = getattr(details, k, None)
                    if v is not None:
                        return v
                return None

            begin = _get_dt("begin_execution_time", "beginExecutionTime")
            end_t = _get_dt("end_execution_time",   "endExecutionTime")
            create = _get_dt("creation_time",       "creationTime")

            if begin and end_t:
                result["execution_sec"] = (end_t - begin).total_seconds()
            if create and end_t:
                result["wall_clock_sec"] = (end_t - create).total_seconds()
            elif create and begin:
                # 완료 전이면 begin까지의 큐 시간만이라도
                pass

        except Exception as e:
            result["error"] = str(e)
        return result

    @staticmethod
    def cancel_job(job_id: str, token: str = None) -> dict:
        """Azure Quantum job 취소."""
        try:
            from azure.quantum import Workspace
            from azure.identity import ClientSecretCredential, DefaultAzureCredential

            tenant = os.getenv("AZURE_TENANT_ID")
            client = os.getenv("AZURE_CLIENT_ID")
            secret = os.getenv("AZURE_CLIENT_SECRET")
            if tenant and client and secret:
                credential = ClientSecretCredential(
                    tenant_id=tenant, client_id=client, client_secret=secret
                )
            else:
                credential = DefaultAzureCredential()

            workspace = Workspace(
                subscription_id=os.getenv("AZURE_QUANTUM_SUBSCRIPTION_ID"),
                resource_group=os.getenv("AZURE_QUANTUM_RESOURCE_GROUP"),
                name=os.getenv("AZURE_QUANTUM_WORKSPACE"),
                location=os.getenv("AZURE_QUANTUM_LOCATION", "westus"),
                credential=credential,
            )
            workspace.cancel_job(job_id)
            print(f"    ✓ Azure job 취소 요청: {job_id}")
            return {"ok": True}
        except Exception as e:
            print(f"    ✗ Azure job 취소 실패: {e}")
            return {"ok": False, "error": str(e)}

    # ─────────────────────────────────────────
    # Utility
    # ─────────────────────────────────────────

    def print_summary(self):
        print("\n  [Azure] 실행 결과 요약")
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
# Public utility
# ──────────────────────────────────────────────────────────────

def list_azure_targets() -> list[str]:
    """현재 workspace에 사용 가능한 모든 target 목록 (디버깅용)."""
    try:
        ex = UQIExecutorAzure(converter=None, shots=1)  # converter 안 씀
        ws = ex._get_workspace()
        return [t.name for t in ws.get_targets()]
    except Exception as e:
        return [f"<error: {e}>"]


def check_device_availability_azure(qpu_name: str) -> dict:
    """
    Azure Quantum target의 가용성/큐 상태 조회.

    Args:
        qpu_name: UQI qpu_name (e.g. "pasqal_fresnel")

    Returns:
        {
          "qpu":                    str,
          "device_status":          str | None,    # Available/Degraded/Unavailable
          "available_now":          bool | None,
          "average_queue_time_sec": int | None,
          "input_format":           str | None,    # e.g. "pasqal.pulser.v1"
          "message":                str,
          "warnings":               list[str],
        }
    """
    result = {
        "qpu": qpu_name,
        "device_status": None,
        "available_now": None,
        "average_queue_time_sec": None,
        "input_format": None,
        "message": "",
        "warnings": [],
    }

    if qpu_name not in _AZURE_TARGET_MAP:
        result["message"] = "Azure target 매핑 없음"
        return result

    try:
        target_name = _AZURE_TARGET_MAP[qpu_name]
        from azure.quantum import Workspace
        from azure.identity import ClientSecretCredential, DefaultAzureCredential

        tenant = os.getenv("AZURE_TENANT_ID")
        client = os.getenv("AZURE_CLIENT_ID")
        secret = os.getenv("AZURE_CLIENT_SECRET")
        if tenant and client and secret:
            credential = ClientSecretCredential(
                tenant_id=tenant, client_id=client, client_secret=secret
            )
        else:
            credential = DefaultAzureCredential()

        ws = Workspace(
            subscription_id=os.getenv("AZURE_QUANTUM_SUBSCRIPTION_ID"),
            resource_group=os.getenv("AZURE_QUANTUM_RESOURCE_GROUP"),
            name=os.getenv("AZURE_QUANTUM_WORKSPACE"),
            location=os.getenv("AZURE_QUANTUM_LOCATION", "westus"),
            credential=credential,
        )
        target = ws.get_targets(target_name)

        avail = target.current_availability
        avail_str = str(avail).split('.')[-1]      # enum → "AVAILABLE"
        result["device_status"] = avail_str
        result["average_queue_time_sec"] = int(target.average_queue_time or 0)
        result["input_format"] = target.input_data_format

        if avail_str.upper() == "AVAILABLE":
            result["available_now"] = True
            qt = result["average_queue_time_sec"]
            if qt > 0:
                result["message"] = f"✅ 가용 (평균 큐 대기: {qt}초)"
            else:
                result["message"] = "✅ 가용 (큐 비어있음)"
        elif avail_str.upper() == "DEGRADED":
            result["available_now"] = False        # 보수적: degraded는 차단
            result["message"] = "⚠️ Degraded — 일부 기능 제한, 점검 가능성"
            result["warnings"].append("Degraded — 사용 가능 여부 불확실")
        else:
            result["available_now"] = False
            result["message"] = f"❌ {avail_str}"

        # Input format 경고 (Pulser 같은 비Qiskit 형식)
        fmt = result["input_format"] or ""
        if "pulser" in fmt.lower():
            result["warnings"].append(
                f"입력 형식: {fmt} — Pulser pulse program 필요 "
                f"(Qiskit gate 회로 직접 호환 X, 변환 별도 검증 필요)"
            )

    except Exception as e:
        result["warnings"].append(f"가용성 체크 실패: {e}")
        result["message"] = f"오류: {e}"

    return result
