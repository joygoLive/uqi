# run_tests.py
# UQI 전체 단위 테스트 통합 실행 runner

import os
import sys
import subprocess
from datetime import datetime
from pathlib import Path

# ─────────────────────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────────────────────

TESTS_DIR = Path(__file__).parent
LOGS_DIR  = TESTS_DIR.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

PYTHON = sys.executable

# (test_file, cov_module)  — webapp은 cov 없음
TEST_MODULES = [
    ("test_uqi_extractor.py",        "uqi_extractor"),
    ("test_uqi_executor_ibm.py",     "uqi_executor_ibm"),
    ("test_uqi_executor_iqm.py",     "uqi_executor_iqm"),
    ("test_uqi_executor_cudaq.py",   "uqi_executor_cudaq"),
    ("test_uqi_executor_perceval.py","uqi_executor_perceval"),
    ("test_uqi_optimizer.py",        "uqi_optimizer"),
    ("test_uqi_noise.py",            "uqi_noise"),
    ("test_uqi_calibration.py",      "uqi_calibration"),
    ("test_uqi_rag.py",              "uqi_rag"),
    ("test_uqi_qec.py",              "uqi_qec"),
    ("test_uqi_qir_converter.py",    "uqi_qir_converter"),
    ("test_mcp_server.py",           "mcp_server"),
    ("test_uqi_webapp.py",           None),  # JS — cov 제외
]


# ─────────────────────────────────────────────────────────────
# 실행
# ─────────────────────────────────────────────────────────────

def run_module(test_file: str, cov_module: str | None, log_path: Path) -> dict:
    cmd = [PYTHON, "-m", "pytest", test_file, "-v"]
    if cov_module:
        cmd += [
            f"--cov={cov_module}",
            "--cov-report=term-missing",
            f"--cov-config={TESTS_DIR.parent / 'setup.cfg'}",
        ]

    with open(log_path, "w", encoding="utf-8") as f:
        result = subprocess.run(
            cmd,
            cwd=str(TESTS_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        f.write(result.stdout)

    # 결과 파싱
    passed = failed = error = skipped = total = 0
    coverage = None
    for line in result.stdout.splitlines():
        if "Total   :" in line:
            total   = int(line.split(":")[1].strip())
        elif "Passed  :" in line:
            passed  = int(line.split(":")[1].strip())
        elif "Failed  :" in line:
            failed  = int(line.split(":")[1].strip())
        elif "Error   :" in line:
            error   = int(line.split(":")[1].strip())
        elif "Skipped :" in line:
            skipped = int(line.split(":")[1].strip())
        elif line.strip().startswith("TOTAL") and "%" in line:
            parts = line.split()
            for p in parts:
                if p.endswith("%"):
                    coverage = p
                    break

    return {
        "file":     test_file,
        "module":   cov_module or "webapp(JS)",
        "total":    total,
        "passed":   passed,
        "failed":   failed,
        "error":    error,
        "skipped":  skipped,
        "coverage": coverage or "—",
        "ok":       (failed == 0 and error == 0),
        "log":      str(log_path),
    }


def print_separator(char="─", width=78):
    print(char * width)


def main():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_log = LOGS_DIR / f"run_tests_{ts}.log"

    print_separator("═")
    print(f"  UQI 단위 테스트 통합 실행")
    print(f"  시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  대상: {len(TEST_MODULES)}개 모듈")
    print_separator("═")

    results = []
    total_passed = total_failed = total_error = total_tc = 0

    for test_file, cov_module in TEST_MODULES:
        log_path = LOGS_DIR / f"{test_file.replace('.py','')}_{ts}.log"
        print(f"\n  ▶ {test_file}", end="", flush=True)

        r = run_module(test_file, cov_module, log_path)
        results.append(r)

        status = "✓ PASS" if r["ok"] else "✗ FAIL"
        print(f"  →  {status}  ({r['passed']}/{r['total']} passed)")

        total_tc     += r["total"]
        total_passed += r["passed"]
        total_failed += r["failed"] + r["error"]

    # ── 최종 요약 ──
    print()
    print_separator("═")
    print(f"  {'모듈':<35} {'TC':>5} {'통과':>5} {'실패':>5} {'커버리지':>8}  {'결과'}")
    print_separator()
    for r in results:
        status = "✓" if r["ok"] else "✗"
        fail_cnt = r["failed"] + r["error"]
        print(
            f"  {r['module']:<35} {r['total']:>5} {r['passed']:>5} "
            f"{fail_cnt:>5} {r['coverage']:>8}  {status}"
        )
    print_separator()
    overall = "ALL PASS ✓" if total_failed == 0 else f"FAILED ✗ ({total_failed}건)"
    print(
        f"  {'TOTAL':<35} {total_tc:>5} {total_passed:>5} "
        f"{total_failed:>5} {'—':>8}  {overall}"
    )
    print_separator("═")
    print(f"  완료: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  로그: {summary_log}")
    print_separator("═")

    # 요약 로그 저장
    with open(summary_log, "w", encoding="utf-8") as f:
        f.write(f"UQI 단위 테스트 통합 결과 — {ts}\n\n")
        f.write(f"{'모듈':<35} {'TC':>5} {'통과':>5} {'실패':>5} {'커버리지':>8}  결과\n")
        f.write("─" * 78 + "\n")
        for r in results:
            fail_cnt = r["failed"] + r["error"]
            status = "PASS" if r["ok"] else "FAIL"
            f.write(
                f"{r['module']:<35} {r['total']:>5} {r['passed']:>5} "
                f"{fail_cnt:>5} {r['coverage']:>8}  {status}\n"
            )
        f.write("─" * 78 + "\n")
        f.write(
            f"{'TOTAL':<35} {total_tc:>5} {total_passed:>5} "
            f"{total_failed:>5} {'—':>8}  {overall}\n"
        )

    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())