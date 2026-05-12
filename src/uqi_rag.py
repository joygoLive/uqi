# uqi_rag.py
# UQI 지식베이스 (RAG - Retrieval Augmented Generation)
# SQLite WAL + sqlite-vec (dense) + FTS5 (BM25) 하이브리드 + 외부 임베딩/리랭커 서버
# UQI (Universal Quantum Infrastructure)

import os
import json
import uuid
import sqlite3
import threading
from pathlib import Path

# sqlite-vec extension (벡터/하이브리드 검색). 로드 실패해도 SQLite 본체는 동작.
try:
    import sqlite_vec  # type: ignore
    _SQLITE_VEC_OK = True
except Exception as _e:  # pragma: no cover
    sqlite_vec = None
    _SQLITE_VEC_OK = False
    print(f"  [RAG] sqlite-vec import 실패 (벡터 백엔드 비활성): {_e}", flush=True)
from typing import Optional
from datetime import datetime


_DATA_DIR   = Path(__file__).parent.parent / "data"
# 환경 변수로 경로/컬렉션 이름 오버라이드 가능 (배포·마이그레이션 시 유용).
RAG_FILE    = os.environ.get("UQI_RAG_FILE",    str(_DATA_DIR / "uqi_rag.db"))
CACHE_FILE  = os.environ.get("UQI_CACHE_FILE",  str(_DATA_DIR / "uqi_cache.db"))

# Hybrid 검색 weighted RRF — 쿼리 의도별 dense/sparse 가중치.
# (dense, sparse) 튜플. env 로 override 가능.
def _parse_weight_pair(env: str, default: tuple[float, float]) -> tuple[float, float]:
    raw = os.environ.get(env, "").strip()
    if not raw or "," not in raw:
        return default
    try:
        a, b = raw.split(",", 1)
        return (float(a.strip()), float(b.strip()))
    except Exception:
        return default

_HYBRID_WEIGHTS = {
    "concept": _parse_weight_pair("UQI_HYBRID_W_CONCEPT", (0.7, 0.3)),
    "direct":  _parse_weight_pair("UQI_HYBRID_W_DIRECT",  (0.3, 0.7)),
    "mixed":   _parse_weight_pair("UQI_HYBRID_W_MIXED",   (0.5, 0.5)),
}


