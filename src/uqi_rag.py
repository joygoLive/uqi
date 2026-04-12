# uqi_rag.py
# UQI 지식베이스 (RAG - Retrieval Augmented Generation)
# SQLite WAL 기반 + ChromaDB 시맨틱 검색
# UQI (Universal Quantum Infrastructure)

import json
import uuid
import sqlite3
import threading
from pathlib import Path
from typing import Optional
from datetime import datetime


_DATA_DIR   = Path(__file__).parent.parent / "data"
RAG_FILE    = str(_DATA_DIR / "uqi_rag.db")
CACHE_FILE  = str(_DATA_DIR / "uqi_cache.db")
CHROMA_DIR  = str(_DATA_DIR / "uqi_chroma")

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
        self._init_chroma()

    def _init_chroma(self):
        """Chroma 컬렉션 초기화. 실패해도 서버 시작 차단 안 함."""
        try:
            import chromadb
            from chromadb.utils import embedding_functions
            client = chromadb.PersistentClient(path=self.chroma_dir)
            ef = embedding_functions.DefaultEmbeddingFunction()  # all-MiniLM-L6-v2
            self._chroma = client.get_or_create_collection(
                name="uqi_knowledge",
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
        if self._chroma:
            try: chroma_count = self._chroma.count()
            except: pass

        return {
            "total":        total + cache_count,
            "by_type":      by_type,
            "rag_file":     self.rag_file,
            "cache_file":   self.cache_file,
            "chroma_dir":   self.chroma_dir,
            "chroma_index": chroma_count,
            "last_updated": last_updated,
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