# uqi_job_store.py
# QPU 비동기 job 관리 — SQLite 기반 통합 job store
# IBM / IQM / Braket / Azure / Quandela 통합 추적
#
# 테이블: jobs (Phase 2 — 4축 catalog 통합)
#   job_id        TEXT PRIMARY KEY
#   runtime       TEXT   실 실행/청구 클라우드 (IBM Quantum / IQM Resonance /
#                        AWS Braket / Azure Quantum / Quandela Cloud / ...)
#   qpu_vendor    TEXT   제조사 (IBM / IQM / IonQ / Rigetti / Pasqal / ...)
#   qpu_model     TEXT   모델명 (Fez / Emerald / Forte-1 / ...)
#   qpu_family    TEXT   마이크로아키텍처 (Heron R2 / sim / ...) — nullable
#   qpu_modality  TEXT   큐비트 구현 (superconducting / ion-trap / neutral-atom / photonic)
#   qpu_name      TEXT   raw qpu_id (ibm_fez / qpu:ascella / sim:ascella) — backward compat
#   circuit_name  TEXT
#   shots         INTEGER
#   status        TEXT   (submitted | running | done | error | cancelled)
#   counts        TEXT   (JSON)
#   error         TEXT
#   submitted_at  TEXT   (ISO8601)
#   updated_at    TEXT   (ISO8601)
#   extra         TEXT   (JSON — vendor 별 메타)
#
# 마이그레이션: 기존 vendor 컬럼 (게이트웨이 의미) → runtime 으로 의미 명시 + 신규 4축 컬럼.
# init_db() 가 멱등 자동 마이그레이션 처리. 첫 마이그레이션 시 *.bak 백업 자동 생성.

import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_DB_PATH = Path(__file__).parent.parent / "data" / "uqi_jobs.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _existing_columns(conn: sqlite3.Connection) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}


def _migrate_to_v2(conn: sqlite3.Connection) -> None:
    """기존 v1 스키마 (vendor 컬럼) → v2 (runtime + 4축 catalog 컬럼) 자동 마이그레이션.

    멱등 — 이미 v2 면 no-op.

    1) 신규 컬럼 추가 (없으면)
    2) qpu_name → catalog 매핑으로 신규 컬럼 채우기 (NULL 인 row 만)
    3) 옛 vendor 컬럼 DROP (이미 없으면 skip)
    """
    cols = _existing_columns(conn)
    new_cols = [
        ("runtime",      "TEXT"),
        ("qpu_vendor",   "TEXT"),
        ("qpu_model",    "TEXT"),
        ("qpu_family",   "TEXT"),
        ("qpu_modality", "TEXT"),
    ]
    needs_migration = any(c not in cols for c, _ in new_cols) or "vendor" in cols
    if not needs_migration:
        return

    # 자동 백업 (첫 마이그레이션 시 1회)
    if _DB_PATH.exists():
        bak = _DB_PATH.with_suffix(".db.bak.v1")
        if not bak.exists():
            try:
                shutil.copy2(_DB_PATH, bak)
            except Exception:
                pass  # 백업 실패해도 마이그레이션 자체는 진행

    # 1) 컬럼 추가
    for col, typ in new_cols:
        if col not in cols:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typ}")

    # 2) 기존 row 채우기 (qpu_name → catalog 매핑)
    #    parse_qpu_full 은 catalog 매핑 + 휴리스틱 fallback 모두 처리
    from uqi_pricing import parse_qpu_full
    rows = conn.execute("SELECT job_id, qpu_name FROM jobs WHERE qpu_vendor IS NULL").fetchall()
    for row in rows:
        meta = parse_qpu_full(row[1] or "")
        conn.execute("""
            UPDATE jobs
            SET runtime=?, qpu_vendor=?, qpu_model=?, qpu_family=?, qpu_modality=?
            WHERE job_id=?
        """, (
            meta["runtime"], meta["vendor"], meta["model"],
            meta.get("family"), meta["modality"], row[0],
        ))

    # 3) 옛 vendor 컬럼 DROP (SQLite 3.35+)
    cols = _existing_columns(conn)
    if "vendor" in cols:
        conn.execute("ALTER TABLE jobs DROP COLUMN vendor")

    conn.commit()


def init_db():
    with _connect() as conn:
        # 1) 신규 DB: v2 스키마로 바로 생성
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id        TEXT PRIMARY KEY,
                runtime       TEXT,
                qpu_vendor    TEXT,
                qpu_model     TEXT,
                qpu_family    TEXT,
                qpu_modality  TEXT,
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
        conn.commit()
        # 2) 기존 v1 DB: 자동 마이그레이션 (멱등)
        _migrate_to_v2(conn)


