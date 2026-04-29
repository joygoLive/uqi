"""
uqi_pricing.py — QPU 비용 추정 가격 모델

⚠️ 정적 데이터 — 가격 변동 시 수동 업데이트 필요

업데이트 절차:
  1. 해당 vendor 공식 페이지 또는 계약서에서 신규 단가 확인
  2. 아래 PRICING dict의 해당 entry 수정
  3. updated_at 갱신 (오늘 날짜, ISO 8601)
  4. source 필드에 출처/근거 기록
  5. git commit 메시지에 변동 요약

가격 출처:
  - AWS Braket: https://aws.amazon.com/braket/pricing/  (또는 boto3.pricing API)
  - IBM Open: 무료, 한도만 변경 가능 — https://www.ibm.com/quantum/products
  - IQM: 사용자별 계약 (현재: 한국지사장 지원 3000 credits)
  - Quandela: https://cloud.quandela.com/ (Hub Platforms 페이지)
  - Pasqal: 공개 무료 API
  - Quantinuum: HQC(Hardware Quantum Credits), 별도 계약

마지막 전체 점검: 2026-04-27
"""

import fnmatch
from datetime import datetime, timezone
from typing import Optional


# 환율 (대략, 분기별 갱신)
USD_TO_KRW = 1380

# 마지막 전체 점검일 (모든 entry 기준 일자)
LAST_FULL_REVIEW = "2026-04-27"


# ──────────────────────────────────────────────────────────────
# QPU 가격 모델 정적 dict
#
# Entry 필수 키:
#   vendor:     str  — "braket"/"ibm"/"iqm"/"quandela"/"pasqal"/"quantinuum"
#   model:      str  — "task+shot"/"per_minute"/"free_quota"/
#                      "credit_per_second"/"credit_per_shot"/"free"/"hqc"
#   confidence: str  — "exact"/"estimate"/"verify_required"/"unknown"
#   source:     str  — 출처 URL 또는 근거
#   updated_at: str  — ISO 8601 날짜
#
# 모델별 추가 키:
#   task+shot:         task_usd, shot_usd, [min_shots], [max_shots]
#   per_minute:        per_min_usd
#   free_quota:        monthly_free_min
#   credit_per_second: credit_per_sec, [balance_initial]
#   credit_per_shot:   credit_per_shot, [balance_monthly_free]
#   free:              free=True
#   hqc:               (커스텀 계약)
#
# 선택 키:
#   warnings: list[str] — 사용자에게 표시할 주의사항
# ──────────────────────────────────────────────────────────────

