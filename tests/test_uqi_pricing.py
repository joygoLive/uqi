# test_uqi_pricing.py — QPU 가격 추정 모듈 테스트

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from uqi_pricing import (
    PRICING, USD_TO_KRW, LAST_FULL_REVIEW,
    get_pricing, estimate_cost, format_cost_summary, list_stale_entries,
)


# ─────────────────────────────────────────────────────────────
# get_pricing — 정확/glob 매칭
# ─────────────────────────────────────────────────────────────

def test_TC001_get_pricing_exact_match():
    p = get_pricing("ionq_forte1")
    assert p is not None
    assert p["vendor"] == "braket"
    assert p["task_usd"] == 0.30
    assert p["shot_usd"] == 0.08

def test_TC002_get_pricing_glob_ibm():
    p = get_pricing("ibm_fez")
    assert p is not None
    assert p["vendor"] == "ibm"
    assert p["model"] == "free_quota"

def test_TC003_get_pricing_glob_ibm_marrakesh():
    p = get_pricing("ibm_marrakesh")
    assert p is not None
    assert p["plan"] == "open"

def test_TC004_get_pricing_glob_quantinuum():
    p = get_pricing("quantinuum_h2_1")
    assert p is not None
    assert p["model"] == "hqc"

def test_TC005_get_pricing_unknown():
    p = get_pricing("unknown_qpu_xyz")
    assert p is None


# ─────────────────────────────────────────────────────────────
# estimate_cost — Braket task+shot 모델
# ─────────────────────────────────────────────────────────────

def test_TC010_ionq_forte1_100shots_8usd():
    e = estimate_cost("ionq_forte1", 100)
    assert e["estimated_usd"] == 8.30
    assert e["currency"] == "USD"
    assert e["model"] == "task+shot"

def test_TC011_ionq_forte1_1024shots_82usd():
    e = estimate_cost("ionq_forte1", 1024)
    assert abs(e["estimated_usd"] - 82.22) < 0.01

def test_TC012_ionq_forte1_50shots_min_warning():
    e = estimate_cost("ionq_forte1", 50)
    assert any("최소 100" in w for w in e["warnings"])

def test_TC013_ionq_forte1_10000shots_max_warning():
    e = estimate_cost("ionq_forte1", 10000)
    assert any("최대 5000" in w for w in e["warnings"])

def test_TC014_rigetti_cepheus_1000shots():
    e = estimate_cost("rigetti_cepheus", 1000)
    assert abs(e["estimated_usd"] - 0.725) < 0.001
    assert e["confidence"] == "exact"

def test_TC015_quera_aquila_ahs_warning():
    e = estimate_cost("quera_aquila", 100)
    assert abs(e["estimated_usd"] - 1.30) < 0.01
    assert any("AHS" in w for w in e["warnings"])

def test_TC016_krw_conversion():
    e = estimate_cost("ionq_forte1", 100)
    assert e["estimated_krw"] == int(8.30 * USD_TO_KRW)


# ─────────────────────────────────────────────────────────────
# estimate_cost — Braket per_minute (시뮬레이터)
# ─────────────────────────────────────────────────────────────

def test_TC020_braket_sv1_60s():
    e = estimate_cost("braket_sv1", 100, estimated_runtime_sec=60)
    assert abs(e["estimated_usd"] - 0.075) < 0.001

def test_TC021_braket_tn1_60s():
    e = estimate_cost("braket_tn1", 100, estimated_runtime_sec=60)
    assert abs(e["estimated_usd"] - 0.275) < 0.001

def test_TC022_braket_sv1_default_runtime():
    e = estimate_cost("braket_sv1", 100)   # default 10s
    assert abs(e["estimated_usd"] - 0.0125) < 0.001


# ─────────────────────────────────────────────────────────────
# estimate_cost — Azure per_hour (Pasqal)
# ─────────────────────────────────────────────────────────────

def test_TC030_pasqal_fresnel_60s():
    e = estimate_cost("pasqal_fresnel", 100, estimated_runtime_sec=60)
    assert abs(e["estimated_usd"] - 54.0) < 0.5

def test_TC031_pasqal_fresnel_10min():
    e = estimate_cost("pasqal_fresnel", 100, estimated_runtime_sec=600)
    assert abs(e["estimated_usd"] - 540.0) < 5.0

