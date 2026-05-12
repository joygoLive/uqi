# uqi_executor_pasqal.py
# Pasqal Cloud Services (PCS) 직제출 executor.
# Azure Quantum 경유 (uqi_executor_azure.UQIExecutorAzure) 와 동일 인터페이스.
#
# 사용 시점:
#   - pasqal_emu_fresnel (PCS emulator, 큐 X) → 무조건 PCS
#   - pasqal_fresnel / pasqal_fresnel_can1 (실 QPU) → mcp_server 라우팅에서
#       UQI_PASQAL_BACKEND=auto|pcs|azure 에 따라 PCS / Azure / fallback 선택

import os
import json as _json
from typing import Optional
from uqi_qir_converter import UQIQIRConverter


# UQI qpu_name → PCS device key 매핑.
# emulator 인 경우 emulator flag 사용 (device_key 는 base FRESNEL).
_PCS_DEVICE_MAP = {
    "pasqal_fresnel":      ("FRESNEL",      None),          # 실 QPU (West US)
    "pasqal_fresnel_can1": ("FRESNEL_CAN1", None),          # 실 QPU (Canada)
    "pasqal_emu_fresnel":  ("FRESNEL",      "EMU_FRESNEL"), # emulator (실 사양 + noise)
    "pasqal_emu_free":     ("FRESNEL",      "EMU_FREE"),    # emulator (무료, 작은 회로)
}