PRICING: dict[str, dict] = {

    # ────────── AWS Braket ──────────
    "ionq_forte1": {
        "vendor": "braket",
        "model": "task+shot",
        "task_usd": 0.30,
        "shot_usd": 0.08,
        "min_shots": 100,
        "max_shots": 5000,
        "confidence": "exact",
        "source": "https://aws.amazon.com/braket/pricing/",
        "updated_at": "2026-04-27",
    },
    "rigetti_cepheus": {
        "vendor": "braket",
        "model": "task+shot",
        "task_usd": 0.30,
        "shot_usd": 0.000425,
        "confidence": "exact",
        "source": "https://aws.amazon.com/braket/pricing/",
        "updated_at": "2026-04-27",
    },
    "quera_aquila": {
        "vendor": "braket",
        "model": "task+shot",
        "task_usd": 0.30,
        "shot_usd": 0.01,
        "confidence": "exact",
        "source": "https://aws.amazon.com/braket/pricing/",
        "updated_at": "2026-04-27",
        "warnings": ["AHS analog 전용 — Qiskit gate 회로 비호환"],
    },
    "braket_sv1": {
        "vendor": "braket",
        "model": "per_minute",
        "per_min_usd": 0.075,
        "confidence": "exact",
        "source": "https://aws.amazon.com/braket/pricing/",
        "updated_at": "2026-04-27",
    },
    "braket_dm1": {
        "vendor": "braket",
        "model": "per_minute",
        "per_min_usd": 0.075,
        "confidence": "exact",
        "source": "https://aws.amazon.com/braket/pricing/",
        "updated_at": "2026-04-27",
    },
    "braket_tn1": {
        "vendor": "braket",
        "model": "per_minute",
        "per_min_usd": 0.275,
        "confidence": "exact",
        "source": "https://aws.amazon.com/braket/pricing/",
        "updated_at": "2026-04-27",
    },

    # ────────── IBM (Open Plan, 무료 한도) ──────────
    "ibm_*": {
        "vendor": "ibm",
        "plan": "open",
        "model": "free_quota",
        "monthly_free_min": 10,
        "confidence": "exact",
        "source": "https://www.ibm.com/quantum/products (Open Plan)",
        "updated_at": "2026-04-27",
        "warnings": [
            "Open Plan: 월 10분 무료 (28일 rolling window)",
            "한도 초과 시 다음 갱신까지 거부 (추가 과금 없음)",
            "한도 잔량은 IBM Quantum Platform 콘솔에서 확인",
        ],
    },

    # ────────── IQM Resonance (한국지사장 지원 크레딧) ──────────
    "iqm_garnet": {
        "vendor": "iqm",
        "model": "credit_per_second",
        "credit_per_sec": 0.50,
        "balance_initial": 3000,
        "confidence": "exact",
        "source": "한국지사장 지원 (계약 기반, 3000 credits 제공)",
        "updated_at": "2026-04-27",
    },
    "iqm_emerald": {
        "vendor": "iqm",
        "model": "credit_per_second",
        "credit_per_sec": 0.75,
        "balance_initial": 3000,
        "confidence": "exact",
        "source": "한국지사장 지원 (계약 기반, 3000 credits 제공)",
        "updated_at": "2026-04-27",
    },
    "iqm_sirius": {
        "vendor": "iqm",
        "model": "credit_per_second",
        "credit_per_sec": 0.30,
        "balance_initial": 3000,
        "confidence": "exact",
        "source": "한국지사장 지원 (계약 기반, 3000 credits 제공)",
        "updated_at": "2026-04-27",
    },

    # ────────── Quandela Hub (월 200 credits 무료) ──────────
    "qpu:belenos": {
        "vendor": "quandela",
        "model": "credit_per_shot",
        "credit_per_shot": 0.000001,
        "balance_monthly_free": 200,
        "confidence": "exact",
        "source": "https://cloud.quandela.com/ (Hub Platforms 페이지, 샷당 가격)",
        "updated_at": "2026-04-27",
    },
    "qpu:ascella": {
        "vendor": "quandela",
        "model": "credit_per_shot",
        "credit_per_shot": 0.000001,
        "balance_monthly_free": 200,
        "confidence": "exact",
        "source": "https://cloud.quandela.com/ (Hub Platforms 페이지, 샷당 가격)",
        "updated_at": "2026-04-27",
    },
    "sim:belenos": {
        "vendor": "quandela",
        "model": "free",
        "free": True,
        "confidence": "exact",
        "source": "https://cloud.quandela.com/ (시뮬레이터 — 0 credit)",
        "updated_at": "2026-04-27",
    },
    "sim:ascella": {
        "vendor": "quandela",
        "model": "free",
        "free": True,
        "confidence": "exact",
        "source": "https://cloud.quandela.com/ (시뮬레이터 — 0 credit)",
        "updated_at": "2026-04-27",
    },

    # ────────── Pasqal (Azure Quantum 경유, Pay-As-You-Go) ──────────
    # 실 QPU는 시간 기반 과금 (EUR/QPU-hour). EUR → USD 환산 1.08.
    # 단순 회로도 수 분 runtime 발생 가능 → 작은 회로도 큰 비용.
    "pasqal_fresnel": {
        "vendor": "azure",
        "azure_provider": "pasqal",
        "model": "per_hour",
        "per_hour_eur": 3000.0,
        "per_hour_usd": 3240.0,           # EUR 3000 × 1.08
        "confidence": "exact",
        "source": "https://learn.microsoft.com/en-us/azure/quantum/pricing (Pasqal PAYG, 확인 2026-04-27)",
        "updated_at": "2026-04-27",
        "warnings": [
            "EUR 3,000 / QPU-hour — 시간 기반, 회로 길이가 비용 결정",
            "단순 회로도 수 분 runtime 발생 가능 — 작은 비용도 수십 USD",
            "estimate는 60초 runtime 가정 — 실 runtime은 더 길 수 있음",
        ],
    },
    "pasqal_fresnel_can1": {
        "vendor": "azure",
        "azure_provider": "pasqal",
        "model": "per_hour",
        "per_hour_eur": 3000.0,
        "per_hour_usd": 3240.0,
        "confidence": "exact",
        "source": "https://learn.microsoft.com/en-us/azure/quantum/pricing (Pasqal PAYG)",
        "updated_at": "2026-04-27",
        "warnings": [
            "Canada 리전 — Fresnel과 동일 단가",
            "EUR 3,000 / QPU-hour",
        ],
    },

    # ────────── Quantinuum (HQC, 별도 계약 필요) ──────────
    "quantinuum_*": {
        "vendor": "quantinuum",
        "model": "hqc",
        "confidence": "verify_required",
        "source": "Quantinuum 계약 확인 필요 (HQC 단위 모델)",
        "updated_at": "2026-04-27",
        "warnings": [
            "HQC(Hardware Quantum Credits) 단위 — 사용자별 계약",
            "정확한 비용은 Quantinuum 콘솔에서 확인 필요",
        ],
    },
}