def test_TC032_pasqal_fresnel_can1_same_price():
    e1 = estimate_cost("pasqal_fresnel",      100, estimated_runtime_sec=60)
    e2 = estimate_cost("pasqal_fresnel_can1", 100, estimated_runtime_sec=60)
    assert e1["estimated_usd"] == e2["estimated_usd"]

def test_TC033_pasqal_fresnel_high_cost_warning():
    e = estimate_cost("pasqal_fresnel", 100, estimated_runtime_sec=60)
    # $54 >= $10 임계값 → 추가 경고
    assert any("작은 회로도 비싼 편" in w for w in e["warnings"])


# ─────────────────────────────────────────────────────────────
# estimate_cost — IBM free_quota
# ─────────────────────────────────────────────────────────────

def test_TC040_ibm_fez_free():
    e = estimate_cost("ibm_fez", 1024)
    assert e["currency"] == "free"
    assert e["estimated_usd"] == 0.0

def test_TC041_ibm_open_plan_warning():
    e = estimate_cost("ibm_fez", 1024)
    assert any("10분" in w for w in e["warnings"])


# ─────────────────────────────────────────────────────────────
# estimate_cost — IQM credit_per_second
# ─────────────────────────────────────────────────────────────

def test_TC050_iqm_garnet_runtime_estimate():
    # 1024 shots ≈ 30s × 0.50 = 15 credits
    e = estimate_cost("iqm_garnet", 1024)
    assert 14.5 <= e["estimated_credits"] <= 15.5
    assert e["currency"] == "credits"

def test_TC051_iqm_emerald_higher_rate():
    e = estimate_cost("iqm_emerald", 1024)
    assert 22.0 <= e["estimated_credits"] <= 23.0

def test_TC052_iqm_sirius_lower_rate():
    e = estimate_cost("iqm_sirius", 1024)
    assert 8.5 <= e["estimated_credits"] <= 9.5

def test_TC053_iqm_explicit_runtime():
    e = estimate_cost("iqm_garnet", 1024, estimated_runtime_sec=10)
    assert e["estimated_credits"] == 5.0   # 10s × 0.50


# ─────────────────────────────────────────────────────────────
# estimate_cost — Quandela credit_per_shot
# ─────────────────────────────────────────────────────────────

def test_TC060_quandela_qpu_ascella():
    e = estimate_cost("qpu:ascella", 1024)
    assert abs(e["estimated_credits"] - 0.001024) < 1e-6

def test_TC061_quandela_qpu_belenos_1m_shots():
    e = estimate_cost("qpu:belenos", 1000000)
    assert e["estimated_credits"] == 1.0

def test_TC062_quandela_sim_free():
    e = estimate_cost("sim:ascella", 1024)
    assert e["currency"] == "free"
    assert e["estimated_usd"] == 0.0

def test_TC063_quandela_free_quota_message():
    e = estimate_cost("qpu:ascella", 1024)
    assert any("무료 한도" in w for w in e["warnings"])


# ─────────────────────────────────────────────────────────────
# estimate_cost — Pasqal Cloud / Quantinuum HQC
# ─────────────────────────────────────────────────────────────

def test_TC070_pasqal_fresnel_per_hour_model():
    e = estimate_cost("pasqal_fresnel", 100)
    assert e["model"] == "per_hour"

def test_TC071_quantinuum_hqc_model():
    e = estimate_cost("quantinuum_h2_1", 1024)
    assert e["model"] == "hqc"
    assert e["confidence"] == "verify_required"


# ─────────────────────────────────────────────────────────────
# estimate_cost — 미등록/Edge case
# ─────────────────────────────────────────────────────────────

def test_TC080_unknown_qpu_unknown_confidence():
    e = estimate_cost("brand_new_qpu", 100)
    assert e["confidence"] == "unknown"
    assert e["estimated_usd"] is None
    assert any("미등록" in w for w in e["warnings"])


# ─────────────────────────────────────────────────────────────
# format_cost_summary
# ─────────────────────────────────────────────────────────────

def test_TC090_format_usd_with_krw():
    e = estimate_cost("ionq_forte1", 100)
    s = format_cost_summary(e)
    assert "$8.3" in s
    assert "원" in s
    assert "exact" in s