def save_job(
    job_id:       str,
    qpu_name:     str,
    circuit_name: str = "",
    shots:        int = 0,
    extra:        dict = None,
    runtime:      str = None,
    qpu_vendor:   str = None,
    qpu_model:    str = None,
    qpu_family:   str = None,
    qpu_modality: str = None,
) -> None:
    """신규 job 저장 (submitted 상태).

    runtime / qpu_vendor / qpu_model / qpu_family / qpu_modality 는 미지정 시
    qpu_name 을 catalog 매핑으로 자동 추출 (호출 측 편의).
    """
    if any(v is None for v in (runtime, qpu_vendor, qpu_model, qpu_modality)):
        from uqi_pricing import parse_qpu_full
        meta = parse_qpu_full(qpu_name)
        runtime      = runtime      or meta["runtime"]
        qpu_vendor   = qpu_vendor   or meta["vendor"]
        qpu_model    = qpu_model    or meta["model"]
        qpu_family   = qpu_family   if qpu_family is not None else meta.get("family")
        qpu_modality = qpu_modality or meta["modality"]

    now = _now()
    with _connect() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO jobs
                (job_id, runtime, qpu_vendor, qpu_model, qpu_family, qpu_modality,
                 qpu_name, circuit_name, shots,
                 status, submitted_at, updated_at, extra)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'submitted', ?, ?, ?)
        """, (
            job_id, runtime, qpu_vendor, qpu_model, qpu_family, qpu_modality,
            qpu_name, circuit_name, shots,
            now, now,
            json.dumps(extra or {}, ensure_ascii=False),
        ))
        conn.commit()


def update_job(
    job_id: str,
    status: str,
    counts: dict = None,
    error:  str  = None,
) -> None:
    """job 상태/결과 업데이트"""
    with _connect() as conn:
        conn.execute("""
            UPDATE jobs
            SET status=?, counts=?, error=?, updated_at=?
            WHERE job_id=?
        """, (
            status,
            json.dumps(counts, ensure_ascii=False) if counts is not None else None,
            error,
            _now(),
            job_id,
        ))
        conn.commit()


def update_job_extra(job_id: str, extra: dict) -> None:
    """extra 필드만 update. status/counts/updated_at 변경하지 않음.

    캐시(예: vendor API timing 결과) 저장용 — updated_at 갱신 시 wall-clock
    duration 계산이 망가지므로 별도 메서드로 분리.
    """
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET extra=? WHERE job_id=?",
            (json.dumps(extra or {}, ensure_ascii=False), job_id),
        )
        conn.commit()


def get_job(job_id: str) -> dict | None:
    """job_id로 단일 job 조회. 없으면 None"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE job_id=?", (job_id,)
        ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["counts"] = json.loads(d["counts"]) if d.get("counts") else None
    d["extra"]  = json.loads(d["extra"])  if d.get("extra")  else {}
    return d


def list_pending_jobs(runtime: str = None) -> list[dict]:
    """status가 submitted/running 인 job 목록.

    runtime 인자 (예: 'AWS Braket', 'IBM Quantum') 로 특정 cloud 만 필터 가능.
    """
    with _connect() as conn:
        if runtime:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status IN ('submitted','running') AND runtime=?"
                " ORDER BY submitted_at DESC",
                (runtime,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status IN ('submitted','running')"
                " ORDER BY submitted_at DESC"
            ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["counts"] = json.loads(d["counts"]) if d.get("counts") else None
        d["extra"]  = json.loads(d["extra"])  if d.get("extra")  else {}
        result.append(d)
    return result


def list_jobs(limit: int = 20) -> list[dict]:
    """최근 job 목록 (최신순)"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY submitted_at DESC LIMIT ?", (limit,)
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["counts"] = json.loads(d["counts"]) if d.get("counts") else None
        d["extra"]  = json.loads(d["extra"])  if d.get("extra")  else {}
        result.append(d)
    return result


def cancel_job(job_id: str) -> bool:
    """job을 cancelled 상태로 마킹. 이미 done/cancelled면 False 반환.
    error 상태는 로컬 버그 오기록일 수 있으므로 취소 허용."""
    job = get_job(job_id)
    if job is None:
        return False
    if job["status"] in ("done", "cancelled"):
        return False
    update_job(job_id, status="cancelled")
    return True


# 모듈 import 시 자동 초기화 (+ 멱등 자동 마이그레이션)
init_db()