# ──────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────

def get_pricing(qpu_name: str) -> Optional[dict]:
    """qpu_name에 해당하는 가격 모델 dict 반환. 없으면 None.

    매칭 우선순위:
      1. 정확 매칭 (e.g. "ionq_forte1")
      2. glob 패턴 매칭 (e.g. "ibm_*" → "ibm_fez")
    """
    if qpu_name in PRICING:
        return PRICING[qpu_name]
    for pattern, entry in PRICING.items():
        if "*" in pattern and fnmatch.fnmatch(qpu_name, pattern):
            return entry
    return None


def estimate_cost(qpu_name: str, shots: int = 1024,
                  estimated_runtime_sec: Optional[float] = None) -> dict:
    """
    QPU 제출 비용 추정.

    Args:
        qpu_name: QPU 이름 (e.g. "ionq_forte1", "ibm_fez", "qpu:ascella")
        shots: shot 수
        estimated_runtime_sec: (선택) 명시적 runtime 추정. None이면 model별 기본 추정.

    Returns:
        {
          "qpu": str,
          "shots": int,
          "estimated_usd": Optional[float],
          "estimated_krw": Optional[int],
          "estimated_credits": Optional[float],
          "currency": "USD" | "credits" | "free" | "hqc" | "unknown",
          "model": str,
          "confidence": "exact" | "estimate" | "verify_required" | "unknown",
          "details": str,           # 사람이 읽을 비용 설명
          "warnings": list[str],    # 주의사항
          "source": Optional[str],
          "updated_at": Optional[str],
        }
    """
    pricing = get_pricing(qpu_name)
    if pricing is None:
        return {
            "qpu": qpu_name, "shots": shots,
            "estimated_usd": None, "estimated_krw": None, "estimated_credits": None,
            "currency": "unknown", "model": "unknown", "confidence": "unknown",
            "details": f"가격 정보 없음 (qpu_name={qpu_name})",
            "warnings": [f"{qpu_name}: 가격 모델 미등록. uqi_pricing.py 확인 필요."],
            "source": None, "updated_at": None,
        }

    model = pricing.get("model", "unknown")
    base_warnings = list(pricing.get("warnings", []))

    result = {
        "qpu": qpu_name, "shots": shots,
        "estimated_usd": None, "estimated_krw": None, "estimated_credits": None,
        "currency": None, "model": model,
        "confidence": pricing.get("confidence", "unknown"),
        "source": pricing.get("source"),
        "updated_at": pricing.get("updated_at"),
        "warnings": base_warnings,
    }

    # ───── Braket task+shot ─────
    if model == "task+shot":
        task = pricing["task_usd"]
        shot = pricing["shot_usd"]
        # shot 범위 검증
        min_shots = pricing.get("min_shots")
        max_shots = pricing.get("max_shots")
        if min_shots and shots < min_shots:
            result["warnings"].append(
                f"⚠ {qpu_name} 최소 {min_shots} shots 필요 (요청: {shots})"
            )
        if max_shots and shots > max_shots:
            result["warnings"].append(
                f"⚠ {qpu_name} 최대 {max_shots} shots 제한 (요청: {shots})"
            )
        usd = task + shots * shot
        result["estimated_usd"] = round(usd, 4)
        result["estimated_krw"] = int(usd * USD_TO_KRW)
        result["currency"] = "USD"
        result["details"] = (
            f"${task:.2f} (task) + {shots} × ${shot:.6f} (shot) = ${usd:.4f}"
        )

    # ───── Braket per_minute (시뮬레이터) ─────
    elif model == "per_minute":
        per_min = pricing["per_min_usd"]
        # runtime 추정: 명시 안 되면 기본 10초
        runtime_sec = estimated_runtime_sec if estimated_runtime_sec else 10.0
        usd = per_min * (runtime_sec / 60.0)
        result["estimated_usd"] = round(usd, 4)
        result["estimated_krw"] = int(usd * USD_TO_KRW)
        result["currency"] = "USD"
        result["details"] = f"${per_min:.3f}/min × {runtime_sec:.0f}s ≈ ${usd:.4f}"
        result["warnings"].append("Runtime은 회로 복잡도에 따라 변동")

    # ───── Azure per_hour (Pasqal Fresnel) ─────
    elif model == "per_hour":
        per_hour_usd = pricing["per_hour_usd"]
        per_hour_eur = pricing.get("per_hour_eur")
        # runtime 추정: 명시 안 되면 60초 (Pasqal 보수적 가정)
        runtime_sec = estimated_runtime_sec if estimated_runtime_sec else 60.0
        usd = per_hour_usd * (runtime_sec / 3600.0)
        result["estimated_usd"] = round(usd, 4)
        result["estimated_krw"] = int(usd * USD_TO_KRW)
        result["currency"] = "USD"
        eur_str = f" (EUR {per_hour_eur:.0f}/hour)" if per_hour_eur else ""
        result["details"] = (
            f"${per_hour_usd:.0f}/hour{eur_str} × {runtime_sec:.0f}s ≈ ${usd:.4f}"
        )
        result["warnings"].append(
            "⚠️ Pasqal Fresnel은 시간 기반 — 실 runtime이 추정보다 길면 비용 급증"
        )
        if usd >= 10:
            result["warnings"].append(
                f"⚠️ 예상 비용 ${usd:.2f} — 작은 회로도 비싼 편, runtime 신중 결정"
            )

    # ───── IBM Open Plan ─────
    elif model == "free_quota":
        result["estimated_usd"] = 0.0
        result["estimated_krw"] = 0
        result["currency"] = "free"
        free_min = pricing.get("monthly_free_min", 10)
        result["details"] = f"IBM Open Plan: 월 {free_min}분 무료 (28일 rolling)"

    # ───── IQM credit_per_second ─────
    elif model == "credit_per_second":
        cps = pricing["credit_per_sec"]
        # runtime 추정: 1024 shots ≈ 30초 (IQM 평균, 거친 추정)
        runtime_sec = (
            estimated_runtime_sec if estimated_runtime_sec
            else max(10.0, shots * 30 / 1024)
        )
        credits = cps * runtime_sec
        result["estimated_credits"] = round(credits, 2)
        result["currency"] = "credits"
        balance = pricing.get("balance_initial")
        balance_str = f", 초기잔량 {balance} credits" if balance else ""
        result["details"] = (
            f"~{runtime_sec:.0f}s × {cps} credit/s = ~{credits:.1f} credits{balance_str}"
        )
        result["warnings"].append("Runtime 추정은 회로 복잡도에 따라 ±50% 변동")

    # ───── Quandela credit_per_shot ─────
    elif model == "credit_per_shot":
        cps = pricing["credit_per_shot"]
        credits = cps * shots
        result["estimated_credits"] = round(credits, 6)
        result["currency"] = "credits"
        free = pricing.get("balance_monthly_free")
        free_str = f", 월 {free} credits 무료" if free else ""
        result["details"] = (
            f"{shots} shots × {cps} credit/shot = {credits:.6f} credits{free_str}"
        )
        if free and credits < free:
            result["warnings"].append(
                f"무료 한도({free} credits/월) 내 — 사실상 무료"
            )

    # ───── 무료 (시뮬레이터/공개 API) ─────
    elif model == "free":
        result["estimated_usd"] = 0.0
        result["estimated_krw"] = 0
        result["currency"] = "free"
        result["details"] = "무료 (시뮬레이터 또는 공개 API)"

    # ───── HQC (Quantinuum) ─────
    elif model == "hqc":
        result["currency"] = "hqc"
        result["details"] = (
            "HQC(Hardware Quantum Credits) 단위 — Quantinuum 콘솔에서 확인"
        )

    return result