def test_TC091_format_credits():
    e = estimate_cost("qpu:ascella", 1024)
    s = format_cost_summary(e)
    assert "credits" in s

def test_TC092_format_free():
    e = estimate_cost("ibm_fez", 1024)
    s = format_cost_summary(e)
    assert "무료" in s

def test_TC093_format_hqc():
    e = estimate_cost("quantinuum_h2_1", 1024)
    s = format_cost_summary(e)
    assert "HQC" in s


# ─────────────────────────────────────────────────────────────
# list_stale_entries
# ─────────────────────────────────────────────────────────────

def test_TC100_list_stale_recent():
    # 최근 갱신된 것들은 비어있음
    stale = list_stale_entries(days=365)
    assert stale == []

def test_TC101_list_stale_strict():
    # 1일 기준이면 거의 모두 stale (오늘 갱신 외)
    stale = list_stale_entries(days=1)
    # 모든 entry가 2026-04-27 갱신이면 빈 리스트, 그 이전이면 모두 stale
    assert isinstance(stale, list)


# ─────────────────────────────────────────────────────────────
# PRICING dict 무결성
# ─────────────────────────────────────────────────────────────

def test_TC110_all_entries_have_required_fields():
    for qpu, entry in PRICING.items():
        assert "vendor"     in entry, f"{qpu} missing vendor"
        assert "model"      in entry, f"{qpu} missing model"
        assert "confidence" in entry, f"{qpu} missing confidence"
        assert "source"     in entry, f"{qpu} missing source"
        assert "updated_at" in entry, f"{qpu} missing updated_at"

def test_TC111_known_qpus_present():
    must_have = [
        "ionq_forte1", "rigetti_cepheus", "quera_aquila",
        "braket_sv1", "ibm_*", "iqm_garnet",
        "qpu:ascella", "sim:ascella",
        "pasqal_fresnel", "quantinuum_*",
    ]
    for q in must_have:
        assert q in PRICING, f"{q} missing in PRICING"

def test_TC112_aria1_removed():
    """Aria-1 retire 정리 확인"""
    assert "ionq_aria1" not in PRICING

def test_TC113_ankaa3_removed():
    """Rigetti Ankaa-3 retire 정리 확인"""
    assert "rigetti_ankaa3" not in PRICING

def test_TC114_pasqal_per_hour_eur_3000():
    """Pasqal 가격 정합성 (Azure pricing 페이지 정가)"""
    p = PRICING["pasqal_fresnel"]
    assert p["per_hour_eur"] == 3000.0

def test_TC115_ionq_min_max_shots():
    p = PRICING["ionq_forte1"]
    assert p["min_shots"] == 100
    assert p["max_shots"] == 5000


# ─────────────────────────────────────────────────────────────
# 환율/모듈 상수
# ─────────────────────────────────────────────────────────────

def test_TC120_usd_to_krw_set():
    assert USD_TO_KRW > 1000   # 합리적 범위
    assert USD_TO_KRW < 2000

def test_TC121_last_review_set():
    assert LAST_FULL_REVIEW   # 비어있지 않음
    assert "-" in LAST_FULL_REVIEW   # ISO 형식


# ─────────────────────────────────────────────────────────────
# format_actual_cost — vendor별 단위 표시
# ─────────────────────────────────────────────────────────────

from uqi_pricing import format_actual_cost, format_duration


def test_TC130_format_braket_usd():
    cost = estimate_cost("ionq_forte1", 100)
    assert format_actual_cost("braket", "ionq_forte1", cost) == "$8.30"

def test_TC131_format_azure_krw():
    cost = estimate_cost("pasqal_fresnel", 100)
    s = format_actual_cost("azure", "pasqal_fresnel", cost)
    assert s.startswith("₩")
    assert "," in s            # 천단위 콤마

def test_TC132_format_iqm_credits():
    cost = estimate_cost("iqm_emerald", 1024)
    s = format_actual_cost("iqm", "iqm_emerald", cost)
    assert "credits" in s
    assert "22.50" in s

def test_TC133_format_quandela_credits_4digits():
    """Quandela는 매우 작은 단위라 소수점 4자리"""
    cost = estimate_cost("qpu:ascella", 1024)
    s = format_actual_cost("quandela", "qpu:ascella", cost)
    assert "credits" in s
    # 0.0010 형식 (1024 × 0.000001 = 0.001024 → 0.0010)
    assert "0.0010" in s or "0.001" in s

