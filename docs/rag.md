# UQI Knowledge Base (RAG) 아키텍처 가이드

다층 검색 + LLM 답변 합성 시스템. 2026-05 이전 단일 Chroma 기반 구성에서
DGX Spark 위 multi-component pipeline 으로 이관됨.

## 1. 컴포넌트 토폴로지 (단일 DGX Spark 박스)

```
┌────────────────────────────────────────────────────────────────────┐
│  systemd                                                            │
│  ┌──────────────────────┐  ┌──────────────────────┐                 │
│  │ uqi-embed.service    │  │ uqi-rerank.service   │                 │
│  │ Docker uqi-rag:0.1   │  │ Docker uqi-rag:0.1   │                 │
│  │ → embed_server.py    │  │ → rerank_server.py   │                 │
│  │ bge-m3 / 1024-dim    │  │ bge-reranker-v2-m3   │                 │
│  │ 127.0.0.1:7997       │  │ 127.0.0.1:7998       │                 │
│  └──────────────────────┘  └──────────────────────┘                 │
│                ▲                       ▲                            │
│                │ HTTP(loopback)        │ HTTP(loopback)              │
│                │                       │                            │
│  ┌─────────────┴───────────────────────┴──────────────────────┐     │
│  │ uqi-mcp.service                                             │     │
│  │ Python venv mcp_server.py (Starlette SSE :8765)             │     │
│  │   ├─ uqi_rag_search()  (검색 도구)                            │     │
│  │   └─ uqi_kb_ask()       (검색 + 합성 도구) ─→ Anthropic API   │     │
│  │ SQLite uqi_rag.db                                            │     │
│  │   ├─ records (source of truth)                               │     │
│  │   ├─ record_vec  (sqlite-vec, 1024-dim)                      │     │
│  │   └─ record_fts  (FTS5 BM25)                                 │     │
│  │ Chroma (4주 백업, Phase 8 후 제거)                            │     │
│  └─────────────────────────────────────────────────────────────┘     │
│                ▲                                                    │
│                │ ngrok                                              │
└────────────────┼────────────────────────────────────────────────────┘
                 │
        브라우저 (Knowledge 탭)
```

## 2. 검색 → 답변 파이프라인

`UQIRAG.search_semantic(query, limit=10, rerank=True, hybrid=True)`

```
사용자 쿼리
  │
  ▼ embed_server (bge-m3) /embeddings
1024-dim query vector + 동일 텍스트
  │
  ├─► sqlite-vec record_vec (cosine)  → top-50 dense
  └─► FTS5 record_fts (BM25)          → top-50 sparse
  │
  ▼ RRF (k=60) — 두 ranking 결합
top-50 후보
  │
  ▼ rerank_server (bge-reranker-v2-m3) /rerank
정밀 재정렬 → top-`limit`

(uqi_kb_ask 의 경우 여기에 한 단계 추가)
  │
  ▼ uqi_rag_scrub.scrub() — 마스킹
민감 필드 제거된 컨텍스트
  │
  ▼ Anthropic API (UQI_SYNTH_MODEL)
답변 + 인용 [record_id]
```

## 3. 환경 변수 (`.env`)

```env
# 임베딩 / 재랭킹 (DGX Spark 로컬)
UQI_EMBED_URL=http://127.0.0.1:7997
UQI_EMBED_MODEL=BAAI/bge-m3
UQI_EMBED_DIM=1024
UQI_RERANK_URL=http://127.0.0.1:7998
UQI_RERANK_MODEL=BAAI/bge-reranker-v2-m3
UQI_RERANK_TOPN=50            # over-fetch before rerank

# 백엔드 선택 (auto / sqlite-vec / chroma)
UQI_RAG_BACKEND=auto

# 답변 합성
ANTHROPIC_API_KEY=sk-ant-...
UQI_SYNTH_MODEL=claude-opus-4-7-...

# 마스킹 정책 (off / standard / strict)
UQI_SCRUB_LEVEL=standard

# (옵션) 저장 경로 오버라이드
UQI_RAG_FILE=...
UQI_CACHE_FILE=...
UQI_CHROMA_DIR=...
UQI_CHROMA_COLLECTION=...
```

## 4. 운영 명령

```bash
# 전체 / 개별 재시작
sudo systemctl restart uqi-embed uqi-rerank uqi-mcp

# 로그 (실시간 tail)
journalctl -u uqi-embed -f
journalctl -u uqi-rerank -f
journalctl -u uqi-mcp -f

# 컨테이너 상태
docker ps --filter "name=uqi-"
# GPU 점유
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv

# 임베딩 서버 직접 호출 테스트
curl -sS http://127.0.0.1:7997/health
curl -sS -X POST http://127.0.0.1:7997/embeddings \
  -H "Content-Type: application/json" \
  -d '{"input":["hello world"]}'

# RAG 품질 평가
cd ~/work/orientom/uqi
python3 tests/golden_set_eval.py --live --k 10        # live (sqlite-vec)
python3 tests/golden_set_eval.py --baseline --k 10    # baseline (chroma+v1)

# 마이그레이션 (records → record_vec/fts, 멱등)
python3 tests/migrate_to_sqlite_vec.py                # missing only
python3 tests/migrate_to_sqlite_vec.py --rebuild      # from scratch
python3 tests/migrate_to_sqlite_vec.py --dry-run --limit 20
```