def format_cost_summary(estimate: dict) -> str:
    """비용 추정 dict을 사람이 읽기 좋은 multi-line 문자열로 포맷."""
    qpu = estimate["qpu"]
    conf = estimate.get("confidence", "unknown")
    details = estimate.get("details", "")

    if estimate.get("estimated_usd") is not None and estimate["currency"] != "free":
        usd = estimate["estimated_usd"]
        krw = estimate["estimated_krw"]
        cost = f"${usd:.4f} (~{krw:,}원)"
    elif estimate.get("estimated_credits") is not None:
        cost = f"{estimate['estimated_credits']} credits"
    elif estimate.get("currency") == "free":
        cost = "무료"
    elif estimate.get("currency") == "hqc":
        cost = "HQC 단위 (별도 확인)"
    else:
        cost = "추정 불가"

    lines = [
        f"[{qpu}] 비용 추정 (신뢰도: {conf})",
        f"  예상: {cost}",
        f"  근거: {details}",
    ]

    for w in estimate.get("warnings", []):
        lines.append(f"  ⚠ {w}")

    src = estimate.get("source")
    if src:
        lines.append(f"  source: {src}")

    return "\n".join(lines)


def format_actual_cost(vendor: str, qpu_name: str,
                       cost: Optional[dict] = None) -> str:
    """vendor별 통화 단위 다르게 표시 (소수점 2자리).

    - braket (IonQ/Rigetti/QuEra) → USD ($X.XX)
    - azure (Pasqal)               → KRW (₩X,XXX)
    - iqm                          → credits (X.XX credits)
    - quandela                     → credits (X.XXXX credits, 작은 단위)
    - ibm                          → 무료 (Open Plan)
    - quantinuum                   → HQC (별도)
    """
    if cost is None:
        cost = estimate_cost(qpu_name, 1)

    currency = cost.get("currency")

    # 가격 모델의 currency 기반 우선 분기 (jobs.db vendor와 무관하게 정확)
    if currency == "hqc":
        # Quantinuum HQC — Azure 경유든 자사든 동일 표시
        return "HQC (별도)"
    if currency == "free":
        # 무료 — IBM Open Plan / Pasqal sim / 공개 API 등
        if vendor == "ibm":
            return "무료 (Open Plan)"
        return "무료"

    # vendor별 단위 분기 (currency가 명확하지 않은 경우)
    if vendor == "ibm":
        return "무료 (Open Plan)"
    if vendor == "braket":
        usd = cost.get("estimated_usd")
        return f"${usd:.2f}" if usd is not None else "—"
    if vendor == "azure":
        krw = cost.get("estimated_krw")
        return f"₩{int(krw):,}" if krw is not None else "—"
    if vendor == "iqm":
        cr = cost.get("estimated_credits")
        return f"{cr:.2f} credits" if cr is not None else "—"
    if vendor == "quandela":
        cr = cost.get("estimated_credits")
        return f"{cr:.4f} credits" if cr is not None else "—"
    if vendor == "quantinuum":
        return "HQC (별도)"
    # fallback
    if cost.get("estimated_usd") is not None:
        return f"${cost['estimated_usd']:.2f}"
    if cost.get("estimated_credits") is not None:
        return f"{cost['estimated_credits']:.2f} credits"
    return "—"


