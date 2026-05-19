"""Governed PowerShell tool (policy-compatible shell sibling)."""

from __future__ import annotations

import asyncio
import shutil
from typing import Any

from agent_driver.contracts import ApprovalMode, SideEffectClass, ToolManifest, ToolRisk
from agent_driver.tools.registry import ToolRegistry

_POWERSHELL_TOOL = "powershell_tool"


def register_powershell_tools(registry: ToolRegistry) -> None:
    """Register PowerShell tool with explicit unavailable behavior."""
    registry.register(_powershell_manifest(), _powershell_handler)


def _powershell_manifest() -> ToolManifest:
    return ToolManifest(
        name=_POWERSHELL_TOOL,
        description=(
            "Execute bounded PowerShell command when pwsh is available "
            "(policy-compatible shell tool)."
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
                "command": {"type": "string"},
                "timeout_seconds": {"type": "number", "minimum": 0.1, "maximum": 120},
                "max_output_chars": {
                    "type": "integer",
                    "minimum": 64,
                    "maximum": 100_000,
                },
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        output_type="json",
        metadata={
            "implementation_status": "platform_gated_native",
            "adapter_kind": "shell",
            "application_tags": ["shell"],
        },
    )


async def _powershell_handler(args: dict[str, Any]) -> dict[str, Any]:
    command = str(args.get("command") or "").strip()
    if not command:
        raise ValueError("command is required")
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        raise ValueError("powershell_tool unavailable: 'pwsh' not found on this host")
    timeout_seconds = float(args.get("timeout_seconds") or 8.0)
    max_output_chars = int(args.get("max_output_chars") or 6000)
    proc = await asyncio.create_subprocess_exec(
        pwsh,
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        command,
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
    stdout = raw_stdout.decode("utf-8", errors="replace")
    stderr = raw_stderr.decode("utf-8", errors="replace")
    return {
        "summary": (
            f"powershell command completed (exit={proc.returncode}, timed_out={timed_out})"
        ),
        "command": command,
        "exit_code": int(proc.returncode) if proc.returncode is not None else 1,
        "timed_out": timed_out,
        "stdout": stdout[:max_output_chars],
        "stderr": stderr[:max_output_chars],
        "truncated": len(stdout) > max_output_chars or len(stderr) > max_output_chars,
    }


__all__ = ["register_powershell_tools"]