## 5. 코드 구조

| 파일 | 역할 |
|---|---|
| `src/uqi_rag.py` | UQIRAG 클래스: SQLite + Chroma + sqlite-vec 통합 |
| `src/uqi_rag_scrub.py` | API 송신 전 민감 필드 마스킹 |
| `src/mcp_server.py` | MCP tools `uqi_rag_search`, `uqi_kb_ask` |
| `/etc/uqi/embed_server.py` | bge-m3 임베딩 OpenAI-호환 서버 |
| `/etc/uqi/rerank_server.py` | bge-reranker-v2-m3 cross-encoder 서버 |
| `/etc/uqi/Dockerfile` | NGC PyTorch 25.06 + sentence-transformers pinned |
| `webapp/uqi_webapp.html` | Knowledge 탭 UI (Search/Semantic/Ask) |
| `tests/migrate_to_sqlite_vec.py` | records → vec/fts 백필 (멱등) |
| `tests/golden_set_*.py` | 평가 인프라 (Recall, MRR, NDCG, Type-Recall) |
| `tests/test_rag_quality.py` | 회귀 임계값 가드 (live) |

## 6. 골든셋 평가

`tests/golden_set.json` — 30 쿼리 × (query, intent, category, expected, expected_types, current_top5).

```bash
# expected 후보 자동 추천 + apply
python3 tests/golden_set_suggest.py --apply

# expected_types 재생성 (expected_ids 의 type set)
python3 tests/golden_set_suggest.py --types

# 빈 expected 채운 뒤 평가
python3 tests/golden_set_eval.py --live --k 10
```

**현재 vs Baseline**

| Metric | Baseline (Chroma + all-MiniLM-L6-v2 + v1) | 새 (sqlite-vec + bge-m3 + v2 + RRF + rerank) | 변화 |
|---|---:|---:|---:|
| Recall@10 | 0.164 | **0.456** | +178% |
| MRR | 0.227 | **0.577** | +154% |
| NDCG@10 | 0.179 | **0.461** | +158% |
| Type-Recall@10 | — (new metric) | **0.745** | — |

회귀 임계값(`test_rag_quality.py`): Type-Recall ≥ 0.65, Recall ≥ 0.35, MRR ≥ 0.45.

## 7. 일반적 트러블슈팅

| 증상 | 진단 / 조치 |
|---|---|
| `uqi_kb_ask` 가 `ANTHROPIC_API_KEY missing` 반환 | `.env` 의 키 비어있는지 확인. 키 채우고 `sudo systemctl restart uqi-mcp` |
| `embed 호출 실패 (...)` 로그 | `systemctl status uqi-embed` 확인. 다운 시 자동 chroma fallback (성능 ↓) |
| `rerank 호출 실패 → 원본 순서 유지` | `systemctl status uqi-rerank`. dense 결과 그대로 반환됨 (정밀도 ↓ 만) |
| Chrome 페이지 크래시 | (이전 issue) contentIndex 안 쓰니 무관. 답변 합성 별도 SSE 큰 응답 시 검토 |
| RAG 품질 회귀 fail | `python3 tests/golden_set_eval.py --live` 로 어느 쿼리 / 카테고리 퇴행했는지 확인 |
| sqlite-vec 로드 실패 | `pip install sqlite-vec` 확인 + `_connect()` 가 `enable_load_extension` 지원하는 빌드인지 |
| 임베딩 차원 mismatch | record_vec 스키마 `FLOAT[1024]` 와 임베딩 모델 출력 차원 일치 확인 |

## 8. Phase 8 (관측 후 chroma 제거)

새 백엔드 운영 4주 후 (2026-06-08 즈음) 안전하게 제거:

```bash
# 1. Chroma 데이터 백업 (혹시 모를 롤백용)
tar czf ~/uqi_chroma_backup_$(date +%Y%m%d).tar.gz \
    /home/sean/work/orientom/uqi/data/uqi_chroma

# 2. uqi_rag.py 에서 _init_chroma() / _chroma_add() / _search_chroma() 제거
#    (또는 UQI_RAG_BACKEND=sqlite-vec 강제로 환경변수만 변경해 비활성)

# 3. 환경변수 UQI_CHROMA_* 4개 제거 from .env
# 4. data/uqi_chroma/ 디렉토리 제거
rm -rf /home/sean/work/orientom/uqi/data/uqi_chroma
# 5. sudo systemctl restart uqi-mcp
# 6. 골든셋 회귀 통과 재확인
python3 tests/golden_set_eval.py --live --k 10
```