def format_actual_cost_token(vendor: str, qpu_name: str,
                              cost: Optional[dict] = None) -> Optional[str]:
    """i18n 처리용 토큰 반환.

    숫자 포함 가격(USD/EUR/credits 등)은 None — 그대로 표시.
    무료/HQC 등 Korean 고정문구만 토큰화 ⇒ webapp에서 locale 변환.

    Returns:
        "free"            → "무료" / "Free" / "Gratuit"
        "free_open_plan"  → "무료 (Open Plan)" / "Free (Open Plan)" / "Gratuit (Open Plan)"
        "hqc_separate"    → "HQC (별도)" / "HQC (separate)" / "HQC (séparé)"
        None              → 토큰 없음 (cost.display 그대로 사용)
    """
    if cost is None:
        cost = estimate_cost(qpu_name, 1)
    currency = cost.get("currency")
    if currency == "hqc":
        return "hqc_separate"
    if currency == "free":
        return "free_open_plan" if vendor == "ibm" else "free"
    if vendor == "ibm":
        return "free_open_plan"
    if vendor == "quantinuum":
        return "hqc_separate"
    return None


# QPU 이름 → (제조사, 모델명) 매핑
# 청구처(billing source)와 다름. 청구처는 게이트웨이/자사 클라우드 (get_cost_source).
_QPU_IDENTITY_MAP: dict[str, tuple[str, str]] = {
    # 제조사       모델명
    "ionq_forte1":         ("IonQ",       "Forte-1"),
    "rigetti_cepheus":     ("Rigetti",    "Cepheus-1-108Q"),
    "rigetti_ankaa3":      ("Rigetti",    "Ankaa-3 (retired)"),
    "quera_aquila":        ("QuEra",      "Aquila"),
    "pasqal_fresnel":      ("Pasqal",     "Fresnel"),
    "pasqal_fresnel_can1": ("Pasqal",     "Fresnel-CAN1"),
    "ibm_fez":             ("IBM",        "Fez (Heron R2)"),
    "ibm_marrakesh":       ("IBM",        "Marrakesh (Heron R2)"),
    "ibm_kingston":        ("IBM",        "Kingston (Heron R2)"),
    "iqm_garnet":          ("IQM",        "Garnet"),
    "iqm_emerald":         ("IQM",        "Emerald"),
    "iqm_sirius":          ("IQM",        "Sirius"),
    "qpu:ascella":         ("Quandela",   "Ascella"),
    "qpu:belenos":         ("Quandela",   "Belenos"),
    "sim:ascella":         ("Quandela",   "Ascella (sim)"),
    "sim:belenos":         ("Quandela",   "Belenos (sim)"),
    "quantinuum_h2_1":     ("Quantinuum", "H2-1"),
    "quantinuum_h2_2":     ("Quantinuum", "H2-2"),
    "quantinuum_h1_1":     ("Quantinuum", "H1-1"),
    "quantinuum_h2_1sc":   ("Quantinuum", "H2-1SC"),    # Syntax Checker (Azure)
    "quantinuum_h2_1e":    ("Quantinuum", "H2-1E"),     # Emulator (Azure)
    "braket_sv1":          ("Amazon",     "SV1 (sim)"),
    "braket_dm1":          ("Amazon",     "DM1 (sim)"),
    "braket_tn1":          ("Amazon",     "TN1 (sim)"),
}


