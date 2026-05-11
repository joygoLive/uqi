# uqi_rag.py
# UQI 지식베이스 (RAG - Retrieval Augmented Generation)
# SQLite WAL 기반 + ChromaDB 시맨틱 검색
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
CHROMA_DIR  = os.environ.get("UQI_CHROMA_DIR",  str(_DATA_DIR / "uqi_chroma"))
CHROMA_NAME = os.environ.get("UQI_CHROMA_COLLECTION", "uqi_knowledge")

# 새 RAG 백엔드 (sqlite-vec). chroma 와 병행 운영 후 Phase 8 에서 chroma 제거.
EMBED_URL   = os.environ.get("UQI_EMBED_URL",   "http://127.0.0.1:7997")
EMBED_MODEL = os.environ.get("UQI_EMBED_MODEL", "BAAI/bge-m3")
EMBED_DIM   = int(os.environ.get("UQI_EMBED_DIM", "1024"))

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

    원자적·멱등적. 확장 로드 실패 시 False 반환하고 호출부에서 chroma fallback.
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
# Chroma 임베딩 텍스트 생성
# ─────────────────────────────────────────────────────────

def _make_embedding_text(record_type: str, data: dict) -> str:
    """레코드 타입별 임베딩용 텍스트 생성"""
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
    UQI 지식베이스 — SQLite WAL + ChromaDB 시맨틱 검색

    - records DB (SQLite): source of truth
    - cache DB (SQLite):   작업 결과 캐시
    - Chroma:              시맨틱 검색 인덱스 (SQLite 보조)
    """

    def __init__(self,
                 rag_file:   str = RAG_FILE,
                 cache_file: str = CACHE_FILE,
                 chroma_dir: str = CHROMA_DIR):
        self.rag_file   = rag_file
        self.cache_file = cache_file
        self.chroma_dir = chroma_dir
        self._lock      = threading.Lock()
        self._chroma    = None

        _init_rag_db(self.rag_file)
        _init_cache_db(self.cache_file)
        # sqlite-vec 백엔드 (record_vec/record_fts). 실패해도 chroma 로 동작.
        self._sqlite_vec_ok = _init_sqlite_vec_schema(self.rag_file)
        if self._sqlite_vec_ok:
            print(f"  [RAG] sqlite-vec 활성 (dim={EMBED_DIM}, embed_url={EMBED_URL})", flush=True)
        self._init_chroma()

    def _init_chroma(self):
        """Chroma 컬렉션 초기화. 실패해도 서버 시작 차단 안 함."""
        try:
            import chromadb
            from chromadb.utils import embedding_functions
            client = chromadb.PersistentClient(path=self.chroma_dir)
            ef = embedding_functions.DefaultEmbeddingFunction()  # all-MiniLM-L6-v2
            self._chroma = client.get_or_create_collection(
                name=CHROMA_NAME,
                embedding_function=ef,
                metadata={"hnsw:space": "cosine"},
            )
            print(f"  [RAG] Chroma 초기화 완료: {self.chroma_dir} "
                  f"(인덱스 {self._chroma.count()}개)", flush=True)
        except Exception as e:
            print(f"  [RAG] Chroma 비활성화 (chromadb 미설치 또는 오류: {e})", flush=True)
            self._chroma = None

    def _chroma_add(self, record_id: str, record_type: str, data: dict, timestamp: str):
        """Chroma에 임베딩 추가. 실패해도 SQLite 저장에 영향 없음."""
        if self._chroma is None or record_type in _SKIP_EMBED_TYPES:
            return
        try:
            text = _make_embedding_text(record_type, data)
            self._chroma.add(
                ids=[record_id],
                documents=[text],
                metadatas=[{"type": record_type, "timestamp": timestamp}],
            )
        except Exception as e:
            print(f"  [RAG] Chroma 인덱싱 실패 {record_id}: {e}", flush=True)

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

        # Chroma 동기화 (락 밖에서)
        self._chroma_add(record_id, record_type, data, timestamp)
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
                        record_type: str  = None) -> list:
        """
        Chroma 벡터 유사도 검색.
        결과 record_id로 SQLite에서 상세 데이터 조회.
        """
        if self._chroma is None:
            return []

        try:
            where = {"type": record_type} if record_type else None
            results = self._chroma.query(
                query_texts=[query],
                n_results=min(limit, self._chroma.count() or 1),
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            print(f"  [RAG] Chroma 검색 실패: {e}", flush=True)
            return []

        ids       = results["ids"][0] if results["ids"] else []
        distances = results["distances"][0] if results["distances"] else []

        if not ids:
            return []

        # SQLite에서 상세 데이터 조회
        conn = _connect(self.rag_file)
        try:
            placeholders = ",".join("?" * len(ids))
            rows = conn.execute(
                f"SELECT * FROM records WHERE id IN ({placeholders})", ids
            ).fetchall()
        finally:
            conn.close()

        # id 순서 유지 + similarity score 추가
        record_map = {row["id"]: _row_to_record(row) for row in rows}
        records = []
        for rid, dist in zip(ids, distances):
            if rid in record_map:
                rec = dict(record_map[rid])
                rec["similarity"] = round(1 - dist, 4)  # cosine distance → similarity
                records.append(rec)

        return records

    def reindex_chroma(self) -> int:
        """SQLite 전체 레코드를 Chroma에 재인덱싱. 마이그레이션/복구용."""
        if self._chroma is None:
            print("  [RAG] Chroma 비활성화 상태 — 재인덱싱 불가")
            return 0

        conn = _connect(self.rag_file)
        try:
            rows = conn.execute("SELECT * FROM records").fetchall()
        finally:
            conn.close()

        count = 0
        batch_ids, batch_docs, batch_metas = [], [], []

        for row in rows:
            rec = _row_to_record(row)
            if rec["type"] in _SKIP_EMBED_TYPES:
                continue

            # 이미 인덱싱된 ID는 스킵
            try:
                existing = self._chroma.get(ids=[rec["id"]])
                if existing["ids"]:
                    continue
            except Exception:
                pass

            text = _make_embedding_text(rec["type"], rec["data"])
            batch_ids.append(rec["id"])
            batch_docs.append(text)
            batch_metas.append({"type": rec["type"], "timestamp": rec["timestamp"]})

            # 배치 100개씩 upsert
            if len(batch_ids) >= 100:
                self._chroma.add(ids=batch_ids, documents=batch_docs, metadatas=batch_metas)
                count += len(batch_ids)
                print(f"  [RAG] Chroma 인덱싱 진행: {count}개", flush=True)
                batch_ids, batch_docs, batch_metas = [], [], []

        if batch_ids:
            self._chroma.add(ids=batch_ids, documents=batch_docs, metadatas=batch_metas)
            count += len(batch_ids)

        print(f"  [RAG] Chroma 재인덱싱 완료: {count}개 추가")
        return count

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
        finally:
            conn.close()

        chroma_count = 0
        chroma_health = "disabled"  # disabled | empty | active | error
        chroma_error = None
        if self._chroma is not None:
            try:
                chroma_count = self._chroma.count()
                chroma_health = "active" if chroma_count > 0 else "empty"
            except Exception as e:
                chroma_health = "error"
                chroma_error = str(e)

        return {
            "total":         total + cache_count,
            "by_type":       by_type,
            "rag_file":      self.rag_file,
            "cache_file":    self.cache_file,
            "chroma_dir":    self.chroma_dir,
            "chroma_name":   CHROMA_NAME,
            "chroma_index":  chroma_count,
            "chroma_health": chroma_health,
            "chroma_error":  chroma_error,
            "last_updated":  last_updated,
        }

    def print_stats(self):
        s = self.stats()
        print(f"\n[RAG] 지식베이스 통계")
        print(f"  총 레코드: {s['total']}")
        print(f"  이력 DB:   {s['rag_file']}")
        print(f"  캐시 DB:   {s['cache_file']}")
        print(f"  Chroma:    {s['chroma_dir']} ({s['chroma_index']}개 인덱싱)")
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