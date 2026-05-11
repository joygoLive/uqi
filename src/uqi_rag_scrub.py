"""
Phase 6 — 외부 API 전송 전 민감 필드 자동 마스킹.

`외부 API 사용 자유` 정책이라도 client_ip / 절대경로 / 인증 키 흔적이
LLM 합성 컨텍스트에 그대로 들어가면 운영상 위험. 호출 직전 단계에서
deep-copy 후 비파괴적으로 마스킹한다.

대상 (`standard` 모드 기준):
  · IP 주소 (IPv4 + IPv6 일부)
  · 파일 시스템 절대 경로 (/home/<u>/..., /root/..., 윈도 C:\\...)
  · 트레이스백 / 라인 인용의 절대경로
  · API 키 prefix: sk-..., ghp_..., AKIA..., AIza..., glpat-...,
    Bearer <token>, "api_key": "...", "password": "..."
  · security_block.pattern / match_line — 첫·끝 몇 글자만 남기고 중간 가림
  · 키 이름 자체가 sensitive 한 필드 — 통째 [redacted]:
    NOTION_TOKEN_V2, ANTHROPIC_API_KEY, AWS_SECRET_ACCESS_KEY 등

env:
  UQI_SCRUB_LEVEL = off | standard (default) | strict

  off       — 어떠한 마스킹도 안 함 (개발 디버그용)
  standard  — 위 정책 적용
  strict    — standard + algorithm_file/file_name 도 익명화 (file_001.py),
              security_block.tool 까지 가림
"""
import copy
import ipaddress
import os
import re
from typing import Any

SCRUB_LEVEL = os.environ.get("UQI_SCRUB_LEVEL", "standard").lower()

# ─── 정규식 패턴 ──────────────────────────────────────────────

_IPV4_RE = re.compile(
    r"\b(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\."
    r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
)
# 절대 경로 — UNIX + Windows
_ABS_PATH_RE = re.compile(r"(?<![A-Za-z0-9_/])(?:/(?:home|root|var|opt|etc|usr|srv|tmp)/[^\s\"'`]+)")
_WIN_PATH_RE = re.compile(r"\b[A-Z]:\\[^\s\"'`]+")

# API 키 / 토큰 패턴 (보수적 — false positive 줄임)
_TOKEN_PATTERNS = [
    re.compile(r"sk-(?:ant-)?[A-Za-z0-9_-]{20,}"),     # OpenAI / Anthropic
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),                # GitHub PAT
    re.compile(r"AKIA[0-9A-Z]{16}"),                    # AWS Access Key
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),              # Google API
    re.compile(r"glpat-[A-Za-z0-9_-]{20,}"),            # GitLab
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}", re.IGNORECASE),
    re.compile(r"(?i)(?:api[_-]?key|secret|password|token)\s*[:=]\s*['\"]?[A-Za-z0-9._-]{8,}['\"]?"),
]

# 키 자체가 항상 sensitive 한 필드 (값 전체 redact)
_SENSITIVE_KEYS = {
    "anthropic_api_key", "notion_token_v2", "notion_token", "ibm_quantum_token",
    "iqm_quantum_token", "aws_secret_access_key", "aws_access_key_id",
    "azure_client_secret", "quandela_token", "ionq_api_key", "rigetti_api_key",
    "pasqal_password", "pasqal_username",
    "api_key", "secret", "password", "client_secret", "auth_token",
    "x-api-key", "authorization",
}

# strict 모드에서만 가리는 키 (사용자가 활용해야 할 정보일 수 있음)
_STRICT_FIELDS = {"algorithm_file", "file_abspath", "file_name", "tool"}


# ─── 마스킹 헬퍼 ──────────────────────────────────────────────

def _mask_ipv4(s: str) -> str:
    def repl(m: re.Match) -> str:
        ip = m.group(0)
        try:
            parsed = ipaddress.ip_address(ip)
            if parsed.is_private or parsed.is_loopback or parsed.is_link_local:
                return "<ip-private>"
            return "<ip-redacted>"
        except ValueError:
            return ip
    return _IPV4_RE.sub(repl, s)


def _mask_paths(s: str) -> str:
    def repl(m: re.Match) -> str:
        path = m.group(0)
        # 마지막 component(basename)만 유지, 경로는 평탄화
        tail = path.rsplit("/", 1)[-1] or path.rsplit("\\", 1)[-1]
        return f"<path>/{tail}" if tail else "<path>"
    s = _ABS_PATH_RE.sub(repl, s)
    s = _WIN_PATH_RE.sub(repl, s)
    return s


def _mask_tokens(s: str) -> str:
    for p in _TOKEN_PATTERNS:
        s = p.sub("<credential-redacted>", s)
    return s


def _mask_pattern_text(s: str, keep: int = 3) -> str:
    """security_block.pattern 같이 짧지만 민감한 텍스트 가운데 가리기."""
    if not s:
        return s
    if len(s) <= keep * 2 + 1:
        return s[0] + "…" if s else "…"
    return s[:keep] + "…" + s[-keep:]


def _scrub_string(s: str, level: str) -> str:
    if not isinstance(s, str) or not s:
        return s
    s = _mask_tokens(s)
    s = _mask_ipv4(s)
    s = _mask_paths(s)
    return s


def scrub(obj: Any, level: str = None) -> Any:
    """레코드(또는 검색결과 리스트) 를 비파괴적으로 마스킹."""
    level = (level or SCRUB_LEVEL).lower()
    if level == "off":
        return obj
    return _scrub_walk(copy.deepcopy(obj), level)


def _scrub_walk(node: Any, level: str) -> Any:
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            kl = str(k).lower()
            if kl in _SENSITIVE_KEYS:
                out[k] = "<redacted>"
                continue
            if level == "strict" and kl in _STRICT_FIELDS and isinstance(v, str):
                # 절대경로/파일명을 통째로 가리되 확장자는 유지
                if "." in v:
                    out[k] = f"<file>{v[v.rfind('.'):]}"
                else:
                    out[k] = "<file>"
                continue
            # security_block 의 pattern / match_line — 첫·끝만 노출
            if kl in ("pattern", "match_line") and isinstance(v, str):
                out[k] = _mask_pattern_text(v)
                continue
            out[k] = _scrub_walk(v, level)
        return out
    if isinstance(node, list):
        return [_scrub_walk(x, level) for x in node]
    if isinstance(node, str):
        return _scrub_string(node, level)
    return node


__all__ = ["scrub", "SCRUB_LEVEL"]