def parse_qpu_identity(qpu_name: str) -> tuple[str, str]:
    """QPU 이름 → (제조사, 모델명) 분리.

    예:
      "rigetti_cepheus" → ("Rigetti", "Cepheus-1-108Q")
      "pasqal_fresnel"  → ("Pasqal", "Fresnel")
      "ibm_fez"         → ("IBM", "Fez (Heron R2)")
      "qpu:ascella"     → ("Quandela", "Ascella")

    매핑 미등록 시: 휴리스틱(prefix split)로 fallback.
    """
    if qpu_name in _QPU_IDENTITY_MAP:
        return _QPU_IDENTITY_MAP[qpu_name]
    # 휴리스틱
    if qpu_name.startswith(("qpu:", "sim:")):
        suffix = qpu_name.split(":", 1)[1]
        return ("Quandela", suffix.title())
    if "_" in qpu_name:
        prefix, model = qpu_name.split("_", 1)
        return (prefix.title(), model.replace("_", "-").title())
    return ("Unknown", qpu_name)


def get_cost_source(vendor: str, qpu_name: str = "") -> str:
    """비용 추정의 출처(게이트웨이 또는 자사 클라우드) 라벨 반환.

    벤더 → 게이트웨이/자사 클라우드 매핑:
      - braket    → AWS Braket
      - azure     → Azure Quantum
      - ibm       → IBM Open Plan
      - iqm       → IQM Resonance
      - quandela  → Quandela Cloud
      - quantinuum → Quantinuum Nexus / Azure
      - pasqal    → Pasqal Cloud  (자사 — Azure 게이트웨이 거치지 않은 경우)
    """
    if vendor == "braket":      return "AWS Braket"
    if vendor == "azure":       return "Azure Quantum"
    if vendor == "ibm":         return "IBM Open Plan"
    if vendor == "iqm":         return "IQM Resonance"
    if vendor == "quandela":    return "Quandela Cloud"
    if vendor == "quantinuum":  return "Quantinuum Nexus / Azure"
    if vendor == "pasqal":      return "Pasqal Cloud"
    return "—"


