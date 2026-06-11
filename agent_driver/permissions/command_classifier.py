"""Heuristic risk classifier for shell commands.

A reusable, dependency-free classifier that labels a shell command by how
dangerous it is, so a permission gate (or any tool) can deny/escalate risky
commands without each call site re-implementing the patterns. It is
deliberately conservative — it recognizes well-known destructive forms rather
than trying to fully parse a shell — and pattern-based, so it is deterministic
and exhaustively unit-testable.

This complements, and does not replace, the read-only allowlist baked into the
built-in ``bash`` tool: the classifier is a cross-tool risk signal usable by
the permission policy regardless of which tool runs the command.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntEnum


class CommandRiskLevel(IntEnum):
    """Ordered command risk levels (higher = more dangerous)."""

    SAFE = 0
    CAUTION = 1
    DANGEROUS = 2
    CRITICAL = 3


@dataclass(frozen=True, slots=True)
class CommandRisk:
    """Classification result for one command."""

    level: CommandRiskLevel
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def matched(self) -> bool:
        """Whether any risk above SAFE was detected."""
        return self.level > CommandRiskLevel.SAFE


# Each rule: (compiled pattern, level, human reason). The command's level is
# the max over all matching rules; reasons accumulate for transparency.
_RULES: tuple[tuple[re.Pattern[str], CommandRiskLevel, str], ...] = (
    # --- CRITICAL: irrecoverable / system-wide destruction ---
    (
        re.compile(
            r"\brm\b[^\n|;&]*\s-[a-z]*[rf][a-z]*\b[^\n|;&]*\s(/|~|\$HOME|\*)\s*$"
        ),
        CommandRiskLevel.CRITICAL,
        "recursive force-delete of a root/home/glob path",
    ),
    (
        re.compile(r"\brm\b[^\n|;&]*\s-[a-z]*[rf][a-z]*\b[^\n|;&]*\s(/\s|/\*|/\s*$)"),
        CommandRiskLevel.CRITICAL,
        "recursive force-delete targeting /",
    ),
    (
        re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
        CommandRiskLevel.CRITICAL,
        "fork bomb",
    ),
    (
        re.compile(r"\bmkfs(\.\w+)?\b"),
        CommandRiskLevel.CRITICAL,
        "filesystem format (mkfs)",
    ),
    (
        re.compile(r"\bdd\b[^\n]*\bof=/dev/(sd|nvme|disk|hd)"),
        CommandRiskLevel.CRITICAL,
        "raw write to a block device (dd of=/dev/...)",
    ),
    (
        re.compile(r">\s*/dev/(sd|nvme|disk|hd)\w*"),
        CommandRiskLevel.CRITICAL,
        "redirect to a raw block device",
    ),
    (
        re.compile(r"\b(chmod|chown)\b[^\n]*-[a-z]*R[a-z]*\b[^\n]*\s/(\s|$)"),
        CommandRiskLevel.CRITICAL,
        "recursive permission/ownership change on /",
    ),
    # --- DANGEROUS: remote-code execution, privilege, force-push ---
    (
        re.compile(
            r"\b(curl|wget|fetch)\b[^\n]*\|\s*(sudo\s+)?(sh|bash|zsh|python\d?)\b"
        ),
        CommandRiskLevel.DANGEROUS,
        "pipe network download directly into a shell/interpreter",
    ),
    (
        re.compile(r"\bsudo\b"),
        CommandRiskLevel.DANGEROUS,
        "privilege escalation (sudo)",
    ),
    (
        re.compile(r"\brm\b[^\n|;&]*\s-[a-z]*[rf][a-z]*\b"),
        CommandRiskLevel.DANGEROUS,
        "recursive/forced delete",
    ),
    (
        re.compile(r"\bgit\b[^\n]*\bpush\b[^\n]*(--force\b|-f\b|\+)"),
        CommandRiskLevel.DANGEROUS,
        "force push",
    ),
    (
        re.compile(r"\beval\b"),
        CommandRiskLevel.DANGEROUS,
        "dynamic command evaluation (eval)",
    ),
    (
        re.compile(r"\bkill\b[^\n]*\s-9?\s*-1\b"),
        CommandRiskLevel.DANGEROUS,
        "signal to every process",
    ),
    # --- CAUTION: mutating but usually recoverable ---
    (
        re.compile(r"\brm\b"),
        CommandRiskLevel.CAUTION,
        "file deletion",
    ),
    (
        re.compile(r"\b(curl|wget|fetch)\b"),
        CommandRiskLevel.CAUTION,
        "network access",
    ),
    (
        re.compile(r">>?"),
        CommandRiskLevel.CAUTION,
        "output redirection to a file",
    ),
    (
        re.compile(r"\b(mv|cp|truncate|tee)\b"),
        CommandRiskLevel.CAUTION,
        "filesystem mutation",
    ),
)


def classify_command(command: str) -> CommandRisk:
    """Classify a shell command string into a :class:`CommandRisk`."""
    text = (command or "").strip()
    if not text:
        return CommandRisk(level=CommandRiskLevel.SAFE)
    level = CommandRiskLevel.SAFE
    reasons: list[str] = []
    for pattern, rule_level, reason in _RULES:
        if pattern.search(text):
            reasons.append(reason)
            level = max(level, rule_level)
    return CommandRisk(level=level, reasons=tuple(reasons))


__all__ = ["CommandRisk", "CommandRiskLevel", "classify_command"]
