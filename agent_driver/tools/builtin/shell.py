"""Governed shell tool with read-only command policy."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_driver.contracts import (
    ApprovalMode,
    SideEffectClass,
    ToolManifest,
    ToolRisk,
)
from agent_driver.tools.registry import ToolRegistry

_BASH_TOOL = "bash"
_DEFAULT_TIMEOUT_SECONDS = 8.0
_DEFAULT_MAX_OUTPUT_CHARS = 6_000
_READONLY_PREFIXES = {
    "ls",
    "pwd",
    "echo",
    "whoami",
    "date",
    "uname",
    "env",
    "rg",
    "git",
    "python",
    "python3",
    "pip",
    "pip3",
    "pytest",
    ".venv/bin/python",
    ".venv/bin/pytest",
}
_NETWORK_READ_PREFIXES = {"curl", "wget"}
_READONLY_GIT_SUBCOMMANDS = {"status", "log", "show", "diff", "branch", "rev-parse"}
_FORBIDDEN_PATTERN = re.compile(
    r"(^|[\s;&|])(rm|mv|cp|chmod|chown|sudo|dd|mkfs|mount|umount|shutdown|reboot)\b"
)
_REDIRECTION_PATTERN = re.compile(r"(>>|>|<|\|\s*tee\b)")
_SPLIT_PATTERN = re.compile(r"\s*(?:&&|\|\||\|)\s*")
_STATEMENT_SEPARATOR_PATTERN = re.compile(r"(^|[^\\]);")
_NETWORK_TARGET_RE = re.compile(r"https?://([^/\s]+)")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass(frozen=True, slots=True)
class _BashRequest:
    command: str
    cwd: Path
    timeout_seconds: float
    max_output_chars: int


@dataclass(frozen=True, slots=True)
class _CommandPolicyResult:
    allowed: bool
    risk: str
    category: str
    reasons: list[str]


def register_shell_tools(registry: ToolRegistry) -> None:
    """Register governed shell command tool."""
    registry.register(_bash_manifest(), _bash_handler)


def _bash_manifest() -> ToolManifest:
    return ToolManifest(
        name=_BASH_TOOL,
        description=(
            "Execute read-only shell commands with timeout and bounded output."
        ),
        risk=ToolRisk.HIGH,
        side_effect=SideEffectClass.IRREVERSIBLE_WRITE,
        approval_mode=ApprovalMode.ON_POLICY_MATCH,
        timeout_seconds=20.0,
        output_char_budget=9000,
        idempotent=False,
        args_schema={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute",
                },
                "cwd": {
                    "type": "string",
                    "description": "Optional absolute working directory",
                },
                "timeout_seconds": {
                    "type": "number",
                    "minimum": 0.1,
                    "maximum": 120,
                    "description": "Execution timeout for command",
                },
                "max_output_chars": {
                    "type": "integer",
                    "minimum": 64,
                    "maximum": 100_000,
                    "description": "Maximum stdout/stderr characters per stream",
                },
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        output_type="json",
    )


async def _bash_handler(args: dict[str, Any]) -> dict[str, Any]:
    request = _parse_bash_request(args)
    policy = _evaluate_command_policy(request.command)
    if not policy.allowed:
        raise ValueError("; ".join(policy.reasons) or "command blocked by policy")
    execution = await _execute_bash(
        command=request.command,
        cwd=request.cwd,
        timeout_seconds=request.timeout_seconds,
    )
    stdout_preview = execution["stdout"][: request.max_output_chars]
    stderr_preview = execution["stderr"][: request.max_output_chars]
    truncated = (
        len(execution["stdout"]) > request.max_output_chars
        or len(execution["stderr"]) > request.max_output_chars
    )
    summary = (
        f"bash command completed (exit={execution['exit_code']}, "
        f"timed_out={execution['timed_out']}, "
        f"risk={policy.risk}, category={policy.category})"
    )
    return {
        "summary": summary,
        "command": request.command,
        "cwd": str(request.cwd),
        "exit_code": execution["exit_code"],
        "timed_out": execution["timed_out"],
        "risk_level": policy.risk,
        "risk_category": policy.category,
        "policy_reasons": policy.reasons,
        "stdout": stdout_preview,
        "stderr": stderr_preview,
        "truncated": truncated,
    }


def _evaluate_command_policy(command: str) -> _CommandPolicyResult:
    reasons: list[str] = []
    if _STATEMENT_SEPARATOR_PATTERN.search(command):
        reasons.append("statement separator ';' is not allowed")
    if _REDIRECTION_PATTERN.search(command):
        reasons.append("shell redirection/tee is not allowed")
    if _FORBIDDEN_PATTERN.search(command):
        reasons.append("destructive command keyword is blocked")
    segments = [segment.strip() for segment in _SPLIT_PATTERN.split(command) if segment]
    if not segments:
        reasons.append("command is empty after parsing")
    categories: list[str] = []
    for segment in segments:
        first = _first_token(segment)
        if first is None:
            reasons.append("unable to parse command segment")
            continue
        if first not in _READONLY_PREFIXES:
            if first in _NETWORK_READ_PREFIXES:
                if _is_private_network_target(segment):
                    reasons.append("network command target must not be localhost/private")
                    categories.append("destructive")
                else:
                    categories.append("network_read")
                continue
            reasons.append(f"command prefix '{first}' is not in read-only allowlist")
            categories.append("unknown")
            continue
        if first == "git":
            if not _is_readonly_git(segment):
                reasons.append(
                    "git command must be one of status/log/show/diff/branch/rev-parse"
                )
                categories.append("write_like")
                continue
            categories.append("readonly")
            continue
        categories.append("readonly")
    allowed = not reasons
    category = _resolve_risk_category(categories)
    risk = _risk_from_category(category)
    return _CommandPolicyResult(
        allowed=allowed,
        risk=risk,
        category=category,
        reasons=reasons,
    )


def _first_token(segment: str) -> str | None:
    try:
        parts = shlex.split(segment)
    except ValueError:
        return None
    if not parts:
        return None
    return parts[0]


def _parse_bash_request(args: dict[str, Any]) -> _BashRequest:
    command = str(args.get("command") or "").strip()
    if not command:
        raise ValueError("command is required")
    cwd = _resolve_cwd(args.get("cwd"))
    timeout_seconds = _as_float(
        args.get("timeout_seconds"),
        default=_DEFAULT_TIMEOUT_SECONDS,
        minimum=0.1,
    )
    max_output_chars = _as_int(
        args.get("max_output_chars"),
        default=_DEFAULT_MAX_OUTPUT_CHARS,
        minimum=64,
    )
    return _BashRequest(
        command=command,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        max_output_chars=max_output_chars,
    )


async def _execute_bash(
    *, command: str, cwd: Path, timeout_seconds: float
) -> dict[str, Any]:
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    timed_out = False
    try:
        raw_stdout, raw_stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_seconds
        )
    except TimeoutError:
        timed_out = True
        proc.kill()
        raw_stdout, raw_stderr = await proc.communicate()
    exit_code = int(proc.returncode) if proc.returncode is not None else 1
    return {
        "stdout": raw_stdout.decode("utf-8", errors="replace"),
        "stderr": raw_stderr.decode("utf-8", errors="replace"),
        "timed_out": timed_out,
        "exit_code": exit_code,
    }


def _is_readonly_git(segment: str) -> bool:
    try:
        parts = shlex.split(segment)
    except ValueError:
        return False
    if len(parts) < 2:
        return False
    return parts[1] in _READONLY_GIT_SUBCOMMANDS


def _is_private_network_target(segment: str) -> bool:
    for match in _NETWORK_TARGET_RE.finditer(segment):
        host = match.group(1).strip().lower()
        if ":" in host:
            host = host.split(":", maxsplit=1)[0]
        if host in _LOCAL_HOSTS:
            return True
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            continue
        if addr.is_private or addr.is_loopback:
            return True
    return False


def _resolve_risk_category(categories: list[str]) -> str:
    if not categories:
        return "unknown"
    if "destructive" in categories:
        return "destructive"
    if "write_like" in categories:
        return "write_like"
    if "unknown" in categories:
        return "unknown"
    if "network_read" in categories:
        return "network_read"
    return "readonly"


def _risk_from_category(category: str) -> str:
    if category == "readonly":
        return "low"
    if category in {"network_read", "write_like"}:
        return "medium"
    return "high"


def _resolve_cwd(raw: Any) -> Path:
    if raw is None:
        cwd = Path.cwd()
    elif isinstance(raw, str) and raw.strip():
        cwd = Path(raw).expanduser()
    else:
        raise ValueError("cwd must be a non-empty string when provided")
    if not cwd.is_absolute():
        raise ValueError("cwd must be absolute")
    if not cwd.exists() or not cwd.is_dir():
        raise ValueError(f"cwd is not an existing directory: {cwd}")
    return cwd


def _as_int(raw: Any, *, default: int, minimum: int) -> int:
    if raw is None:
        return default
    value = int(raw)
    if value < minimum:
        raise ValueError(f"value must be >= {minimum}")
    return value


def _as_float(raw: Any, *, default: float, minimum: float) -> float:
    if raw is None:
        return default
    value = float(raw)
    if value < minimum:
        raise ValueError(f"value must be >= {minimum}")
    return value


__all__ = ["register_shell_tools"]
