"""Declarative permission policy: modes + deny/allow/ask rules.

A composable, operator-authorable policy that decides whether a planned tool
call is allowed, denied, or must be escalated for approval. It layers two
mechanisms:

* **Explicit rules** (first match wins) — match by tool-name glob and optional
  command substring/regex, with an explicit decision. Operator intent always
  wins over heuristics.
* **Mode default** — for calls no rule matched, the mode decides. ``YOLO``
  allows everything; ``STANDARD`` and ``STRICT`` run the
  :func:`classify_command` heuristic on command-bearing tools and map the risk
  level to allow/ask/deny.

The policy is provider- and runtime-neutral; :func:`build_permission_gate`
adapts it to the runtime ``ToolGate`` seam.
"""

from __future__ import annotations

import re
from fnmatch import fnmatch
from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums import StrEnum
from agent_driver.permissions.command_classifier import (
    CommandRiskLevel,
    classify_command,
)

_COMMAND_KEYS = ("command", "cmd", "script")


class PermissionMode(StrEnum):
    """How calls unmatched by an explicit rule are decided."""

    YOLO = "yolo"  # allow everything
    STANDARD = "standard"  # critical -> deny, dangerous -> ask
    STRICT = "strict"  # dangerous+ -> deny, caution -> ask


class PermissionDecision(StrEnum):
    """Outcome for one planned call."""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class PermissionOutcome(ContractModel):
    """A decision plus a human-readable reason."""

    decision: PermissionDecision
    reason: str = ""


class PermissionRule(ContractModel):
    """One explicit allow/deny/ask rule.

    Matches when the tool name matches any ``tools`` glob (empty = any tool)
    AND every supplied command condition holds. Command conditions inspect the
    call's command-bearing argument (``command`` / ``cmd`` / ``script``).
    """

    decision: PermissionDecision
    tools: tuple[str, ...] = ()
    command_includes: str | None = None
    command_regex: str | None = None
    path_under: str | None = None
    reason: str = ""

    @field_validator("command_regex")
    @classmethod
    def validate_regex(cls, value: str | None) -> str | None:
        """Compile the regex at load time so a bad rule fails loudly."""
        if value is None:
            return None
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"command_regex does not compile: {exc}") from exc
        return value

    def matches(self, tool_name: str, args: dict[str, Any]) -> bool:
        """Whether this rule applies to the planned call."""
        if self.tools and not any(fnmatch(tool_name, glob) for glob in self.tools):
            return False
        command = command_text(args)
        if self.command_includes is not None:
            if self.command_includes.lower() not in (command or "").lower():
                return False
        if self.command_regex is not None:
            if not re.search(self.command_regex, command or ""):
                return False
        if self.path_under is not None:
            # Scope-aware: the rule fires only when the call's path argument
            # *could* touch the protected prefix — i.e. the path's static anchor
            # (before any glob wildcard) overlaps ``path_under``. A bulk/glob op
            # rooted at or above the protected area triggers; an unrelated path
            # does not. Calls with no path argument never match.
            target = path_text(args)
            if target is None or not _path_overlaps(target, self.path_under):
                return False
        return True


# Mode -> (level that triggers DENY, level that triggers ASK). A command at or
# above the deny threshold is denied; at or above the ask threshold is asked;
# otherwise allowed.
_MODE_THRESHOLDS: dict[PermissionMode, tuple[CommandRiskLevel, CommandRiskLevel]] = {
    PermissionMode.STANDARD: (CommandRiskLevel.CRITICAL, CommandRiskLevel.DANGEROUS),
    PermissionMode.STRICT: (CommandRiskLevel.DANGEROUS, CommandRiskLevel.CAUTION),
}


class PermissionPolicy(ContractModel):
    """Ordered rules plus a mode default."""

    mode: PermissionMode = PermissionMode.STANDARD
    rules: tuple[PermissionRule, ...] = Field(default_factory=tuple)

    def decide(self, tool_name: str, args: dict[str, Any]) -> PermissionOutcome:
        """Resolve a planned call to an allow/ask/deny outcome."""
        for rule in self.rules:
            if rule.matches(tool_name, args):
                return PermissionOutcome(decision=rule.decision, reason=rule.reason)
        if self.mode == PermissionMode.YOLO:
            return PermissionOutcome(decision=PermissionDecision.ALLOW)
        command = command_text(args)
        if not command:
            return PermissionOutcome(decision=PermissionDecision.ALLOW)
        return self._classify_default(command)

    def _classify_default(self, command: str) -> PermissionOutcome:
        risk = classify_command(command)
        deny_at, ask_at = _MODE_THRESHOLDS[self.mode]
        reason = "; ".join(risk.reasons)
        if risk.level >= deny_at:
            return PermissionOutcome(decision=PermissionDecision.DENY, reason=reason)
        if risk.level >= ask_at:
            return PermissionOutcome(decision=PermissionDecision.ASK, reason=reason)
        return PermissionOutcome(decision=PermissionDecision.ALLOW, reason=reason)


def command_text(args: dict[str, Any]) -> str | None:
    """Return the command-bearing argument of a call, if any."""
    for key in _COMMAND_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


_PATH_KEYS = ("path", "file_path", "dir", "base_dir", "directory", "target")
_GLOB_CHARS = "*?["


def path_text(args: dict[str, Any]) -> str | None:
    """Return the path-bearing argument of a call, if any."""
    for key in _PATH_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _path_anchor(path: str) -> str:
    """The static prefix of a (possibly glob) path, before the first wildcard."""
    head = path
    for index, char in enumerate(path):
        if char in _GLOB_CHARS:
            head = path[:index]
            break
    # Normalize to a directory-ish prefix (drop the trailing partial segment).
    head = head.rstrip("/")
    return head or "/"


def _path_overlaps(path: str, protected: str) -> bool:
    """Whether ``path`` (or a glob rooted at it) could touch ``protected``.

    True when the path's static anchor and the protected prefix are in an
    ancestor relationship either way — a bulk op rooted at ``/`` or ``/etc``
    overlaps protected ``/etc``, while ``/home/x`` does not.
    """
    anchor = _path_anchor(path).rstrip("/") or "/"
    prot = protected.rstrip("/") or "/"
    a = anchor if anchor == "/" else anchor + "/"
    p = prot if prot == "/" else prot + "/"
    return a.startswith(p) or p.startswith(a)


__all__ = [
    "PermissionDecision",
    "PermissionMode",
    "PermissionOutcome",
    "PermissionPolicy",
    "PermissionRule",
    "command_text",
    "path_text",
]
