#!/usr/bin/env python3
"""
force_sync_calibration.py
특정 QPU 캘리브레이션을 강제 동기화하여 DB에 저장하는 일회성 스크립트.

사용법:
  cd /home/sean/work/orientom/uqi
  /home/sean/work/orientom/QUWA/.venv_transpile/bin/python src/force_sync_calibration.py

- .env 파일에서 API 토큰 자동 로드 (uqi/.env)
- 대상 QPU: ibm_fez, rigetti_ankaa3 (데이터 없는 장비)
- 타임아웃: QPU당 60초
"""

import sys
import os
import concurrent.futures
from pathlib import Path

# ── 경로 설정 ──
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT / "src")   # .env 로드 기준 디렉토리

from dotenv import load_dotenv
load_dotenv(dotenv_path=ROOT / ".env")

from uqi_calibration import UQICalibration

TARGETS = [
    "ibm_fez",
    "rigetti_ankaa3",
]

TIMEOUT_PER_QPU = 60   # 초


def sync_one(cal: UQICalibration, qpu_name: str) -> bool:
    """타임아웃 내 단일 QPU 동기화"""
    # _SYNC_CACHE 우회: 강제 재동기화
    UQICalibration._SYNC_CACHE.pop(qpu_name, None)
    UQICalibration._SYNC_FAIL_CACHE.pop(qpu_name, None)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as exe:
        f = exe.submit(cal.sync, qpu_name)
        try:
            ok = f.result(timeout=TIMEOUT_PER_QPU)
            return ok
        except concurrent.futures.TimeoutError:
            print(f"  ✗ {qpu_name}: timeout ({TIMEOUT_PER_QPU}s 초과) — API 응답 없음")
            return False


def main():
    print(f"[ForceSync] 캘리브레이션 강제 동기화 시작")
    print(f"  DB: {ROOT / 'data' / 'uqi_calibration.db'}")
    print(f"  대상: {TARGETS}\n")

    cal = UQICalibration()

    for qpu in TARGETS:
        vendor = cal._detect_vendor(qpu)
        print(f"[{qpu}] vendor={vendor} — 동기화 중... (max {TIMEOUT_PER_QPU}s)")

        # 현재 DB 상태 확인
        existing = cal.data.get(qpu, {})
        if existing:
            print(f"  현재 데이터: num_qubits={existing.get('num_qubits')}, "
                  f"last_updated={existing.get('last_updated', 'N/A')}")
        else:
            print(f"  현재 데이터: 없음")

        ok = sync_one(cal, qpu)

        if ok:
            d = cal.data.get(qpu, {})
            print(f"  ✓ 동기화 성공: num_qubits={d.get('num_qubits')}, "
                  f"avg_2q_error={d.get('avg_2q_error')}, "
                  f"last_updated={d.get('last_updated')}")
        else:
            print(f"  ✗ 동기화 실패 — 기존 데이터 유지")
        print()

    print("[ForceSync] 완료")


if __name__ == "__main__":
    main()
