"""Shared helpers for in-process and subprocess code executors."""

from __future__ import annotations

import io
from dataclasses import dataclass
from time import monotonic
from typing import Any

from agent_driver.code_agent.contracts import (
    CodeAgentAction,
    CodeAgentExecutionResult,
    CodeAgentFinalAnswer,
    CodeAgentLimits,
    CodeAgentObservation,
)
from agent_driver.code_agent.policy import validate_code_action
from agent_driver.code_agent.serialization import serialize_payload
from agent_driver.contracts.serialization import ExecutorSerializationPolicy

SAFE_BUILTINS = {
    "len": len,
    "sum": sum,
    "min": min,
    "max": max,
    "range": range,
    "enumerate": enumerate,
    "sorted": sorted,
    "abs": abs,
    "print": print,
}


class CodeExecutionError(RuntimeError):
    """Raised when sandbox policy or execution fails."""


@dataclass(frozen=True, slots=True)
class CodeExecutionRequest:
    """Normalized inputs for code action execution."""

    action: CodeAgentAction
    limits: CodeAgentLimits
    authorized_imports: set[str]
    serialization_policy: ExecutorSerializationPolicy | None = None


def validate_or_raise(request: CodeExecutionRequest) -> None:
    """Run static validation and raise on policy violations."""
    violations = validate_code_action(
        code=request.action.code,
        limits=request.limits,
        authorized_imports=request.authorized_imports,
    )
    if violations:
        raise CodeExecutionError("; ".join(item.message for item in violations))


def build_exec_environments(
    tool_wrappers: dict[str, object],
) -> tuple[dict[str, object], dict[str, object], dict[str, str]]:
    """Build restricted globals/locals for exec and final_answer holder."""
    final_answer_holder: dict[str, str] = {}

    def _final_answer(value: object) -> str:
        text = str(value)
        final_answer_holder["text"] = text
        return text

    locals_env: dict[str, object] = {
        "final_answer": _final_answer,
        **tool_wrappers,
    }
    globals_env: dict[str, object] = {"__builtins__": SAFE_BUILTINS}
    return globals_env, locals_env, final_answer_holder


def observations_from_streams(
    *,
    stdout: io.StringIO,
    stderr: io.StringIO,
    limits: CodeAgentLimits,
) -> list[CodeAgentObservation]:
    """Build bounded observations from captured stdout/stderr."""
    observations: list[CodeAgentObservation] = []
    for source, raw_text in (("stdout", stdout.getvalue()), ("stderr", stderr.getvalue())):
        if not raw_text:
            continue
        truncated = len(raw_text) > limits.max_output_chars
        preview = raw_text[: limits.max_output_chars]
        if truncated:
            preview += "..."
        observations.append(
            CodeAgentObservation(
                source=source,
                text_preview=preview,
                truncated=truncated,
                metadata={"original_length": len(raw_text)},
            )
        )
    return observations


def build_execution_result(
    *,
    request: CodeExecutionRequest,
    elapsed_ms: int,
    observations: list[CodeAgentObservation],
    final_answer_holder: dict[str, str],
    planned_calls: list[dict[str, object]],
    executor_mode: str,
) -> CodeAgentExecutionResult:
    """Map execution artifacts into CodeAgentExecutionResult."""
    final_answer = (
        CodeAgentFinalAnswer(
            text=final_answer_holder["text"],
            source="helper",
            metadata={"action_id": request.action.action_id},
        )
        if "text" in final_answer_holder
        else None
    )
    payload = serialize_payload(
        {"planned_tool_calls": planned_calls}, request.serialization_policy
    )
    return CodeAgentExecutionResult(
        final_answer=final_answer,
        observations=observations,
        tool_results=planned_calls,
        metadata={"elapsed_ms": elapsed_ms, "executor_mode": executor_mode, **payload},
    )


def enforce_elapsed_limit(*, elapsed_ms: int, limits: CodeAgentLimits) -> None:
    """Raise when execution exceeded configured wall time."""
    if elapsed_ms > limits.max_exec_ms:
        raise CodeExecutionError("execution time limit exceeded")


__all__ = [
    "CodeExecutionError",
    "CodeExecutionRequest",
    "SAFE_BUILTINS",
    "build_exec_environments",
    "build_execution_result",
    "enforce_elapsed_limit",
    "observations_from_streams",
    "validate_or_raise",
]