def test_TC134_format_ibm_free():
    cost = estimate_cost("ibm_fez", 1024)
    s = format_actual_cost("ibm", "ibm_fez", cost)
    assert "무료" in s

def test_TC135_format_quantinuum_hqc():
    cost = estimate_cost("quantinuum_h2_1", 1024)
    s = format_actual_cost("quantinuum", "quantinuum_h2_1", cost)
    assert "HQC" in s

def test_TC136_format_unknown_vendor_fallback():
    cost = estimate_cost("ionq_forte1", 100)
    # 모르는 vendor도 fallback (estimated_usd 있으면 USD)
    s = format_actual_cost("unknown_vendor", "ionq_forte1", cost)
    assert s == "$8.30"


# ─────────────────────────────────────────────────────────────
# format_actual_cost_token — i18n 토큰 (무료/HQC 등)
# ─────────────────────────────────────────────────────────────

from uqi_pricing import format_actual_cost_token


def test_TC137_token_ibm_free_open_plan():
    cost = estimate_cost("ibm_fez", 1024)
    assert format_actual_cost_token("ibm", "ibm_fez", cost) == "free_open_plan"

def test_TC138_token_quantinuum_hqc():
    cost = estimate_cost("quantinuum_h2_1", 1024)
    assert format_actual_cost_token("quantinuum", "quantinuum_h2_1", cost) == "hqc_separate"

def test_TC139_token_quantinuum_via_azure_hqc():
    """Azure 경유 Quantinuum도 HQC 토큰 (currency 우선)"""
    cost = estimate_cost("quantinuum_h2_1sc", 1024)
    assert format_actual_cost_token("azure", "quantinuum_h2_1sc", cost) == "hqc_separate"

def test_TC139b_token_paid_returns_none():
    """USD/EUR/credits 등 숫자 가격은 토큰 없음 (display 그대로 사용)"""
    cost = estimate_cost("ionq_forte1", 100)
    assert format_actual_cost_token("braket", "ionq_forte1", cost) is None
    cost2 = estimate_cost("pasqal_fresnel", 100)
    assert format_actual_cost_token("azure", "pasqal_fresnel", cost2) is None


# ─────────────────────────────────────────────────────────────
# format_duration — 사람 읽기 좋게
# ─────────────────────────────────────────────────────────────

def test_TC140_format_duration_subsecond():
    assert format_duration(0.5) == "0.50s"

def test_TC141_format_duration_seconds():
    assert format_duration(4.32) == "4.32s"
    assert format_duration(57) == "57.00s"

def test_TC142_format_duration_minute_boundary():
    assert format_duration(60) == "1m 0s"

def test_TC143_format_duration_minutes_seconds():
    # 357 = 5*60 + 57
    assert format_duration(357) == "5m 57s"

def test_TC144_format_duration_hours():
    # 7890 = 2h 11m 30s → "2h 11m"
    assert format_duration(7890) == "2h 11m"

def test_TC145_format_duration_none():
    assert format_duration(None) == "—"

def test_TC146_format_duration_negative():
    assert format_duration(-5) == "—"


# ─────────────────────────────────────────────────────────────
# get_cost_source — 비용 추정 출처 라벨
# ─────────────────────────────────────────────────────────────

from uqi_pricing import get_cost_source


def test_TC150_source_braket():
    assert get_cost_source("braket") == "AWS Braket"

def test_TC151_source_azure():
    assert get_cost_source("azure") == "Azure Quantum"

def test_TC152_source_ibm():
    s = get_cost_source("ibm")
    assert "IBM" in s and "Open" in s

def test_TC153_source_iqm():
    s = get_cost_source("iqm")
    assert "IQM" in s

def test_TC154_source_quandela():
    s = get_cost_source("quandela")
    assert "Quandela" in s

def test_TC155_source_quantinuum():
    s = get_cost_source("quantinuum")
    assert "Quantinuum" in s
    # Nexus 또는 Azure 둘 중 하나 안내
    assert ("Nexus" in s) or ("Azure" in s)

def test_TC156_source_pasqal():
    s = get_cost_source("pasqal")
    assert "Pasqal" in s

def test_TC157_source_unknown():
    assert get_cost_source("unknown_vendor") == "—"