class UQIExecutorPasqal:
    """
    PCS (Pasqal Cloud Services) 직제출 executor — Azure executor 와 동일 인터페이스.

    인증:
      .env 의 PASQAL_USERNAME / PASQAL_PASSWORD / PASQAL_PROJECT_ID 모두 필수.
      셋 중 하나라도 비어있으면 _get_cloud() / _get_sdk() 에서 RuntimeError.
    """

    def __init__(self, converter: UQIQIRConverter, shots: int = 1024):
        self.converter = converter
        self.shots = shots
        self.results = {}
        self._sdk = None       # pasqal_cloud.SDK (제출용)
        self._cloud = None     # pulser_pasqal.PasqalCloud (device fetch + layout swap 용)

    # ─────────────────────────────────────────
    # 내부 유틸 — 자격증명 / SDK
    # ─────────────────────────────────────────

    @staticmethod
    def _required_env() -> tuple[str, str, str]:
        u = os.getenv("PASQAL_USERNAME")
        p = os.getenv("PASQAL_PASSWORD")
        pid = os.getenv("PASQAL_PROJECT_ID")
        missing = [k for k, v in (("PASQAL_USERNAME", u), ("PASQAL_PASSWORD", p),
                                  ("PASQAL_PROJECT_ID", pid)) if not v]
        if missing:
            raise RuntimeError(
                f"PCS 자격증명 누락: {missing}. .env 의 PASQAL_USERNAME / "
                f"PASQAL_PASSWORD / PASQAL_PROJECT_ID 모두 설정 필요."
            )
        return u, p, pid

    def _get_sdk(self):
        if self._sdk is not None:
            return self._sdk
        u, p, pid = self._required_env()
        from pasqal_cloud import SDK
        self._sdk = SDK(username=u, password=p, project_id=pid)
        return self._sdk

    def _get_cloud(self):
        if self._cloud is not None:
            return self._cloud
        u, p, pid = self._required_env()
        from pulser_pasqal import PasqalCloud
        self._cloud = PasqalCloud(username=u, password=p, project_id=pid)
        return self._cloud

    @staticmethod
    def _resolve_device(qpu_name: str) -> tuple[str, Optional[str]]:
        """qpu_name → (device_key, emulator_type_value).

        emulator_type_value 가 None 이면 실 QPU 제출.
        """
        if qpu_name in _PCS_DEVICE_MAP:
            return _PCS_DEVICE_MAP[qpu_name]
        raise RuntimeError(f"Unknown PCS qpu_name: {qpu_name}")

    # ─────────────────────────────────────────
    # AHS / Pulser 제출
    # ─────────────────────────────────────────

    def _submit_single_ahs(self, name: str, algorithm_file: str,
                           backend_name: str = "pasqal_emu_fresnel") -> dict:
        """algorithm_file → pulser Sequence 추출 → PCS create_batch 제출.

        실 QPU 인 경우 사용자 atom 좌표를 calibrated trap 으로 swap (필수).
        Emulator 인 경우 좌표 그대로 사용 (calibrated layout 강제 X).
        """
        from uqi_executor_azure import UQIExecutorAzure  # 정적 헬퍼 재사용

        result = {
            "ok": False, "job_id": None, "batch_id": None,
            "backend": backend_name, "via": "pcs-pulser", "error": None,
        }
        try:
            device_key, emulator_value = self._resolve_device(backend_name)
            seq = UQIExecutorAzure._extract_pulser_sequence(algorithm_file)

            # 실 QPU 제출 시: calibrated layout 매핑 강제.
            # Emulator 제출 시: prototype Sequence 도 허용 — 매핑 건너뜀.
            if emulator_value is None:
                try:
                    cloud = self._get_cloud()
                    devs = cloud.fetch_available_devices()
                    real_device = devs.get(device_key)
                    if (real_device is not None
                            and getattr(real_device, "pre_calibrated_layouts", None)):
                        seq = UQIExecutorAzure._adapt_sequence_to_device(seq, real_device)
                        print(f"    ✓ Sequence → {device_key} (calibrated layout 매핑)")
                except Exception as _de:
                    print(f"    ⚠ PCS device swap 실패 — 원본 sequence 그대로 시도: {_de}")

            sdk = self._get_sdk()
            from pasqal_cloud.device import EmulatorType

            kwargs = {
                "serialized_sequence": seq.to_abstract_repr(),
                "jobs": [{"runs": int(self.shots), "variables": {}}],
            }
            if emulator_value is not None:
                kwargs["emulator"] = EmulatorType(emulator_value)

            batch = sdk.create_batch(**kwargs)
            # PCS 의 batch.id 가 우리 job_id. add_execution 시 counts 추출은
            # fetch_job_status 에서 batch.jobs[0].result 로 처리.
            result["batch_id"] = batch.id
            result["job_id"] = batch.id
            result["backend"] = (f"pcs:{emulator_value}"
                                 if emulator_value else f"pcs:{device_key}")
            result["ok"] = True
            print(f"    ✓ PCS batch 제출: batch_id={batch.id} "
                  f"({'emulator='+emulator_value if emulator_value else device_key})")
        except Exception as e:
            result["error"] = str(e)
            print(f"    ✗ PCS batch 제출 실패: {e}")
        return result

    # ─────────────────────────────────────────
    # job 상태 / timing / 취소 (static)
    # ─────────────────────────────────────────

    @staticmethod
    def _new_sdk_or_raise():
        u, p, pid = UQIExecutorPasqal._required_env()
        from pasqal_cloud import SDK
        return SDK(username=u, password=p, project_id=pid)

    @staticmethod
    def _job_counts(job) -> Optional[dict]:
        """PCS Job 의 result 를 UQI counts dict (bitstring → int) 로 정규화.

        SDK 버전에 따라 result 가 dict 또는 list 형태로 올 수 있어 양쪽 모두 처리.
        """
        try:
            r = getattr(job, "result", None)
            if r is None:
                return None
            if isinstance(r, dict):
                # 이미 bitstring → count dict
                return {str(k): int(v) for k, v in r.items()}
            if isinstance(r, list):
                # list[dict] 형태 — 첫 entry 의 counts 사용
                if r and isinstance(r[0], dict):
                    inner = r[0].get("counts") or r[0]
                    return {str(k): int(v) for k, v in inner.items()}
            return None
        except Exception:
            return None

    @staticmethod
    def fetch_job_status(job_id: str) -> dict:
        """PCS batch 상태 조회. job_id 는 batch_id.

        반환: { job_id, status, counts, error, done, cancelled }
        """
        out = {
            "job_id": job_id, "status": None, "counts": None, "probs": None,
            "error": None, "done": False, "cancelled": False,
        }
        try:
            sdk = UQIExecutorPasqal._new_sdk_or_raise()
            batch = sdk.get_batch(job_id)
        except Exception as e:
            out["error"] = f"PCS get_batch 실패: {e}"
            return out

        batch_status = (getattr(batch, "status", "") or "").upper()
        out["status"] = batch_status

        # PCS 상태값: PENDING / RUNNING / DONE / ERROR / CANCELED / PAUSED
        if batch_status == "DONE":
            jobs = list(getattr(batch, "ordered_jobs", None) or batch.jobs or [])
            counts = UQIExecutorPasqal._job_counts(jobs[0]) if jobs else None
            out["counts"] = counts
            out["done"] = True
        elif batch_status in ("CANCELED", "CANCELLED"):
            out["cancelled"] = True
        elif batch_status == "ERROR":
            # 실패 사유는 job.errors 에 들어있을 수 있음
            jobs = list(getattr(batch, "ordered_jobs", None) or batch.jobs or [])
            errs = []
            for j in jobs:
                e = getattr(j, "errors", None)
                if e:
                    errs.extend(e if isinstance(e, list) else [str(e)])
            out["error"] = "; ".join(errs) if errs else "PCS batch ERROR"
            out["cloud_failed"] = True
        # PENDING/RUNNING/PAUSED → running 으로 간주 (out["done"]=False 유지)
        return out

    @staticmethod
    def fetch_job_timing(job_id: str) -> dict:
        """제출/시작/종료 timestamp 조회. 단위는 ISO8601 문자열 그대로 반환."""
        out = {"job_id": job_id, "submitted_at": None,
               "started_at": None, "ended_at": None, "error": None}
        try:
            sdk = UQIExecutorPasqal._new_sdk_or_raise()
            batch = sdk.get_batch(job_id)
            out["submitted_at"] = getattr(batch, "created_at", None)
            out["started_at"]   = getattr(batch, "start_datetime", None)
            out["ended_at"]     = getattr(batch, "end_datetime", None)
        except Exception as e:
            out["error"] = str(e)
        return out

    @staticmethod
    def cancel_job(job_id: str) -> dict:
        """PCS batch 취소."""
        try:
            sdk = UQIExecutorPasqal._new_sdk_or_raise()
            sdk.cancel_batch(job_id)
            return {"ok": True, "job_id": job_id}
        except Exception as e:
            return {"ok": False, "error": str(e), "job_id": job_id}