def format_duration(seconds: Optional[float]) -> str:
    """초 단위를 사람이 읽기 좋은 형태로 (소수점 2자리).

    < 60초:    "X.XXs"
    < 1시간:   "Xm Ys"
    >= 1시간:  "Xh Ym"
    """
    if seconds is None:
        return "—"
    s = float(seconds)
    if s < 0:
        return "—"
    if s < 60:
        return f"{s:.2f}s"
    if s < 3600:
        m, sec = divmod(s, 60)
        return f"{int(m)}m {sec:.0f}s"
    h, rem = divmod(s, 3600)
    m, _ = divmod(rem, 60)
    return f"{int(h)}h {int(m)}m"


def list_stale_entries(days: int = 90) -> list[str]:
    """updated_at이 days일 이전인 entry 목록. 정기 점검용.

    Args:
        days: 기준 일수 (기본 90일)

    Returns:
        오래된 가격 모델 이름 리스트.
    """
    today = datetime.now(timezone.utc).date()
    stale = []
    for qpu, p in PRICING.items():
        upd = p.get("updated_at")
        if not upd:
            stale.append(qpu)
            continue
        try:
            d = datetime.fromisoformat(upd).date()
            if (today - d).days > days:
                stale.append(qpu)
        except Exception:
            stale.append(qpu)
    return stale
