"""E3: ingestion-time prompt-injection / C2 scanner for untrusted context.

Files pulled into the system prompt — project memory (AGENTS.md/CLAUDE.md),
skills, recalled long-term memory — are untrusted input. This scanner runs at
*ingestion* time (not output-filter time): it matches a curated set of
prompt-injection and command-and-control patterns and, on a hit, the caller can
withhold the text (a blocking placeholder is provided) so a poisoned file never
reaches the model. Mirrors hermes' threat-pattern scan in the prompt builder.

Deterministic, dependency-free, and intentionally conservative — patterns
target unambiguous override/exfiltration phrasing to keep false positives low.
The caller decides the policy (drop vs placeholder); this module only detects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# (reason, compiled pattern). Case-insensitive; anchored to unambiguous phrasing.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "instruction_override",
        re.compile(
            r"\b(ignore|disregard|forget)\b.{0,40}"
            r"\b(previous|above|prior|earlier|all)\b"
            r".{0,20}\b(instructions?|prompts?|rules?|context)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "role_reassignment",
        re.compile(
            r"\byou\s+are\s+now\b.{0,40}\b(a|an|the|developer|admin|root|dan)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "system_prompt_probe",
        re.compile(
            r"\b(reveal|print|repeat|show|leak)\b.{0,30}\b(system\s+prompt|"
            r"your\s+instructions|hidden\s+prompt)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "exfiltration",
        re.compile(
            r"\b(exfiltrate|send|post|upload|leak)\b.{0,40}"
            r"(https?://|api[_-]?key|secret|credential|token|password)",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "remote_shell",
        re.compile(
            r"(curl|wget)\b[^\n]{0,80}\|\s*(sh|bash|zsh)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "code_eval_payload",
        re.compile(
            r"\b(eval|exec)\s*\(\s*(base64|atob|fromCharCode|__import__)",
            re.IGNORECASE,
        ),
    ),
)

_PLACEHOLDER = (
    "[context withheld: ingested file flagged for a suspected prompt-injection "
    "or command-and-control pattern ({reasons})]"
)


@dataclass(slots=True)
class ScanResult:
    """Outcome of scanning one untrusted text blob."""

    flagged: bool
    source: str = ""
    reasons: list[str] = field(default_factory=list)
    safe_text: str = ""


def scan_context_text(text: str, *, source: str = "context") -> ScanResult:
    """Scan ``text`` for injection/C2 patterns.

    Returns a :class:`ScanResult`. When ``flagged``, ``safe_text`` is a short
    blocking placeholder naming the matched reasons (so the caller can either
    drop the text or surface that a file was withheld); otherwise ``safe_text``
    is the original text unchanged.
    """
    reasons = [reason for reason, pattern in _PATTERNS if pattern.search(text)]
    if not reasons:
        return ScanResult(flagged=False, source=source, reasons=[], safe_text=text)
    return ScanResult(
        flagged=True,
        source=source,
        reasons=reasons,
        safe_text=_PLACEHOLDER.format(reasons=", ".join(reasons)),
    )


__all__ = ["ScanResult", "scan_context_text"]
