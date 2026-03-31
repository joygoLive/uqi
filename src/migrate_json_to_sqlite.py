#!/usr/bin/env python3
# migrate_json_to_sqlite.py
# uqi_calibration.json → uqi_calibration.db 마이그레이션

import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

DEFAULT_JSON = Path(__file__).parent / "uqi_calibration.json"
DEFAULT_DB   = Path(__file__).parent / "uqi_calibration.db"

_KNOWN_COLS = frozenset({
    'vendor', 'num_qubits', 'avg_t1_ms', 'avg_t2_ms',
    'avg_ro_error', 'avg_1q_error', 'avg_2q_error',
    'avg_1q_ns', 'avg_2q_ns', 'basis_gates', 'coupling_map', 'last_updated',
})


def migrate(json_path: Path, db_path: Path):
    if not json_path.exists():
        print(f"[migrate] JSON 파일 없음: {json_path}")
        sys.exit(1)

    with open(json_path, 'r') as f:
        data = json.load(f)

    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS calibrations (
            qpu_name    TEXT PRIMARY KEY,
            vendor      TEXT,
            num_qubits  INTEGER,
            avg_t1_ms   REAL,
            avg_t2_ms   REAL,
            avg_ro_error REAL,
            avg_1q_error REAL,
            avg_2q_error REAL,
            avg_1q_ns   REAL,
            avg_2q_ns   REAL,
            basis_gates TEXT,
            coupling_map TEXT,
            extra_data  TEXT,
            last_updated TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS calibration_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            qpu_name    TEXT NOT NULL,
            snapshot    TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_history_qpu_time
        ON calibration_history(qpu_name, recorded_at)
    """)
    conn.commit()

    cal_count = 0
    hist_count = 0

    for key, value in data.items():
        if key.endswith('__history'):
            qpu_name = key[:-len('__history')]
            snapshots = value if isinstance(value, list) else []
            for snap in snapshots:
                recorded_at = snap.get('last_updated', datetime.now().isoformat())
                conn.execute(
                    "INSERT INTO calibration_history (qpu_name, snapshot, recorded_at) VALUES (?, ?, ?)",
                    (qpu_name, json.dumps(snap, default=str), recorded_at)
                )
                hist_count += 1
        else:
            qpu_name = key
            entry = value
            extra = {k: v for k, v in entry.items() if k not in _KNOWN_COLS}
            bg = entry.get('basis_gates')
            cm = entry.get('coupling_map')
            conn.execute("""
                INSERT OR REPLACE INTO calibrations
                (qpu_name, vendor, num_qubits, avg_t1_ms, avg_t2_ms,
                 avg_ro_error, avg_1q_error, avg_2q_error, avg_1q_ns, avg_2q_ns,
                 basis_gates, coupling_map, extra_data, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                qpu_name,
                entry.get('vendor'),
                entry.get('num_qubits'),
                entry.get('avg_t1_ms'),
                entry.get('avg_t2_ms'),
                entry.get('avg_ro_error'),
                entry.get('avg_1q_error'),
                entry.get('avg_2q_error'),
                entry.get('avg_1q_ns'),
                entry.get('avg_2q_ns'),
                json.dumps(bg) if bg is not None else None,
                json.dumps(cm) if cm is not None else None,
                json.dumps(extra, default=str) if extra else None,
                entry.get('last_updated'),
            ))
            cal_count += 1

    conn.commit()
    conn.close()

    print(f"[migrate] 완료: 캘리브레이션 {cal_count}개, 이력 {hist_count}개 → {db_path}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="uqi_calibration.json → SQLite 마이그레이션")
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON, help="소스 JSON 경로")
    parser.add_argument("--db",   type=Path, default=DEFAULT_DB,   help="대상 DB 경로")
    args = parser.parse_args()
    migrate(args.json, args.db)
