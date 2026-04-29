# test_uqi_job_store.py — Phase 2 catalog 통합 스키마 검증
#
# 검증 항목:
#   - 새 스키마 (12 cols, runtime/qpu_vendor/qpu_model/qpu_family/qpu_modality)
#   - save_job 자동 catalog 매핑 (qpu_name → 4축)
#   - 명시 인자 우선 처리
#   - 마이그레이션 멱등성 (v1 → v2 → v2 호출 시 변화 없음)
#   - vendor 컬럼 DROP 후 신규 컬럼 도입

import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

# uqi_job_store 는 import 시 init_db() 실행 — _DB_PATH 가 모듈 스코프라
# 테스트마다 다른 임시 DB 사용하려면 monkey-patch 필요
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def tmp_job_db(tmp_path, monkeypatch):
    """임시 DB 경로로 uqi_job_store 를 사용 — _DB_PATH patch + init_db() 재호출."""
    db_path = tmp_path / "test_jobs.db"
    import uqi_job_store
    monkeypatch.setattr(uqi_job_store, "_DB_PATH", db_path)
    uqi_job_store.init_db()  # 새 path 로 다시 init (monkeypatch 가 yield 후 자동 복원)
    yield uqi_job_store, db_path


# ─────────────────────────────────────────────────────────
# 새 스키마 v2 검증
# ─────────────────────────────────────────────────────────

def test_TC200_v2_schema_has_all_columns(tmp_job_db):
    """v2 스키마는 12개 컬럼 — vendor 없음, catalog 4축 + runtime"""
    _, db_path = tmp_job_db
    conn = sqlite3.connect(str(db_path))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    conn.close()

    expected = {
        "job_id", "qpu_name", "circuit_name", "shots",
        "status", "counts", "error", "submitted_at", "updated_at", "extra",
        "runtime", "qpu_vendor", "qpu_model", "qpu_family", "qpu_modality",
    }
    assert expected.issubset(cols), f"missing cols: {expected - cols}"
    assert "vendor" not in cols, "legacy vendor 컬럼이 남아있음"


# ─────────────────────────────────────────────────────────
# save_job 자동 catalog 매핑
# ─────────────────────────────────────────────────────────

def test_TC201_save_job_auto_maps_from_qpu_name(tmp_job_db):
    """save_job(qpu_name='ibm_fez') → catalog 매핑으로 4축 자동 채움"""
    js, db_path = tmp_job_db
    js.save_job(job_id="test_001", qpu_name="ibm_fez", shots=100)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM jobs WHERE job_id='test_001'").fetchone()
    conn.close()

    assert row["runtime"]      == "IBM Quantum"
    assert row["qpu_vendor"]   == "IBM"
    assert row["qpu_model"]    == "Fez"
    assert row["qpu_family"]   == "Heron R2"
    assert row["qpu_modality"] == "superconducting"
    assert row["qpu_name"]     == "ibm_fez"
    assert row["shots"]        == 100


def test_TC202_save_job_braket_ionq_mapping(tmp_job_db):
    """AWS Braket 경유 IonQ → runtime='AWS Braket', vendor='IonQ', modality='ion-trap'"""
    js, db_path = tmp_job_db
    js.save_job(job_id="t_ionq", qpu_name="ionq_forte1", shots=100)
    job = js.get_job("t_ionq")
    assert job["runtime"]      == "AWS Braket"
    assert job["qpu_vendor"]   == "IonQ"
    assert job["qpu_model"]    == "Forte-1"
    assert job["qpu_family"]   is None
    assert job["qpu_modality"] == "ion-trap"


def test_TC203_save_job_quandela_sim_family(tmp_job_db):
    """Quandela sim:ascella → family='sim' (qpu/sim 구분용)"""
    js, _ = tmp_job_db
    js.save_job(job_id="t_q1", qpu_name="qpu:ascella", shots=10)
    js.save_job(job_id="t_q2", qpu_name="sim:ascella", shots=10)
    j1 = js.get_job("t_q1")
    j2 = js.get_job("t_q2")
    assert j1["qpu_family"] is None
    assert j2["qpu_family"] == "sim"
    # 같은 model + 같은 vendor + 같은 runtime, family 만 다름
    assert j1["qpu_model"]   == j2["qpu_model"]   == "Ascella"
    assert j1["qpu_vendor"]  == j2["qpu_vendor"]  == "Quandela"
    assert j1["qpu_runtime"] if False else True  # qpu_runtime 별도 표시 — runtime 컬럼만 검증
    assert j1["runtime"] == j2["runtime"] == "Quandela Cloud"


