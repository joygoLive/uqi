#!/usr/bin/env python3
"""
Generate test security_block records in the RAG DB.
Exercises _safe_file_check against a test file with blocked patterns.
Run from project root: python tests/generate_security_test_data.py
"""
import sys
import re as _re
from pathlib import Path

# Add src/ to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from uqi_rag import UQIRAG

_rag = UQIRAG()

_BLOCKED_PATTERNS = [
    (r'\bos\.(?!getenv\b)', "os module direct call"),
    (r'\bsubprocess\b', "subprocess module"),
    (r'\bsocket\b', "socket module"),
    (r'\beval\s*\(', "eval() usage"),
    (r'\bexec\s*\(', "exec() usage"),
    (r'\b__import__\s*\(', "__import__() usage"),
    (r'\bopen\s*\(.*["\']w["\']', "file write (open write)"),
]

_ALLOWED_IMPORTS = {
    "qiskit", "pennylane", "numpy", "scipy", "math", "cmath",
    "collections", "itertools", "functools", "typing", "abc",
}

test_file = str(Path(__file__).parent.parent / "alg-files" / "test_blocked_patterns.py")

try:
    source = Path(test_file).read_text(encoding="utf-8")
except FileNotFoundError:
    print(f"[ERROR] Test file not found: {test_file}")
    sys.exit(1)

_src_lines = source.splitlines()
blocked_count = 0

for pattern, desc in _BLOCKED_PATTERNS:
    m = _re.search(pattern, source)
    if m:
        line_no    = source[:m.start()].count('\n') + 1
        match_line = _src_lines[line_no - 1].strip() if _src_lines else ''
        _rag.add_security_block(
            test_file,
            reason=desc,
            pattern=pattern,
            tool="uqi_analyze",
            match_lineno=line_no,
            match_line=match_line,
        )
        print(f"  [+] Blocked pattern '{desc}' at line {line_no}: {match_line[:60]}")
        blocked_count += 1
        break  # _safe_file_check stops at first match

# Also test unauthorized import detection
import_lines = _re.findall(r'^\s*(?:import|from)\s+([\w\.]+)', source, _re.MULTILINE)
if blocked_count == 0:
    for mod in import_lines:
        root = mod.split(".")[0]
        if root not in _ALLOWED_IMPORTS:
            m2 = _re.search(rf'^\s*(?:import|from)\s+{_re.escape(root)}\b', source, _re.MULTILINE)
            line_no    = (source[:m2.start()].count('\n') + 1) if m2 else None
            match_line = _src_lines[line_no - 1].strip() if (line_no and _src_lines) else ''
            _rag.add_security_block(
                test_file,
                reason=f"Unauthorized module import: {root}",
                pattern=f"import {root}",
                tool="uqi_analyze",
                match_lineno=line_no,
                match_line=match_line,
            )
            print(f"  [+] Unauthorized import '{root}' at line {line_no}: {match_line[:60]}")
            blocked_count += 1
            break

print(f"\nDone. {blocked_count} security_block record(s) written to RAG DB.")
