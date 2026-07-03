"""Guarded capability-pack validation runners."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from agent_driver.contracts.policy import ValidationGateResult
from agent_driver.harness.capability_packs import (
    _default_adapter_id,
    _default_scenario_ids,
    resolve_capability_pack,
    seed_adapter_manifests,
    seed_capability_packs,
    seed_scenario_specs,
)

_PLACEHOLDER_RE = re.compile(r"<[^>]+>")
_UNSAFE_COMMAND_PATTERNS = (
    ".env",
    "printenv",
    " env ",
    "cat ",
    "grep ",
)
_REDACTION_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password|auth)[=:][^\s]+"),
)


def run_capability_pack_deterministic_gates(
    *,
    pack_id: str,
    adapter_id: str | None = None,
    scenario_ids: list[str] | None = None,
    deterministic_commands: list[str] | None = None,
    cwd: str | Path | None = None,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    """Execute deterministic gate commands with conservative guardrails."""
    pack = _seed_pack(pack_id)
    adapter = _seed_adapter(adapter_id or _default_adapter_id(pack))
    scenarios = _seed_scenarios(scenario_ids or _default_scenario_ids(adapter.adapter_id))
    for scenario in scenarios:
        if scenario.product_adapter_id != adapter.adapter_id:
            raise ValueError(
                f"scenario {scenario.scenario_id} belongs to adapter "
                f"{scenario.product_adapter_id}, not {adapter.adapter_id}"
            )

    commands = list(deterministic_commands or adapter.deterministic_commands)
    command_results = [
        _run_or_block_command(
            command=command,
            index=index,
            cwd=Path(cwd or ".").resolve(),
            timeout_seconds=timeout_seconds,
        )
        for index, command in enumerate(commands, start=1)
    ]
    gate_result = _aggregate_deterministic_gate(command_results)
    resolution = resolve_capability_pack(
        pack,
        adapter_manifest=adapter,
        scenario_specs=scenarios,
        gate_results=[gate_result],
    )
    evidence_index = (
        resolution.evidence_index.model_dump(mode="json")
        if resolution.evidence_index is not None
        else None
    )
    return {
        "mode": "run_deterministic",
        "executed_commands": command_results,
        "would_execute": {
            "optional_live_commands": resolution.optional_live_commands,
        },
        "capability_pack_resolution": resolution.model_dump(mode="json"),
        "validation_gate_results": [gate_result.model_dump(mode="json")],
        "evidence_index": evidence_index,
        "redaction": {
            "safe_by_default": True,
            "contains_secret_values": False,
            "contains_raw_command_output": False,
        },
    }


def _run_or_block_command(
    *,
    command: str,
    index: int,
    cwd: Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    command_id = f"deterministic_{index}"
    block_reason = _blocked_reason(command)
    if block_reason is not None:
        return {
            "command_id": command_id,
            "command": command,
            "status": "blocked",
            "exit_code": None,
            "cwd": str(cwd),
            "reason": block_reason,
            "stdout": "",
            "stderr": "",
        }
    try:
        completed = subprocess.run(  # noqa: S602 - commands are pack-owned and guarded.
            command,
            cwd=cwd,
            shell=True,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "command_id": command_id,
            "command": command,
            "status": "failed",
            "exit_code": None,
            "cwd": str(cwd),
            "reason": "timeout",
            "stdout": _redact_text(exc.stdout or ""),
            "stderr": _redact_text(exc.stderr or ""),
        }
    return {
        "command_id": command_id,
        "command": command,
        "status": "passed" if completed.returncode == 0 else "failed",
        "exit_code": completed.returncode,
        "cwd": str(cwd),
        "reason": None if completed.returncode == 0 else "nonzero_exit",
        "stdout": _redact_text(completed.stdout),
        "stderr": _redact_text(completed.stderr),
    }


def _blocked_reason(command: str) -> str | None:
    if _PLACEHOLDER_RE.search(command):
        return "command_template_contains_placeholder"
    normalized = f" {command.lower()} "
    if any(pattern in normalized for pattern in _UNSAFE_COMMAND_PATTERNS):
        return "command_may_read_environment_or_secrets"
    return None


def _redact_text(value: str | bytes | None) -> str:
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    redacted = text or ""
    for pattern in _REDACTION_PATTERNS:
        redacted = pattern.sub(r"\1=<redacted>", redacted)
    return redacted


def _aggregate_deterministic_gate(
    command_results: list[dict[str, Any]],
) -> ValidationGateResult:
    statuses = [str(item.get("status")) for item in command_results]
    if not command_results:
        status = "skipped"
        reason = "no_deterministic_commands_selected"
    elif "failed" in statuses:
        status = "failed"
        reason = "one_or_more_commands_failed"
    elif "blocked" in statuses:
        status = "blocked"
        reason = "one_or_more_commands_blocked"
    else:
        status = "passed"
        reason = None
    return ValidationGateResult(
        gate_id="deterministic_tests",
        status=status,
        evidence_path="command_outputs/",
        reason=reason,
        redacted_metadata={
            "command_count": len(command_results),
            "statuses": statuses,
        },
    )


def _seed_pack(pack_id: str):
    pack = seed_capability_packs().get(pack_id)
    if pack is None:
        raise ValueError(f"unknown capability pack: {pack_id}")
    return pack


def _seed_adapter(adapter_id: str):
    adapter = seed_adapter_manifests().get(adapter_id)
    if adapter is None:
        raise ValueError(f"unknown capability adapter: {adapter_id}")
    return adapter


def _seed_scenarios(scenario_ids: list[str]):
    scenarios = seed_scenario_specs()
    selected = []
    for scenario_id in scenario_ids:
        scenario = scenarios.get(scenario_id)
        if scenario is None:
            raise ValueError(f"unknown capability scenario: {scenario_id}")
        selected.append(scenario)
    return selected


__all__ = ["run_capability_pack_deterministic_gates"]
