# uqi_qpu_live_check.py
# QPU 제출 직전 실시간 상태 확인 + modality 기반 대안 추천
#
# - 캐시(5분 TTL)에 의존하지 않고 타겟 QPU 1개에 대해 벤더 API를 직접 호출
# - 네트워크/API 실패 시 3회 재시도 후 unreachable 리턴
# - Pasqal/Quantinuum은 벤더 API가 operational status를 제공하지 않아 미지원
"""
submit 단계에서 호출되는 실시간 QPU 상태 확인 모듈.

호출자는 live_check_qpu() 의 반환값을 보고
- ok=False → 사용자에게 "나중에 재시도" 안내
- available=False → recommend_alternatives() 로 같은 modality 내 대안 제시
"""
import os
import time


LIVE_CHECK_RETRIES   = 3
LIVE_CHECK_BACKOFF_S = 1.5

# 물리적 modality 기반 분류. 대안 추천은 같은 그룹 내에서만 수행.
# (gate-based 는 기본 그룹으로, 명시되지 않은 QPU는 모두 gate 로 간주)
_MODALITY_MEMBERS = {
    "photonic": {"qpu:ascella", "qpu:belenos", "sim:ascella", "sim:belenos"},
    "analog":   {"quera_aquila", "pasqal_fresnel"},
}


def get_modality(qpu_name: str) -> str:
    for mod, members in _MODALITY_MEMBERS.items():
        if qpu_name in members:
            return mod
    return "gate"


def _infer_vendor(qpu_name: str) -> str:
    if qpu_name.startswith(("sim:", "qpu:")):
        return "quandela"
    if qpu_name.startswith("ibm"):
        return "ibm"
    if qpu_name.startswith("iqm"):
        return "iqm"
    if qpu_name.startswith("ionq"):
        return "braket:ionq"
    if qpu_name.startswith("rigetti"):
        return "braket:rigetti"
    if qpu_name.startswith("quera"):
        return "braket:quera"
    if qpu_name.startswith(("pasqal", "quantinuum")):
        return "unsupported"
    return "unknown"


# ─── 벤더별 단일 QPU 상태 조회 ────────────────────────────────

def _check_quandela(qpu_name: str) -> dict:
    import perceval as pcvl
    token = os.getenv("QUANDELA_TOKEN")
    if not token:
        raise RuntimeError("QUANDELA_TOKEN 없음")
    session = pcvl.QuandelaSession(platform_name=qpu_name, token=token)
    session.start()
    try:
        p = session.build_remote_processor()
        status_str = str(getattr(p, "status", "")).lower()
    finally:
        session.stop()
    return {
        "available": status_str == "available",
        "status":    status_str or "unknown",
    }


def _check_ibm(qpu_name: str) -> dict:
    from qiskit_ibm_runtime import QiskitRuntimeService
    token = os.getenv("IBM_QUANTUM_TOKEN")
    if not token:
        raise RuntimeError("IBM_QUANTUM_TOKEN 없음")
    service = QiskitRuntimeService(channel="ibm_quantum_platform", token=token)
    backend = service.backend(qpu_name)
    status = backend.status()
    operational = bool(getattr(status, "operational", False))
    pending = getattr(status, "pending_jobs", None)
    note = "operational" if operational else "offline"
    if operational and pending is not None and pending > 20:
        note = f"operational (queue {pending})"
    return {
        "available":    operational,
        "status":       note,
        "pending_jobs": pending,
    }


def _check_iqm(qpu_name: str) -> dict:
    from iqm.iqm_client import IQMClient
    token = os.getenv("IQM_QUANTUM_TOKEN")
    if not token:
        raise RuntimeError("IQM_QUANTUM_TOKEN 없음")
    # iqm_garnet → garnet
    device = qpu_name.split("_", 1)[1] if "_" in qpu_name else qpu_name
    client = IQMClient("https://resonance.meetiqm.com",
                       quantum_computer=device, token=token)
    health = client.get_health()
    healthy = bool(health.get("healthy", False))
    return {
        "available": healthy,
        "status":    "healthy" if healthy else "unhealthy",
    }


_BRAKET_MAP = {
    "ionq_forte1":     ("IONQ_FORTE_ARN",      "us-east-1"),
    "rigetti_cepheus": ("RIGETTI_CEPHEUS_ARN", "us-west-1"),
    "quera_aquila":    ("QuEra_Aquila_ARN",    "us-east-1"),
}


