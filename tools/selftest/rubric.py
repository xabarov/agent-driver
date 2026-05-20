"""Log scoring helpers for interactive self-test scenarios."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    passed: bool


def score_log(*, scenario_id: str, text: str) -> list[CheckResult]:
    checks: list[CheckResult] = []
    if scenario_id == "A":
        checks.extend(
            [
                CheckResult("used_web_search", _used_tool(text, "web_search")),
                CheckResult("used_web_fetch", _used_tool(text, "web_fetch")),
                CheckResult("final_has_url", _has(text, r"https?://")),
            ]
        )
    elif scenario_id == "B":
        checks.extend(
            [
                CheckResult("used_glob_search", _used_tool(text, "glob_search")),
                CheckResult("mentions_md", _has(text, r"\.md")),
                CheckResult(
                    "avoids_web_tools",
                    not _used_tool(text, "web_search") and not _used_tool(text, "web_fetch"),
                ),
            ]
        )
    elif scenario_id == "C":
        checks.extend(
            [
                CheckResult("has_doctor_output", _has(text, r"doctor> ")),
                CheckResult(
                    "doctor_has_last_signal",
                    _has(text, r"doctor> last_signal (final_answered|interrupt_requested:[\\w_]+)"),
                ),
            ]
        )
    return checks


def summarize_score(*, checks: list[CheckResult]) -> tuple[int, int]:
    passed = sum(1 for item in checks if item.passed)
    return passed, len(checks)


def detect_provider_error(text: str) -> str | None:
    patterns: list[tuple[str, str]] = [
        (r"httpx\.HTTPStatusError:.*400 Bad Request", "http_400_bad_request"),
        (r"httpx\.RemoteProtocolError", "remote_protocol_error"),
        (r"ConnectTimeout", "connect_timeout"),
        (r"ReadTimeout", "read_timeout"),
        (r"event> interrupt reason=model_error", "interrupt_model_error"),
    ]
    for pattern, label in patterns:
        if _has(text, pattern):
            return label
    return None


def _has(text: str, pattern: str) -> bool:
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _used_tool(text: str, tool_name: str) -> bool:
    escaped = re.escape(tool_name)
    patterns = [
        rf"\[tool\s+{escaped}\]",
        rf"tool>\s*{escaped}\(",
        rf"'tool_name':\s*'{escaped}'",
        rf'"tool_name":\s*"{escaped}"',
    ]
    return any(_has(text, pattern) for pattern in patterns)
