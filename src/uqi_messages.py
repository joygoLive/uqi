# uqi_messages.py
# 백엔드 전체에서 사용되는 사용자/UI 표시 메시지 상수 중앙 관리
# i18n 준비: 모든 에러/상태 메시지는 여기서만 정의
# 향후 다국어 지원 시 이 파일의 값만 번역 파일로 교체하면 됨

# ─── Status codes (DB 저장용 — 변경 금지) ───────────────────
STATUS_SUBMITTED             = "submitted"
STATUS_RUNNING               = "running"
STATUS_DONE                  = "done"
STATUS_ERROR                 = "error"
STATUS_CANCELLED             = "cancelled"
STATUS_FAILED                = "failed"

# ─── API response status codes (변경 금지) ───────────────────
STATUS_AWAITING_CONFIRMATION = "awaiting_confirmation"
STATUS_COMPLETED             = "completed"
STATUS_CACHE_EXPIRED         = "cache_expired"
STATUS_SUBMITTING            = "submitting"

# ─── Executor: IBM ──────────────────────────────────────────
IBM_BACKEND_INACCESSIBLE     = "백엔드 접근 불가 (이전 회로 동일 오류) — 스킵"
IBM_NO_CIRCUIT               = "회로 변환 실패 (QIR/QASM 모두 없음)"
IBM_ESTIMATOR_NO_CIRCUIT     = "회로 없음 (QASM/원본 모두 없음)"
IBM_NO_QASM                  = "QASM 없음"

# ─── Executor: IQM ──────────────────────────────────────────
IQM_NO_QASM                  = "QASM 없음"
IQM_CIRCUIT_CONVERT_FAIL     = "IQM Circuit 변환 실패"
IQM_NO_RESULT                = "실행 결과 없음"
IQM_NO_TOKEN                 = "IQM_QUANTUM_TOKEN 없음"


def iqm_limit_exceeded(count: int) -> str:
    return f"IQM 제한 초과 ({count} instructions > 10000)"

# ─── Executor: CUDAQ ────────────────────────────────────────
CUDAQ_NO_SAMPLE_RESULT       = "cudaq.sample 결과 없음"


def cudaq_qubit_exceeded(num_qubits: int) -> str:
    return f"큐비트 수 초과 ({num_qubits}q > 10q)"

# ─── Executor: Perceval ─────────────────────────────────────
PERCEVAL_NO_CIRCUIT          = "회로 없음"
PERCEVAL_NO_TOKEN            = "QUANDELA_TOKEN 없음"
PERCEVAL_EMPTY_RESULT        = "결과 없음 (빈 counts) - 회로/입력 상태 확인 필요"


def perceval_modes_exceeded(circuit_m: int, max_modes: int) -> str:
    return f"모드 수 초과 ({circuit_m} > {max_modes})"


def perceval_photons_exceeded(n_photons: int, max_photons: int) -> str:
    return f"광자 수 초과 ({n_photons} > {max_photons})"


def perceval_run_fail(reason: str) -> str:
    return f"Perceval 실행 실패: {reason}"

# ─── MCP Server: QPU 제출 ────────────────────────────────────
MCP_CACHE_EXPIRED            = "분석 캐시가 만료되었습니다. confirmed=False로 다시 분석 후 제출해주세요."


def mcp_qpu_offline(qpu_name: str) -> str:
    return f"{qpu_name} 현재 offline 상태입니다."


def mcp_qpu_offline_cached(qpu_name: str) -> str:
    return f"{qpu_name} 현재 offline 상태입니다 (캐시)."


def mcp_qpu_offline_live(qpu_name: str, status_str: str) -> str:
    return f"{qpu_name} 사용 불가 — 현재 상태: {status_str}. 제출이 취소되었습니다."


def mcp_live_check_unreachable(qpu_name: str, attempts: int) -> str:
    return (f"{qpu_name} 상태 확인 실패 ({attempts}회 재시도 후 연결 불가). "
            f"현재 온라인 연결이 어려운 상태입니다. 잠시 후 다시 시도해주세요.")


def mcp_action_retry_or_cancel() -> str:
    return "추천된 다른 QPU로 재시도하거나 제출을 취소하세요."


def mcp_unavailable_qpu(qpu_name: str) -> str:
    return f"미지원 또는 가용하지 않은 QPU: {qpu_name}"


def mcp_qubit_exceeded_submit(name: str, circuit_qubits: int, qpu_name: str, device_qubits: int) -> str:
    return f"{name}: 회로 큐비트({circuit_qubits}q)가 {qpu_name} 장비({device_qubits}q)를 초과합니다. 제출 불가."


def mcp_qubit_exceeded_transpile(circuit_name: str, circuit_qubits: int, qpu_name: str, device_qubits: int) -> str:
    return f"회로 큐비트({circuit_qubits}q)가 {qpu_name} 장비({device_qubits}q)를 초과합니다. 트랜스파일 불가."

# ─── MCP Server: 기타 ────────────────────────────────────────
MCP_SEMANTIC_NO_QUERY        = "semantic 검색은 query 파라미터 필요"
MCP_FILE_EXT_NOT_ALLOWED     = "허용 확장자: .py"
MCP_FILE_ONLY_PY             = "Python 파일만 조회 가능합니다"
MCP_FILE_TOO_LARGE           = "파일 크기 초과 (최대 1MB)"
MCP_SUBMISSION_NOT_FOUND     = "submission_id not found"


def mcp_unsupported_query_type(query_type: str) -> str:
    return f"미지원 query_type: {query_type}"


def mcp_no_calibration(qpu_name: str) -> str:
    return f"캘리브레이션 없음: {qpu_name}"


def mcp_file_not_found(path: str) -> str:
    return f"파일 없음: {path}"


def mcp_dir_not_found(path: str) -> str:
    return f"디렉토리 없음: {path}"
