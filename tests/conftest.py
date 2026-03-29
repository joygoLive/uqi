# conftest.py

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