def _check_braket(qpu_name: str) -> dict:
    from braket.aws import AwsDevice, AwsSession
    import boto3
    aws_key    = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret = os.getenv("AWS_SECRET_ACCESS_KEY")
    if not (aws_key and aws_secret):
        raise RuntimeError("AWS 자격증명 없음")
    mapping = _BRAKET_MAP.get(qpu_name)
    if not mapping:
        raise RuntimeError(f"{qpu_name}: Braket ARN 매핑 없음")
    arn_env, region = mapping
    arn = os.getenv(arn_env)
    if not arn:
        raise RuntimeError(f"{arn_env} 환경변수 없음")
    boto_session = boto3.Session(
        aws_access_key_id=aws_key, aws_secret_access_key=aws_secret,
        region_name=region,
    )
    aws_session = AwsSession(boto_session=boto_session)
    device = AwsDevice(arn, aws_session=aws_session)
    status = device.status  # "ONLINE" / "OFFLINE" / "RETIRED"
    return {
        "available": status == "ONLINE",
        "status":    status,
    }


def _dispatch(qpu_name: str) -> dict:
    vendor = _infer_vendor(qpu_name)
    if vendor == "quandela":
        return _check_quandela(qpu_name)
    if vendor == "ibm":
        return _check_ibm(qpu_name)
    if vendor == "iqm":
        return _check_iqm(qpu_name)
    if vendor.startswith("braket:"):
        return _check_braket(qpu_name)
    if vendor == "unsupported":
        # Pasqal/Quantinuum: 벤더 API에 operational status 없음
        return {
            "available": True,   # 보수적 fallback — 제출은 허용, 실패 시 벤더 측 에러
            "status":    "live_check_unsupported",
        }
    raise RuntimeError(f"{qpu_name}: 알 수 없는 벤더")


# ─── 공개 API ──────────────────────────────────────────────

def live_check_qpu(qpu_name: str,
                   retries: int = LIVE_CHECK_RETRIES,
                   backoff_s: float = LIVE_CHECK_BACKOFF_S) -> dict:
    """
    타겟 QPU 1개의 실시간 상태를 retries 회 재시도로 조회.

    Returns
    -------
    {
      "ok":        bool,       # 벤더 API 호출 자체 성공 여부
      "available": bool,       # 사용 가능 여부
      "status":    str,        # "available"/"maintenance"/"unhealthy"/"unreachable"/...
      "attempts":  int,
      "error":     str | None,
    }
    """
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            res = _dispatch(qpu_name)
            return {
                "ok":        True,
                "available": bool(res.get("available", False)),
                "status":    res.get("status", "unknown"),
                "attempts":  attempt,
                "error":     None,
            }
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(backoff_s)
    return {
        "ok":        False,
        "available": False,
        "status":    "unreachable",
        "attempts":  retries,
        "error":     f"{type(last_err).__name__}: {last_err}" if last_err else "unknown",
    }


def recommend_alternatives(offline_qpu: str,
                           qpu_comparison: dict,
                           limit: int = 3) -> list:
    """
    같은 modality 그룹 내에서 online 캐시된 QPU를 fidelity 순으로 반환.

    호출자는 최종 제출 시 반환된 QPU에 대해서도 live_check_qpu() 를 다시 돌려야 함
    (캐시 5분 TTL 때문에 대안도 이미 offline 일 수 있음).
    """
    mod = get_modality(offline_qpu)
    candidates = []
    for q, info in (qpu_comparison or {}).items():
        if q == offline_qpu:
            continue
        if get_modality(q) != mod:
            continue
        if not info.get("online", False):
            continue
        fid = info.get("avg_fidelity")
        score = info.get("composite_score")
        candidates.append({
            "qpu":             q,
            "avg_fidelity":    round(float(fid), 4) if fid is not None else None,
            "composite_score": round(float(score), 4) if score is not None else None,
            "queue_note":      info.get("queue_note", ""),
        })
    # fidelity 우선, 동률이면 composite_score 로
    candidates.sort(
        key=lambda c: (
            c["avg_fidelity"]    if c["avg_fidelity"]    is not None else -1,
            c["composite_score"] if c["composite_score"] is not None else -1,
        ),
        reverse=True,
    )
    return candidates[:limit]