# RAG 백엔드 (sqlite-vec + 외부 임베딩/리랭커 서버).
EMBED_URL    = os.environ.get("UQI_EMBED_URL",    "http://127.0.0.1:7997")
EMBED_MODEL  = os.environ.get("UQI_EMBED_MODEL",  "BAAI/bge-m3")
EMBED_DIM    = int(os.environ.get("UQI_EMBED_DIM", "1024"))
RERANK_URL   = os.environ.get("UQI_RERANK_URL",   "http://127.0.0.1:7998")
RERANK_MODEL = os.environ.get("UQI_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
RERANK_TOPN  = int(os.environ.get("UQI_RERANK_TOPN", "50"))  # over-fetch before rerank

# 캐시 타입은 임베딩 제외 (값이 크고 검색 의미 없음)
_SKIP_EMBED_TYPES = {"cache", "qpu_availability"}

RECORD_TYPES = {
    "optimization":       "회로 최적화 결과 (엔진별 감소율)",
    "execution":          "QPU/시뮬 실행 이력 (비용/에러율/결과)",
    "calibration":        "캘리브레이션 스냅샷 및 변경 이력",
    "transpile_pattern":  "트랜스파일 패턴 (성공/실패/우회 방법)",
    "pipeline_issue":     "파이프라인 단계별 이슈 및 해결 방법",
    "conversion_pattern": "회로 변환 패턴 (QASM/QIR/네이티브)",
    "qpu_comparison":     "QPU 간 비교 실험 결과",
    "algorithm_mapping":  "알고리즘-도메인 매핑",
    "cost":               "실행 비용 이력",
    "qec_experiment":     "QEC 실험 결과 (인코딩 전후 Fidelity 비교)",
    "qpu_availability":   "QPU 가용성 이력 (온라인/오프라인/큐 상태)",
    "gpu_benchmark":      "CPU vs GPU 시뮬레이션 벤치마크 결과",
    "security_block":     "보안 정책 위반 차단 이력 (정적 분석)",
}


# ─────────────────────────────────────────────────────────
# DB 연결 헬퍼
# ─────────────────────────────────────────────────────────

def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    # sqlite-vec 확장 자동 로드 — vec0/FTS5 가상 테이블 쿼리에 필수.
    # Ubuntu/conda 파이썬 sqlite3 모듈은 기본적으로 load_extension 허용.
    if _SQLITE_VEC_OK:
        try:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
        except Exception as e:  # pragma: no cover
            # 로드 실패해도 일반 SQL 은 동작 — 벡터 검색만 비활성화됨
            print(f"  [RAG] sqlite-vec load skipped: {e}", flush=True)
        finally:
            try:
                conn.enable_load_extension(False)
            except Exception:
                pass
    return conn


def _init_rag_db(db_path: str):
    conn = _connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS records (
            id        TEXT PRIMARY KEY,
            type      TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            tags      TEXT NOT NULL,
            data      TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_type      ON records(type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON records(timestamp)")
    conn.commit()
    conn.close()


def _init_sqlite_vec_schema(db_path: str) -> bool:
    """records DB 에 record_vec (sqlite-vec) + record_fts (BM25) 가상 테이블 추가.

    원자적·멱등적. 확장 로드 실패 시 False 반환하고 호출부에서 빈 결과로 graceful degrade.
    """
    if not _SQLITE_VEC_OK:
        return False
    conn = _connect(db_path)
    try:
        conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS record_vec USING vec0("
            f"  record_id TEXT PRIMARY KEY,"
            f"  embedding FLOAT[{EMBED_DIM}]"
            f")"
        )
        # contentless FTS5 — record_id 는 검색 대상 아님(필터용), content 만 BM25 인덱싱
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS record_fts USING fts5("
            "  record_id UNINDEXED, content,"
            "  tokenize='unicode61 remove_diacritics 2'"
            ")"
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"  [RAG] sqlite-vec 스키마 생성 실패: {e}", flush=True)
        return False
    finally:
        conn.close()


def _init_cache_db(db_path: str):
    conn = _connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            key       TEXT PRIMARY KEY,
            value     TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def _row_to_record(row: sqlite3.Row) -> dict:
    return {
        "id":        row["id"],
        "type":      row["type"],
        "timestamp": row["timestamp"],
        "tags":      json.loads(row["tags"]),
        "data":      json.loads(row["data"]),
    }


# ─────────────────────────────────────────────────────────
# 임베딩 텍스트 생성 (v1: flatten key-value 형태, 테스트/베이스라인 비교용)
# 운영 경로에서는 _make_embedding_text_v2 (자연어 문장형) 가 사용된다.
# ─────────────────────────────────────────────────────────

def _make_embedding_text(record_type: str, data: dict) -> str:
    """레코드 타입별 임베딩용 텍스트 생성 (v1, 보존용)"""
    parts = [f"type:{record_type}"]

    if record_type == "optimization":
        parts += [
            f"qpu:{data.get('qpu_name','')}",
            f"circuit:{data.get('circuit_name','')}",
            f"combination:{data.get('combination','')}",
            f"engine:{data.get('opt_engine','')}",
            f"gate_reduction:{data.get('gate_reduction','')}",
            f"qubits:{data.get('num_qubits','')}",
        ]
    elif record_type == "execution":
        parts += [
            f"qpu:{data.get('qpu_name','')}",
            f"circuit:{data.get('circuit_name','')}",
            f"sdk:{data.get('sdk','')}",
            f"backend:{data.get('backend','')}",
            f"ok:{data.get('ok','')}",
            f"error_rate:{data.get('error_rate','')}",
        ]
    elif record_type == "noise_simulate":
        parts += [
            f"qpu:{data.get('qpu_name','')}",
            f"circuit:{data.get('circuit_name','')}",
            f"fidelity:{data.get('fidelity','')}",
            f"tvd:{data.get('tvd','')}",
        ]
    elif record_type == "qec_experiment":
        parts += [
            f"qpu:{data.get('qpu_name','')}",
            f"circuit:{data.get('circuit_name','')}",
            f"code:{data.get('code','')}",
            f"fidelity_before:{data.get('fidelity_before','')}",
            f"fidelity_after:{data.get('fidelity_after','')}",
            f"improvement:{data.get('improvement','')}",
            f"effective:{data.get('effective','')}",
        ]
    elif record_type == "gpu_benchmark":
        parts += [
            f"circuit:{data.get('circuit_name','')}",
            f"framework:{data.get('framework','')}",
            f"speedup:{data.get('speedup','')}",
            f"cpu_time:{data.get('cpu_time_sec','')}",
            f"gpu_time:{data.get('gpu_time_sec','')}",
            f"verdict:{data.get('verdict','')}",
        ]
    elif record_type == "security_block":
        parts += [
            "보안정책위반 security block 차단 정적분석",
            f"file:{data.get('file_name','')}",
            f"reason:{data.get('reason','')}",
            f"pattern:{data.get('pattern','')}",
            f"tool:{data.get('tool','')}",
        ]
    elif record_type == "pipeline_issue":
        parts += [
            f"stage:{data.get('stage','')}",
            f"sdk:{data.get('sdk','')}",
            f"issue:{data.get('issue','')}",
            f"solution:{data.get('solution','')}",
            f"severity:{data.get('severity','')}",
        ]
    elif record_type == "transpile_pattern":
        parts += [
            f"sdk:{data.get('sdk','')}",
            f"qpu:{data.get('qpu_name','')}",
            f"pattern:{data.get('pattern','')}",
            f"success:{data.get('success','')}",
            f"workaround:{data.get('workaround','')}",
        ]
    elif record_type == "conversion_pattern":
        parts += [
            f"from:{data.get('from_format','')}",
            f"to:{data.get('to_format','')}",
            f"sdk:{data.get('sdk','')}",
            f"success:{data.get('success','')}",
            f"description:{data.get('description','')}",
        ]
    else:
        # 나머지 타입은 data를 평탄화
        for k, v in data.items():
            if isinstance(v, (str, int, float, bool)) and v not in ('', None):
                parts.append(f"{k}:{v}")

    return " | ".join(str(p) for p in parts if p)


# ─────────────────────────────────────────────────────────
# 자연어 임베딩 텍스트 (bge-m3 / sqlite-vec backend 용)
# ─────────────────────────────────────────────────────────

def _fmt_pct(v) -> str:
    """0.42 → '42% 감소', None/0 → 빈 문자열."""
    try:
        if v is None: return ""
        f = float(v)
        if f == 0: return ""
        return f"{f*100:.1f}%"
    except (TypeError, ValueError):
        return ""


def _make_embedding_text_v2(record_type: str, data: dict) -> str:
    """bge-m3 가 잘 잡는 한·영 자연문 1~2 문장.

    설계 원칙:
      - schema 키(`qpu:`, `circuit:` 등) 토큰은 노이즈라 제거. 문장으로 풀어씀
      - 빈 값/None 은 절대 임베딩에 포함하지 않음 (의미 없는 토큰화 방지)
      - 한·영 혼재 — 사용자 쿼리 패턴과 일치
      - 핵심 속성 (qpu/sdk/result) 을 문장 앞쪽에 배치
      - 숫자는 percentage / fidelity 등 사람이 읽는 형식으로 변환
    """
    d = data or {}

    def _v(*keys):
        """비어있지 않은 첫 값."""
        for k in keys:
            v = d.get(k)
            if v not in (None, "", []):
                return v
        return None

    def _join(parts):
        return ". ".join(p for p in parts if p).strip()

    if record_type == "optimization":
        qpu     = _v("qpu_name")
        circuit = _v("circuit_name")
        combo   = _v("combination")
        qubits  = _v("num_qubits")
        red     = _fmt_pct(_v("gate_reduction"))
        depth   = _fmt_pct(_v("depth_reduction"))
        sent = []
        if circuit and qpu:
            sent.append(f"회로 {circuit} 를 {qpu} 에 맞춰 트랜스파일·최적화한 결과")
        elif qpu:
            sent.append(f"{qpu} 대상 회로 최적화 결과")
        if combo:
            sent.append(f"조합 {combo} 사용")
        if red:
            sent.append(f"게이트 수 {red} 감소")
        if depth:
            sent.append(f"depth {depth} 감소")
        if qubits:
            sent.append(f"{qubits} 큐빗 회로")
        return _join(sent) or f"optimization {circuit or '익명 회로'}"

    if record_type == "execution":
        qpu      = _v("qpu_name")
        circuit  = _v("circuit_name")
        sdk      = _v("sdk")
        backend  = _v("backend")
        ok       = d.get("ok")
        err_rate = d.get("error_rate")
        shots    = _v("shots")
        is_noise = isinstance(backend, str) and backend.startswith("noise_sim")
        sent = []
        if is_noise:
            sdk_n = backend.replace("noise_sim_", "") if isinstance(backend, str) else ""
            sent.append(f"회로 {circuit or ''} 를 {qpu or 'QPU'} 캘리브레이션 기반 노이즈 시뮬레이션 실행 ({sdk_n})")
            comp = (d.get("comparison") or {})
            fid = comp.get("fidelity")
            tvd = comp.get("tvd")
            if fid is not None:
                sent.append(f"fidelity {float(fid):.3f}")
            if tvd is not None:
                sent.append(f"TVD {float(tvd):.3f}")
        else:
            if circuit and qpu:
                sent.append(f"회로 {circuit} 를 실 QPU {qpu} 에서 실행")
            elif qpu:
                sent.append(f"실 QPU {qpu} 에서 작업 실행")
            if sdk:
                sent.append(f"SDK {sdk}")
            if shots is not None:
                sent.append(f"{shots} shots")
            if ok is False:
                sent.append("실패 / device error")
            elif ok is True:
                sent.append("정상 완료")
            if err_rate is not None:
                try:
                    f = float(err_rate)
                    if f > 0:
                        sent.append(f"error rate {f:.3f}")
                except (TypeError, ValueError):
                    pass
        return _join(sent) or "execution"

    if record_type == "qec_experiment":
        qpu     = _v("qpu_name")
        circuit = _v("circuit_name")
        code    = _v("code")
        f_b     = d.get("fidelity_before")
        f_a     = d.get("fidelity_after")
        eff     = d.get("effective")
        improv  = d.get("improvement")
        sent = []
        if circuit and qpu and code:
            sent.append(f"{qpu} 에서 회로 {circuit} 에 QEC code {code} 적용")
        elif code:
            sent.append(f"QEC code {code} 실험")
        if f_b is not None and f_a is not None:
            try:
                sent.append(f"fidelity {float(f_b):.3f} → {float(f_a):.3f}")
            except (TypeError, ValueError):
                pass
        if improv is not None:
            try:
                sent.append(f"{float(improv)*100:+.1f}% 개선")
            except (TypeError, ValueError):
                pass
        if eff is True:
            sent.append("QEC 효과 있음")
        elif eff is False:
            sent.append("QEC 효과 없음")
        return _join(sent) or "qec experiment"

    if record_type == "gpu_benchmark":
        circuit  = _v("circuit_name")
        fw       = _v("framework")
        speedup  = d.get("speedup")
        cpu_t    = d.get("cpu_time_sec")
        gpu_t    = d.get("gpu_time_sec")
        verdict  = _v("verdict")
        sent = []
        if circuit and fw:
            sent.append(f"{fw} 회로 {circuit} 의 CPU vs GPU 시뮬레이션 벤치마크")
        elif fw:
            sent.append(f"{fw} GPU 시뮬레이션 벤치마크")
        if speedup is not None:
            try:
                f = float(speedup)
                sent.append(f"GPU 가속비 {f:.2f}x" + (" — GPU 우세" if f > 1 else " — CPU 우세"))
            except (TypeError, ValueError):
                pass
        if cpu_t is not None and gpu_t is not None:
            try:
                sent.append(f"CPU {float(cpu_t):.2f}s vs GPU {float(gpu_t):.2f}s")
            except (TypeError, ValueError):
                pass
        if verdict:
            sent.append(str(verdict))
        return _join(sent) or "gpu benchmark"

    if record_type == "security_block":
        file_n  = _v("file_name", "algorithm_file")
        reason  = _v("reason")
        pattern = _v("pattern")
        tool    = _v("tool")
        line    = d.get("match_line") or d.get("match_lineno")
        sent = ["보안 정책 위반으로 정적 분석이 차단한 알고리즘 파일"]
        if file_n:
            sent.append(f"파일 {file_n}")
        if reason:
            sent.append(f"사유: {reason}")
        if pattern:
            sent.append(f"감지 패턴 `{pattern}`")
        if tool:
            sent.append(f"감지 도구 {tool}")
        if line:
            sent.append(f"라인 {line}")
        return _join(sent)

    if record_type == "pipeline_issue":
        stage    = _v("stage")
        sdk      = _v("sdk")
        qpu      = _v("qpu_name")
        issue    = _v("issue")
        solution = _v("solution")
        severity = _v("severity")
        sent = []
        head = []
        if stage:    head.append(f"파이프라인 단계 {stage}")
        if sdk:      head.append(f"SDK {sdk}")
        if qpu and qpu != "auto":
            head.append(f"QPU {qpu}")
        if head:
            sent.append(" / ".join(head) + " 에서 발생한 오류")
        if issue:
            sent.append(f"문제: {issue}")
        if solution:
            sent.append(f"해결: {solution}")
        if severity:
            sent.append(f"심각도 {severity}")
        return _join(sent) or "pipeline issue"

    if record_type == "transpile_pattern":
        sdk        = _v("sdk")
        qpu        = _v("qpu_name")
        success    = d.get("success")
        pattern    = _v("pattern")
        workaround = _v("workaround")
        desc       = _v("description")
        sent = []
        if sdk and qpu:
            sent.append(f"{sdk} 로 {qpu} 트랜스파일 시 패턴")
        if pattern:
            sent.append(f"패턴: {pattern}")
        if success is True:
            sent.append("성공")
        elif success is False:
            sent.append("실패")
        if workaround:
            sent.append(f"우회: {workaround}")
        if desc:
            sent.append(str(desc))
        return _join(sent) or "transpile pattern"

    if record_type == "conversion_pattern":
        ff      = _v("from_format")
        tf      = _v("to_format")
        sdk     = _v("sdk")
        success = d.get("success")
        desc    = _v("description") or _v("workaround")
        sent = []
        if ff and tf:
            sent.append(f"양자 회로 변환 {ff} → {tf}")
        if sdk:
            sent.append(f"SDK {sdk}")
        if success is True:
            sent.append("변환 성공")
        elif success is False:
            sent.append("변환 실패")
        if desc:
            sent.append(str(desc))
        return _join(sent) or "conversion pattern"

    # ── fallback: 키/값을 'key 값' 형식으로 평탄화 (콜론 토큰 제거) ──
    parts = [f"기록 타입 {record_type}"]
    for k, v in d.items():
        if isinstance(v, (str, int, float, bool)) and v not in ("", None):
            parts.append(f"{k} {v}")
    return ". ".join(parts)


# ─────────────────────────────────────────────────────────
# 메인 클래스
# ─────────────────────────────────────────────────────────

class UQIRAG:
    """
    UQI 지식베이스 — SQLite WAL + sqlite-vec (dense) + FTS5 (BM25) 하이브리드 검색

    - records DB (SQLite):        source of truth
    - cache DB (SQLite):          작업 결과 캐시
    - record_vec (sqlite-vec):    bge-m3 1024-dim dense 인덱스
    - record_fts (FTS5):          BM25 lexical 인덱스
    - 외부 임베딩/리랭커 서버:    bge-m3 + bge-reranker-v2-m3 (HTTP)
    """

    def __init__(self,
                 rag_file:   str = RAG_FILE,
                 cache_file: str = CACHE_FILE):
        self.rag_file   = rag_file
        self.cache_file = cache_file
        self._lock      = threading.Lock()

        _init_rag_db(self.rag_file)
        _init_cache_db(self.cache_file)
        # sqlite-vec 백엔드 (record_vec/record_fts). 실패 시 검색은 graceful degrade.
        self._sqlite_vec_ok = _init_sqlite_vec_schema(self.rag_file)
        if self._sqlite_vec_ok:
            print(f"  [RAG] sqlite-vec 활성 (dim={EMBED_DIM}, embed_url={EMBED_URL})", flush=True)
        else:
            print("  [RAG] sqlite-vec 비활성 — 시맨틱 검색 불가 (extension 미설치)", flush=True)

    # ─────────────────────────────────────────────────────
    # sqlite-vec 백엔드 — record_vec + record_fts write-through
    # ─────────────────────────────────────────────────────

    def _rerank(self,
                query:     str,
                records:   list,
                top_n:     int,
                timeout:   float = 15.0) -> list:
        """Cross-encoder 재랭킹. 실패 시 입력 그대로 반환 (graceful)."""
        if not records:
            return records
        # 임베딩 텍스트 v2 를 재랭커 입력으로 그대로 사용 — 임베딩과 동일한 표현이라
        # 일관성 ↑. 원본 record content 가 너무 길면 truncate.
        documents = []
        for r in records:
            text = _make_embedding_text_v2(r.get("type", ""), r.get("data") or {})
            documents.append(text[:2000])  # cross-encoder context 보호 (bge-reranker 8K 한계 충분)
        try:
            import requests
            resp = requests.post(
                f"{RERANK_URL.rstrip('/')}/rerank",
                json={"query": query, "documents": documents, "top_n": top_n,
                      "model": RERANK_MODEL},
                timeout=timeout,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
        except Exception as e:
            print(f"  [RAG] rerank 호출 실패 → 원본 순서 유지: {e}", flush=True)
            return records[:top_n]

        # results: [{"index": i, "score": s}, ...] — distance 작은 순(=관련성 높은 순) 정렬돼 있음
        out = []
        for r in results:
            idx = r.get("index")
            if idx is None or idx >= len(records):
                continue
            rec = dict(records[idx])
            rec["rerank_score"] = round(float(r.get("score", 0.0)), 4)
            out.append(rec)
            if len(out) >= top_n:
                break
        return out

    def _embed_text(self, text: str, timeout: float = 10.0) -> Optional[list]:
        """임베딩 서버 HTTP 호출. 실패 시 None — 호출부가 폴백 처리."""
        try:
            import requests  # 지연 import — 임베딩을 안 쓰는 단위 테스트 환경 보호
            r = requests.post(
                f"{EMBED_URL.rstrip('/')}/embeddings",
                json={"model": EMBED_MODEL, "input": text},
                timeout=timeout,
            )
            r.raise_for_status()
            return r.json()["data"][0]["embedding"]
        except Exception as e:
            print(f"  [RAG] embed 호출 실패 ({EMBED_URL}): {e}", flush=True)
            return None

    def _vec_add(self, record_id: str, record_type: str, data: dict, timestamp: str):
        """record_vec(임베딩) + record_fts(BM25) 양쪽에 write-through. 실패해도 SQLite 저장에 영향 없음."""
        if not self._sqlite_vec_ok or record_type in _SKIP_EMBED_TYPES:
            return
        if sqlite_vec is None:  # 안전망 — _sqlite_vec_ok와 일치
            return
        text = _make_embedding_text_v2(record_type, data)
        vec = self._embed_text(text)
        if vec is None:
            # 임베딩 서버 다운 — record_fts 라도 채워 BM25 검색은 살림
            try:
                with self._lock:
                    conn = _connect(self.rag_file)
                    try:
                        conn.execute(
                            "INSERT INTO record_fts(record_id, content) VALUES (?, ?)",
                            (record_id, text),
                        )
                        conn.commit()
                    finally:
                        conn.close()
            except Exception as e:
                print(f"  [RAG] fts-only insert 실패 {record_id}: {e}", flush=True)
            return
        try:
            with self._lock:
                conn = _connect(self.rag_file)
                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO record_vec(record_id, embedding) VALUES (?, ?)",
                        (record_id, sqlite_vec.serialize_float32(vec)),
                    )
                    conn.execute(
                        "INSERT INTO record_fts(record_id, content) VALUES (?, ?)",
                        (record_id, text),
                    )
                    conn.commit()
                finally:
                    conn.close()
        except Exception as e:
            print(f"  [RAG] vec_add 실패 {record_id}: {e}", flush=True)

    # ─────────────────────────────────────────────────────
    # 공통 저장 인터페이스
    # ─────────────────────────────────────────────────────

    def add(self, record_type: str, data: dict, tags: list = None) -> str:
        record_id = str(uuid.uuid4())[:8]
        timestamp = datetime.now().isoformat()
        tags_json = json.dumps(tags or [], ensure_ascii=False)
        data_json = json.dumps(data, default=str, ensure_ascii=False)

        with self._lock:
            conn = _connect(self.rag_file)
            try:
                conn.execute(
                    "INSERT INTO records (id, type, timestamp, tags, data) VALUES (?,?,?,?,?)",
                    (record_id, record_type, timestamp, tags_json, data_json)
                )
                conn.commit()
            finally:
                conn.close()

        # 임베딩 백엔드 write-through (락 밖에서 호출 — 자체 락/스레드세이프).
        self._vec_add(record_id, record_type, data, timestamp)
        return record_id

    # ─────────────────────────────────────────────────────
    # 캐시
    # ─────────────────────────────────────────────────────

    def get_cache(self, cache_key: str) -> Optional[str]:
        conn = _connect(self.cache_file)
        try:
            row = conn.execute(
                "SELECT value FROM cache WHERE key=?", (cache_key,)
            ).fetchone()
            return row["value"] if row else None
        except Exception:
            return None
        finally:
            conn.close()

    def set_cache(self, cache_key: str, value: str) -> str:
        timestamp = datetime.now().isoformat()
        with self._lock:
            conn = _connect(self.cache_file)
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO cache (key, value, timestamp) VALUES (?,?,?)",
                    (cache_key, value, timestamp)
                )
                conn.commit()
            finally:
                conn.close()
        return cache_key

    # ─────────────────────────────────────────────────────
    # 시맨틱 검색
    # ─────────────────────────────────────────────────────

    def search_semantic(self,
                        query:       str,
                        limit:       int  = 10,
                        record_type: str  = None,
                        rerank:      bool = True,
                        hybrid:      bool = True) -> list:
        """sqlite-vec 기반 다층 검색.

        파이프라인:
          1. (hybrid=True, 기본) dense + FTS5 BM25 → RRF 결합 (over-fetch ~50)
             (hybrid=False) dense top-N 만
          2. (rerank=True, 기본) bge-reranker-v2-m3 cross-encoder 로 top-limit 정밀화
             (rerank=False) RRF 또는 dense 순서 그대로 truncate

        각 외부 서버(임베딩/재랭커) 또는 sqlite-vec 다운 시 graceful degrade — 빈 결과 반환.
        """
        if not self._sqlite_vec_ok:
            return []
        try:
            if hybrid:
                candidates = self._search_hybrid(query, limit, record_type)
            else:
                fetch_n = max(limit * 5, RERANK_TOPN) if rerank else limit
                candidates = self._search_sqlite_vec(query, fetch_n, record_type)
            if rerank:
                return self._rerank(query, candidates, limit)
            return candidates[:limit]
        except Exception as e:
            print(f"  [RAG] sqlite-vec 파이프라인 실패: {e}", flush=True)
            return []

    def _search_bm25(self,
                     query:       str,
                     limit:       int,
                     record_type: Optional[str] = None) -> list:
        """FTS5 BM25 lexical 검색. 정확 어휘 / 약어 매칭에 강함.

        `record_fts` 는 contentless FTS5 (content='', UNINDEXED record_id).
        BM25 점수는 `rank` 컬럼으로 제공, 작은 값일수록 관련성 높음.
        """
        conn = _connect(self.rag_file)
        try:
            try:
                over = limit * 3 if record_type else limit
                rows = conn.execute(
                    "SELECT record_id, rank FROM record_fts "
                    "WHERE record_fts MATCH ? ORDER BY rank LIMIT ?",
                    (query, over),
                ).fetchall()
            except Exception:
                # FTS5 가 query 토큰화 못 할 때 — 예: 모든 토큰이 stopword
                return []
            if not rows:
                return []

            ids = [r[0] for r in rows]
            rank_map = {r[0]: r[1] for r in rows}

            placeholders = ",".join("?" * len(ids))
            sql = f"SELECT * FROM records WHERE id IN ({placeholders})"
            params: list = list(ids)
            if record_type:
                sql += " AND type = ?"
                params.append(record_type)
            recs = conn.execute(sql, params).fetchall()
        finally:
            conn.close()

        record_map = {row["id"]: _row_to_record(row) for row in recs}
        results = []
        for rid in ids:
            if rid not in record_map:
                continue
            rec = dict(record_map[rid])
            # rank 는 SQLite FTS5 의 음수 BM25 점수 (작을수록 관련↑). 표시는 절대값 normalized 로.
            rec["bm25_rank"] = float(rank_map[rid])
            results.append(rec)
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _rrf_fuse(rankings: list[list[str]], k: int = 60,
                  weights: Optional[list[float]] = None) -> list[tuple[str, float]]:
        """Weighted Reciprocal Rank Fusion — 각 검색기의 rank 기반 결합.

        score(d) = sum_i w_i / (k + rank_i(d))   (rank_i 1부터)
        weights 가 None 이면 균등 1.0 (기존 동작 호환). 길이가 rankings 와 다르면
        부족분 1.0 fallback. k=60 은 RRF 논문 기본값.
        반환: [(record_id, score), ...] score 내림차순.
        """
        if weights is None:
            weights = [1.0] * len(rankings)
        # 길이 불일치 시 부족분 1.0 보강 (defensive).
        while len(weights) < len(rankings):
            weights.append(1.0)
        scores: dict[str, float] = {}
        for w, ids in zip(weights, rankings):
            for rank_minus_1, rid in enumerate(ids):
                scores[rid] = scores.get(rid, 0.0) + float(w) / (k + rank_minus_1 + 1)
        return sorted(scores.items(), key=lambda kv: -kv[1])

    # ─────────────────────────────────────────────────────
    # 쿼리 intent 분류 (hybrid weight 결정용)
    # ─────────────────────────────────────────────────────
    # concept (자연어 추상) → dense 가중 ↑ — 임베딩 의미 검색 강점
    # direct  (키워드/식별자)  → BM25 가중 ↑ — 어휘 정확 매칭 강점
    # mixed                  → 균등
    @staticmethod
    def _classify_query_intent(query: str) -> str:
        q = (query or "").strip()
        if not q:
            return "mixed"
        # 한글 비율
        hangul = sum(1 for c in q if "가" <= c <= "힣")
        nonspace = sum(1 for c in q if not c.isspace())
        hangul_ratio = (hangul / nonspace) if nonspace else 0.0
        words = q.split()
        # 질문 키워드 — concept 강한 signal
        question_kw = ("어떻게", "왜", "무엇", "어디", "언제", "누구",
                       "what", "why", "how", "where", "when", "who")
        has_question = "?" in q or any(kw in q.lower() for kw in question_kw)

        if has_question or len(words) >= 4 or hangul_ratio > 0.5:
            return "concept"
        if len(words) <= 3 and hangul_ratio < 0.2:
            return "direct"
        return "mixed"

    def _search_hybrid(self,
                       query:       str,
                       limit:       int,
                       record_type: Optional[str]) -> list:
        """dense (sqlite-vec) + sparse (FTS5 BM25) RRF 결합.

        각 검색기 top-N (=limit*3 또는 RERANK_TOPN) → RRF → top-limit.
        한쪽이 빈 결과여도 다른 쪽이 결과 제공 (자동 degrade).
        """
        over = max(limit * 3, RERANK_TOPN)
        dense  = []
        sparse = []
        try:
            dense = self._search_sqlite_vec(query, over, record_type)
        except Exception as e:
            print(f"  [RAG] hybrid: dense 실패: {e}", flush=True)
        try:
            sparse = self._search_bm25(query, over, record_type)
        except Exception as e:
            print(f"  [RAG] hybrid: bm25 실패: {e}", flush=True)

        if not dense and not sparse:
            return []

        # 쿼리 의도 분류 → dense/sparse weight 적용 (weighted RRF).
        intent = self._classify_query_intent(query)
        w_dense, w_sparse = _HYBRID_WEIGHTS.get(intent, _HYBRID_WEIGHTS["mixed"])

        fused = self._rrf_fuse(
            [
                [r["id"] for r in dense],
                [r["id"] for r in sparse],
            ],
            weights=[w_dense, w_sparse],
        )

        # 원본 record 데이터 join + RRF score + intent 부착 (디버깅/평가용)
        record_map: dict[str, dict] = {}
        for r in dense + sparse:
            record_map.setdefault(r["id"], r)

        out = []
        for rid, score in fused[: max(limit, over)]:
            if rid not in record_map:
                continue
            rec = dict(record_map[rid])
            rec["rrf_score"]   = round(score, 6)
            rec["rrf_intent"]  = intent
            out.append(rec)
            if len(out) >= over:
                break
        return out

    def _search_sqlite_vec(self,
                           query:       str,
                           limit:       int,
                           record_type: Optional[str]) -> list:
        """sqlite-vec record_vec 코사인 유사도 top-k 검색."""
        vec = self._embed_text(query)
        if vec is None:
            raise RuntimeError("embedding 서버 응답 없음")
        if sqlite_vec is None:
            raise RuntimeError("sqlite_vec 모듈 미로드")

        conn = _connect(self.rag_file)
        try:
            # vec0 KNN: WHERE embedding MATCH ? AND k = ?
            # 결과는 distance ASC (가까운 순). over-fetch 후 type 필터 적용.
            over = limit * 3 if record_type else limit
            rows = conn.execute(
                "SELECT record_id, distance FROM record_vec "
                "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (sqlite_vec.serialize_float32(vec), over),
            ).fetchall()
            if not rows:
                return []

            ids = [r[0] for r in rows]
            dist_map = {r[0]: r[1] for r in rows}

            placeholders = ",".join("?" * len(ids))
            sql = f"SELECT * FROM records WHERE id IN ({placeholders})"
            params: list = list(ids)
            if record_type:
                sql += " AND type = ?"
                params.append(record_type)
            recs = conn.execute(sql, params).fetchall()
        finally:
            conn.close()

        record_map = {row["id"]: _row_to_record(row) for row in recs}
        results = []
        for rid in ids:  # distance ASC 순 유지
            if rid not in record_map:
                continue
            rec = dict(record_map[rid])
            dist = dist_map[rid]
            # bge-m3 출력은 normalize 됐고 vec0 기본 L2 distance → 0~2 범위.
            # 0~1 친화적 표시로 1 - distance/2 변환 (cosine 근사).
            try:
                rec["similarity"] = round(max(0.0, 1.0 - float(dist) / 2.0), 4)
            except (TypeError, ValueError):
                rec["similarity"] = 0.0
            results.append(rec)
            if len(results) >= limit:
                break
        return results

    # ─────────────────────────────────────────────────────
    # 타입별 저장 헬퍼 (기존과 동일)
    # ─────────────────────────────────────────────────────

    def add_optimization(self, metadata: dict) -> str:
        tags = [
            metadata.get("qpu_name", ""),
            metadata.get("combination", ""),
            metadata.get("opt_engine", ""),
            f"q{metadata.get('num_qubits', '')}",
        ]
        return self.add("optimization", metadata, tags=tags)

    def add_execution(self, circuit_name, qpu_name, backend, shots, counts, ok,
                      error_rate=None, queue_time_sec=None, exec_time_sec=None,
                      cost=None, calibration_snapshot=None, sdk=None, extra=None):
        data = {
            "circuit_name": circuit_name, "qpu_name": qpu_name,
            "backend": backend, "shots": shots, "counts": counts, "ok": ok,
            "error_rate": error_rate, "queue_time_sec": queue_time_sec,
            "exec_time_sec": exec_time_sec, "cost": cost,
            "calibration_snapshot": calibration_snapshot, "sdk": sdk,
        }
        if extra: data.update(extra)
        return self.add("execution", data, tags=[qpu_name, backend, circuit_name, sdk or ""])

    def add_calibration(self, qpu_name, calibration):
        return self.add("calibration", {"qpu_name": qpu_name, "calibration": calibration}, tags=[qpu_name])

    def add_transpile_pattern(self, sdk, qpu_name, pattern, success,
                               workaround=None, description=None, extra=None):
        data = {"sdk": sdk, "qpu_name": qpu_name, "pattern": pattern,
                "success": success, "workaround": workaround, "description": description}
        if extra: data.update(extra)
        return self.add("transpile_pattern", data, tags=[sdk, qpu_name, "success" if success else "failure"])

    def add_pipeline_issue(self, stage, sdk, issue, solution,
                            qpu_name=None, severity="warn", extra=None):
        data = {"stage": stage, "sdk": sdk, "qpu_name": qpu_name,
                "issue": issue, "solution": solution, "severity": severity}
        if extra: data.update(extra)
        return self.add("pipeline_issue", data, tags=[stage, sdk, severity, qpu_name or ""])

    def add_conversion_pattern(self, from_format, to_format, sdk, success,
                                workaround=None, description=None, extra=None):
        data = {"from_format": from_format, "to_format": to_format, "sdk": sdk,
                "success": success, "workaround": workaround, "description": description}
        if extra: data.update(extra)
        return self.add("conversion_pattern", data, tags=[from_format, to_format, sdk, "success" if success else "failure"])

    def add_qpu_comparison(self, circuit_name, results, winner=None,
                            metric="gate_reduction", extra=None):
        data = {"circuit_name": circuit_name, "results": results, "winner": winner, "metric": metric}
        if extra: data.update(extra)
        return self.add("qpu_comparison", data, tags=list(results.keys()) + [circuit_name, metric])

    def add_cost(self, circuit_name, qpu_name, backend, shots,
                 estimated=None, actual=None, currency="credits", queue_sec=None, extra=None):
        data = {"circuit_name": circuit_name, "qpu_name": qpu_name, "backend": backend,
                "shots": shots, "estimated": estimated, "actual": actual,
                "currency": currency, "queue_sec": queue_sec}
        if extra: data.update(extra)
        return self.add("cost", data, tags=[qpu_name, backend, circuit_name])

    def add_qec_experiment(self, circuit_name, qpu_name, code, before, after,
                            improvement, overhead, extra=None):
        data = {
            "circuit_name": circuit_name, "qpu_name": qpu_name, "code": code,
            "fidelity_before": before.get("fidelity"), "fidelity_after": after.get("fidelity"),
            "tvd_before": before.get("tvd"), "tvd_after": after.get("tvd"),
            "improvement": improvement, "qubit_overhead": overhead.get("qubit_overhead"),
            "gate_overhead": overhead.get("gate_overhead"), "orig_qubits": overhead.get("orig_qubits"),
            "effective": bool(improvement > 0),
        }
        if extra: data.update(extra)
        return self.add("qec_experiment", data, tags=[qpu_name, code, "effective" if improvement > 0 else "ineffective"])

    def add_qpu_availability(self, available, status):
        data = {"available": available, "status": status, "timestamp": datetime.now().isoformat()}
        return self.add("qpu_availability", data, tags=available + ["qpu_availability"])

    def add_gpu_benchmark(self, circuit_name, framework, gpu_available, gpu_accelerated,
                           cpu_time_sec, cpu_status, gpu_time_sec=None, gpu_status=None,
                           speedup=None, verdict=None, cpu_error=None, gpu_error=None):
        data = {
            "circuit_name": circuit_name, "framework": framework,
            "gpu_available": gpu_available, "gpu_accelerated": gpu_accelerated,
            "cpu_time_sec": cpu_time_sec, "cpu_status": cpu_status, "cpu_error": cpu_error,
            "gpu_time_sec": gpu_time_sec, "gpu_status": gpu_status, "gpu_error": gpu_error,
            "speedup": speedup, "verdict": verdict,
            "ok": cpu_status == "completed" and gpu_status == "completed",
        }
        return self.add("gpu_benchmark", data, tags=[
            circuit_name, framework,
            "gpu_available" if gpu_available else "gpu_unavailable",
            "success" if data["ok"] else "failure",
        ])

    def add_security_block(self, algorithm_file, reason, pattern, tool=None,
                            match_lineno=None, match_line=None):
        data = {
            "algorithm_file": algorithm_file,
            "file_abspath": str(Path(algorithm_file).resolve()) if Path(algorithm_file).exists() else algorithm_file,
            "file_name": Path(algorithm_file).name,
            "reason": reason, "pattern": pattern, "tool": tool,
        }
        if match_lineno is not None:
            data["match_lineno"] = match_lineno
        if match_line is not None:
            data["match_line"] = match_line
        return self.add("security_block", data, tags=["security_block", Path(algorithm_file).name, pattern])

    # ─────────────────────────────────────────────────────
    # 정형 검색
    # ─────────────────────────────────────────────────────

    def search(self, record_type=None, tags=None, filters=None, limit=20):
        conn = _connect(self.rag_file)
        try:
            if record_type:
                rows = conn.execute(
                    "SELECT * FROM records WHERE type=? ORDER BY timestamp DESC LIMIT ?",
                    (record_type, limit * 10)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM records ORDER BY timestamp DESC LIMIT ?",
                    (limit * 10,)
                ).fetchall()
        finally:
            conn.close()

        results = []
        for row in rows:
            record = _row_to_record(row)
            if tags and not any(t in record["tags"] for t in tags):
                continue
            if filters:
                data = record["data"]
                if not all(str(data.get(k)).lower() == str(v).lower() for k, v in filters.items()):
                    continue
            results.append(record)
            if len(results) >= limit:
                break
        return results

    def search_noise_simulation(self, qpu_name: str = "", limit: int = 20) -> list:
        """SQL push-down version of noise-simulation lookup.

        `_qpu_submit_*` writes one `execution` record per shot batch, with
        `backend` LIKE 'noise_sim_<sdk>' when running the calibration-based
        simulator. Previously mcp_server filtered these in Python after
        fetching `limit * 10` rows; with growing history that wastes JSON
        decoding and risks dropping legitimate hits. Push the filter into
        SQLite using `json_extract` so we read exactly `limit` rows.
        """
        conn = _connect(self.rag_file)
        try:
            if qpu_name:
                sql = (
                    "SELECT * FROM records "
                    "WHERE type='execution' "
                    "  AND json_extract(data,'$.backend') LIKE 'noise_sim_%' "
                    "  AND json_extract(data,'$.qpu_name') = ? "
                    "ORDER BY timestamp DESC LIMIT ?"
                )
                rows = conn.execute(sql, (qpu_name, limit)).fetchall()
            else:
                sql = (
                    "SELECT * FROM records "
                    "WHERE type='execution' "
                    "  AND json_extract(data,'$.backend') LIKE 'noise_sim_%' "
                    "ORDER BY timestamp DESC LIMIT ?"
                )
                rows = conn.execute(sql, (limit,)).fetchall()
        finally:
            conn.close()
        return [_row_to_record(r) for r in rows]

    def search_best_combination(self, num_qubits=0, qpu_name="", limit=20):
        filters = {"qpu_name": qpu_name} if qpu_name else {}
        candidates = self.search(record_type="optimization", filters=filters, limit=500)
        candidates = [r["data"] for r in candidates if r["data"].get("ok") is True]
        candidates = [d for d in candidates if not (
            d.get("opt1_gates") == 0 or
            d.get("equivalent") is False or
            (d.get("gate_reduction") == 1.0 and d.get("equivalent") is None)
        )]
        if num_qubits > 0:
            candidates = [d for d in candidates if d.get("num_qubits", 0) <= num_qubits]
        candidates.sort(key=lambda d: d.get("gate_reduction", 0), reverse=True)
        return candidates[:limit]

    def search_suspicious_optimizations(self, qpu_name="", limit=50):
        filters = {"qpu_name": qpu_name} if qpu_name else {}
        candidates = self.search(record_type="optimization", filters=filters, limit=500)
        results = []
        for r in candidates:
            d = r["data"]
            reasons = []
            if d.get("opt1_gates") == 0:
                reasons.append("empty_circuit")
            if d.get("equivalent") is False:
                reasons.append("not_equivalent")
            if d.get("gate_reduction") == 1.0 and d.get("equivalent") is None:
                reasons.append("unverified_full_reduction")
            if reasons:
                results.append({**d, "_suspicious_reasons": reasons})
        results.sort(key=lambda d: len(d["_suspicious_reasons"]), reverse=True)
        return results[:limit]

    def search_pipeline_issues(self, stage=None, sdk=None):
        filters = {}
        if stage: filters["stage"] = stage
        if sdk:   filters["sdk"]   = sdk
        return self.search(record_type="pipeline_issue", filters=filters if filters else None)

    def search_transpile_patterns(self, sdk=None, success=None):
        filters = {}
        if sdk:            filters["sdk"]     = sdk
        if success is not None: filters["success"] = success
        return self.search(record_type="transpile_pattern", filters=filters if filters else None)

    def search_qec_results(self, qpu_name=None, code=None, effective=None):
        filters = {}
        if qpu_name:          filters["qpu_name"]  = qpu_name
        if code:              filters["code"]       = code
        if effective is not None: filters["effective"] = effective
        return self.search(record_type="qec_experiment", filters=filters if filters else None)

    # ─────────────────────────────────────────────────────
    # 통계
    # ─────────────────────────────────────────────────────

    def stats(self) -> dict:
        conn = _connect(self.rag_file)
        try:
            total    = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            rows     = conn.execute("SELECT type, COUNT(*) as cnt FROM records GROUP BY type").fetchall()
            by_type  = {row["type"]: row["cnt"] for row in rows}
            last_row = conn.execute("SELECT timestamp FROM records ORDER BY timestamp DESC LIMIT 1").fetchone()
            last_updated = last_row["timestamp"] if last_row else None
            conn_c = _connect(self.cache_file)
            try:
                cache_count = conn_c.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
            finally:
                conn_c.close()
            by_type["cache"] = cache_count

            # sqlite-vec / FTS5 인덱스 카운트 (확장 활성 시).
            vec_count = 0
            fts_count = 0
            vec_health = "disabled"
            vec_error = None
            if self._sqlite_vec_ok:
                try:
                    vec_count = conn.execute("SELECT COUNT(*) FROM record_vec").fetchone()[0]
                    fts_count = conn.execute("SELECT COUNT(*) FROM record_fts").fetchone()[0]
                    vec_health = "active" if vec_count > 0 else "empty"
                except Exception as e:
                    vec_health = "error"
                    vec_error = str(e)
        finally:
            conn.close()

        return {
            "total":         total + cache_count,
            "by_type":       by_type,
            "rag_file":      self.rag_file,
            "cache_file":    self.cache_file,
            "vec_index":     vec_count,
            "fts_index":     fts_count,
            "vec_health":    vec_health,
            "vec_error":     vec_error,
            "embed_url":     EMBED_URL,
            "embed_model":   EMBED_MODEL,
            "rerank_url":    RERANK_URL,
            "rerank_model":  RERANK_MODEL,
            "last_updated":  last_updated,
        }

    def print_stats(self):
        s = self.stats()
        print(f"\n[RAG] 지식베이스 통계")
        print(f"  총 레코드: {s['total']}")
        print(f"  이력 DB:   {s['rag_file']}")
        print(f"  캐시 DB:   {s['cache_file']}")
        print(f"  sqlite-vec: {s['vec_health']} (dense {s['vec_index']}건 / fts {s['fts_index']}건)")
        print(f"  최근 업데이트: {s['last_updated']}")
        print(f"  타입별:")
        for t, c in sorted(s["by_type"].items()):
            desc = RECORD_TYPES.get(t, "캐시")
            print(f"    {t:<25} {c:>4}건  {desc}")

    def print_recent(self, n=5, record_type=None):
        records = self.search(record_type=record_type, limit=n)
        print(f"\n[RAG] 최근 {len(records)}건")
        for r in records:
            print(f"  [{r['id']}] {r['type']:<25} {r['timestamp'][:19]}")