#!/usr/bin/env python3
"""
Phase 2c — records → record_vec + record_fts 백필.

Source of truth: `records` 테이블 (uqi_rag.db).
Target:
  - `record_vec` (vec0 1024-dim) — bge-m3 임베딩
  - `record_fts` (FTS5 BM25)     — 동일 텍스트의 lexical 인덱스

임베딩 서버: $UQI_EMBED_URL (default http://127.0.0.1:7997).
임베딩 텍스트: _make_embedding_text_v2 (자연어화 버전).

특성
  - 멱등적: 이미 record_vec 에 들어간 record_id 는 자동 건너뜀
  - --resume: 명시적으로 누락된 것만 처리 (위와 동일하지만 디폴트)
  - --rebuild: 기존 record_vec / record_fts 비우고 처음부터
  - --limit N: 처음 N건만 (디버깅용)
  - --dry-run: 임베딩 호출 없이 대상 개수만
  - 배치(32) 단위 호출 + 트랜잭션
  - 실패 레코드는 건너뛰고 카운트만 보고

사용:
  python3 tests/migrate_to_sqlite_vec.py            # 누락된 것만 채우기
  python3 tests/migrate_to_sqlite_vec.py --rebuild  # 처음부터
  python3 tests/migrate_to_sqlite_vec.py --dry-run --limit 20
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from uqi_rag import (  # noqa: E402
    UQIRAG, _connect, _make_embedding_text_v2,
    _SKIP_EMBED_TYPES, EMBED_DIM, EMBED_URL, EMBED_MODEL,
)
import sqlite_vec  # noqa: E402


BATCH = 32


def embed_batch(texts: list[str], timeout: float = 60.0) -> list[list[float]]:
    """임베딩 서버에 POST /embeddings — 1024-dim float 배열 리스트 반환."""
    r = requests.post(
        f"{EMBED_URL.rstrip('/')}/embeddings",
        json={"model": EMBED_MODEL, "input": texts},
        timeout=timeout,
    )
    r.raise_for_status()
    out = r.json().get("data", [])
    vecs = [item["embedding"] for item in out]
    if len(vecs) != len(texts):
        raise RuntimeError(f"embedding count mismatch: {len(vecs)} != {len(texts)}")
    return vecs


def existing_vec_ids(conn) -> set[str]:
    return {row[0] for row in conn.execute("SELECT record_id FROM record_vec")}


def existing_fts_ids(conn) -> set[str]:
    return {row[0] for row in conn.execute("SELECT record_id FROM record_fts")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rebuild", action="store_true",
                    help="기존 record_vec / record_fts 비우고 처음부터")
    ap.add_argument("--limit", type=int, default=0, help="처음 N건만")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rag = UQIRAG()
    if not rag._sqlite_vec_ok:
        print("ERROR: sqlite-vec 미활성. 스키마 초기화 실패.", file=sys.stderr)
        return 1

    # 임베딩 서버 health 사전 확인
    try:
        h = requests.get(f"{EMBED_URL}/health", timeout=5).json()
        print(f"embed server: {h}")
    except Exception as e:
        print(f"ERROR: 임베딩 서버 {EMBED_URL} 응답 없음: {e}", file=sys.stderr)
        return 2

    conn = _connect(rag.rag_file)
    try:
        # records 후보 추출 (임베딩 제외 타입 필터)
        all_recs = conn.execute(
            "SELECT id, type, timestamp, data FROM records ORDER BY timestamp ASC"
        ).fetchall()
        candidates = [r for r in all_recs if r["type"] not in _SKIP_EMBED_TYPES]
        if args.limit > 0:
            candidates = candidates[: args.limit]

        if args.rebuild and not args.dry_run:
            print("rebuild=True: 기존 record_vec / record_fts 비웁니다")
            conn.execute("DELETE FROM record_vec")
            conn.execute("DELETE FROM record_fts")
            conn.commit()

        already_vec = set() if args.rebuild else existing_vec_ids(conn)
        already_fts = set() if args.rebuild else existing_fts_ids(conn)

        to_do = [r for r in candidates if r["id"] not in already_vec or r["id"] not in already_fts]

        print(f"\nrecords total           : {len(all_recs):>6}")
        print(f"  embeddable (after skip): {len(candidates):>6}")
        print(f"  already indexed (vec)  : {len(already_vec):>6}")
        print(f"  already indexed (fts)  : {len(already_fts):>6}")
        print(f"  pending                : {len(to_do):>6}")

        if not to_do:
            print("\n✓ 이미 완료된 상태입니다. (멱등성: 추가 작업 없음)")
            return 0

        if args.dry_run:
            for r in to_do[:5]:
                txt = _make_embedding_text_v2(r["type"], json.loads(r["data"]))
                print(f"  [dry] {r['id']} {r['type']:18s}  {txt[:80]}")
            print(f"  ... {len(to_do)} 건 dry-run")
            return 0

        # ── 배치 임베딩 + write-through ──
        t0 = time.time()
        ok = fail = 0
        for i in range(0, len(to_do), BATCH):
            chunk = to_do[i:i + BATCH]
            texts = []
            for r in chunk:
                d = json.loads(r["data"])
                texts.append(_make_embedding_text_v2(r["type"], d))
            try:
                vecs = embed_batch(texts)
            except Exception as e:
                fail += len(chunk)
                print(f"  ! batch [{i}..{i+len(chunk)}] failed: {e}", file=sys.stderr)
                continue

            for r, text, vec in zip(chunk, texts, vecs):
                rid = r["id"]
                try:
                    if rid not in already_vec:
                        conn.execute(
                            "INSERT OR REPLACE INTO record_vec(record_id, embedding) VALUES (?, ?)",
                            (rid, sqlite_vec.serialize_float32(vec)),
                        )
                    if rid not in already_fts:
                        conn.execute(
                            "INSERT INTO record_fts(record_id, content) VALUES (?, ?)",
                            (rid, text),
                        )
                    ok += 1
                except Exception as e:
                    fail += 1
                    print(f"  ! insert {rid} failed: {e}", file=sys.stderr)
            conn.commit()
            done = i + len(chunk)
            elapsed = time.time() - t0
            print(f"  [{done:>4}/{len(to_do)}] {elapsed:.1f}s  ({done / max(elapsed, 1e-3):.1f} docs/s)")

        print(f"\n✓ migration complete")
        print(f"  ok          : {ok}")
        print(f"  failed      : {fail}")
        print(f"  total time  : {time.time() - t0:.1f}s")
        print()
        v = conn.execute("SELECT COUNT(*) FROM record_vec").fetchone()[0]
        f = conn.execute("SELECT COUNT(*) FROM record_fts").fetchone()[0]
        print(f"  record_vec rows: {v}")
        print(f"  record_fts rows: {f}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