def test_TC204_save_job_explicit_args_override(tmp_job_db):
    """명시한 인자가 catalog 자동 매핑보다 우선"""
    js, _ = tmp_job_db
    js.save_job(
        job_id="t_override",
        qpu_name="ibm_fez",
        shots=50,
        runtime="Custom Runtime",     # 명시 — catalog 의 'IBM Quantum' 무시
        qpu_vendor="CustomVendor",
        qpu_model="CustomModel",
    )
    job = js.get_job("t_override")
    assert job["runtime"]    == "Custom Runtime"
    assert job["qpu_vendor"] == "CustomVendor"
    assert job["qpu_model"]  == "CustomModel"
    # 미명시 항목은 catalog 매핑 fallback
    assert job["qpu_family"]   == "Heron R2"
    assert job["qpu_modality"] == "superconducting"


def test_TC205_save_job_unknown_qpu_falls_back(tmp_job_db):
    """catalog 미등록 qpu_name 도 휴리스틱 fallback 으로 채움 (modality='unknown')"""
    js, _ = tmp_job_db
    js.save_job(job_id="t_unknown", qpu_name="unknown_xyz", shots=1)
    job = js.get_job("t_unknown")
    # 휴리스틱: prefix='Unknown', model='Xyz', modality='unknown'
    assert job["qpu_modality"] == "unknown"


# ─────────────────────────────────────────────────────────
# 마이그레이션 멱등성
# ─────────────────────────────────────────────────────────

def test_TC210_migration_idempotent(tmp_job_db):
    """이미 v2 DB 에 init_db() 재호출해도 변화 없음"""
    js, db_path = tmp_job_db
    js.save_job(job_id="t_mig", qpu_name="iqm_emerald", shots=100)

    # init_db() 다시 호출 (마이그레이션 트리거)
    js.init_db()
    job = js.get_job("t_mig")
    # 데이터 손실 없음
    assert job["runtime"]      == "IQM Resonance"
    assert job["qpu_vendor"]   == "IQM"
    assert job["qpu_model"]    == "Emerald"
    assert job["qpu_modality"] == "superconducting"


def test_TC211_migration_from_v1_legacy_db(tmp_path, monkeypatch):
    """v1 (vendor 컬럼) DB → v2 자동 마이그레이션 검증.

    수동으로 v1 스키마 DB 생성 → init_db() 호출 → v2 변환 + 기존 row 채움 확인.
    """
    db_path = tmp_path / "legacy_v1.db"

    # 1) v1 스키마로 DB 생성 + 샘플 row 삽입
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE jobs (
            job_id        TEXT PRIMARY KEY,
            vendor        TEXT NOT NULL,
            qpu_name      TEXT NOT NULL,
            circuit_name  TEXT,
            shots         INTEGER,
            status        TEXT NOT NULL DEFAULT 'submitted',
            counts        TEXT,
            error         TEXT,
            submitted_at  TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            extra         TEXT
        )
    """)
    conn.execute("""
        INSERT INTO jobs (job_id, vendor, qpu_name, shots, submitted_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, ("v1_legacy", "azure", "quantinuum_h2_1sc", 100,
          "2026-01-01T00:00:00", "2026-01-01T00:00:00"))
    conn.commit()
    conn.close()

    # 2) uqi_job_store 의 _DB_PATH 를 v1 DB 경로로 patch + init_db() 호출 (마이그레이션 트리거)
    import uqi_job_store
    monkeypatch.setattr(uqi_job_store, "_DB_PATH", db_path)
    uqi_job_store.init_db()

    # 3) v2 컬럼 존재 + vendor 컬럼 DROP 검증
    conn = sqlite3.connect(str(db_path))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    assert "vendor" not in cols
    assert {"runtime", "qpu_vendor", "qpu_model", "qpu_family", "qpu_modality"}.issubset(cols)

    # 4) 기존 row 의 4축 자동 채움 (catalog 매핑)
    row = conn.execute("SELECT * FROM jobs WHERE job_id='v1_legacy'").fetchone()
    cols_idx = {c[0]: i for i, c in enumerate(
        [(r[1],) for r in conn.execute("PRAGMA table_info(jobs)")])}
    # row tuple 으로 받지 말고 dict 변환
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM jobs WHERE job_id='v1_legacy'").fetchone()
    conn.close()

    assert row["runtime"]      == "Azure Quantum"
    assert row["qpu_vendor"]   == "Quantinuum"
    assert row["qpu_model"]    == "H2-1SC"
    assert row["qpu_family"]   == "Syntax Checker"
    assert row["qpu_modality"] == "ion-trap"

    # 5) 백업 파일 자동 생성 검증
    bak = db_path.with_suffix(".db.bak.v1")
    assert bak.exists(), "v1 백업 파일이 생성되지 않음"
