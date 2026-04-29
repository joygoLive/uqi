# uqi_job_store.py
# QPU 비동기 job 관리 — SQLite 기반 통합 job store
# IBM / IQM / 기타 벤더 job_id 통합 추적
#
# 테이블: jobs
#   job_id        TEXT PRIMARY KEY
#   vendor        TEXT   (ibm | iqm | perceval | ...)
#   qpu_name      TEXT
#   circuit_name  TEXT
#   shots         INTEGER
#   status        TEXT   (submitted | running | done | error | cancelled)
#   counts        TEXT   (JSON)
#   error         TEXT
#   submitted_at  TEXT   (ISO8601)
#   updated_at    TEXT   (ISO8601)
#   extra         TEXT   (JSON — via, backend_url 등 벤더별 메타)

import json
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


def init_db():
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
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
        conn.commit()


def save_job(
    job_id:       str,
    vendor:       str,
    qpu_name:     str,
    circuit_name: str = "",
    shots:        int = 0,
    extra:        dict = None,
) -> None:
    """신규 job 저장 (submitted 상태)"""
    now = _now()
    with _connect() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO jobs
                (job_id, vendor, qpu_name, circuit_name, shots,
                 status, submitted_at, updated_at, extra)
            VALUES (?, ?, ?, ?, ?, 'submitted', ?, ?, ?)
        """, (
            job_id, vendor, qpu_name, circuit_name, shots,
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


def list_pending_jobs(vendor: str = None) -> list[dict]:
    """status가 submitted/running 인 job 목록"""
    with _connect() as conn:
        if vendor:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status IN ('submitted','running') AND vendor=?"
                " ORDER BY submitted_at DESC",
                (vendor,)
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


# 모듈 import 시 자동 초기화
init_db()
