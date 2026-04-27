# conftest.py — pytest configuration + 모듈 격리 fixture

import sys
import pytest


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    passed  = len(terminalreporter.stats.get("passed",  []))
    failed  = len(terminalreporter.stats.get("failed",  []))
    error   = len(terminalreporter.stats.get("error",   []))
    skipped = len(terminalreporter.stats.get("skipped", []))
    total   = passed + failed + error + skipped

    terminalreporter.write_sep("=", "TEST SUMMARY")
    terminalreporter.write_line(f"  Total   : {total}")
    terminalreporter.write_line(f"  Passed  : {passed}")
    terminalreporter.write_line(f"  Failed  : {failed}")
    terminalreporter.write_line(f"  Error   : {error}")
    terminalreporter.write_line(f"  Skipped : {skipped}")
    terminalreporter.write_sep("=", "")


# ─────────────────────────────────────────────────────────────
# 모듈 캐시 격리 fixture (test isolation)
#
# 일부 테스트(perceval/qir_converter/executor 등)가 sys.modules를
# patch.dict로 mock하는 패턴을 사용하는데, 다른 테스트가 이미 같은
# 모듈을 import한 상태면 mock이 안 먹힘.
# 매 테스트 시작 전 mock 가능한 모듈을 sys.modules에서 제거해 fresh
# import 보장.
# ─────────────────────────────────────────────────────────────

# 테스트 간 격리가 필요한 모듈 prefix들
# (기존 테스트들이 patch.dict로 mock하는 패턴 분석 결과)
_ISOLATE_MODULE_PREFIXES = (
    "perceval",
    "uqi_executor_perceval",
    "uqi_qir_converter",
    # IBM/IQM은 이미 안정적이지만 print_summary 출력 capturing 영향
    # 받을 수 있어 같이 격리
    "uqi_executor_ibm",
    "uqi_executor_iqm",
)


@pytest.fixture(autouse=True)
def _isolate_module_cache():
    """매 테스트 후 격리 대상 모듈을 sys.modules에서 제거 → 다음 테스트 fresh import."""
    yield
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith(_ISOLATE_MODULE_PREFIXES):
            try:
                del sys.modules[mod_name]
            except KeyError:
                pass
