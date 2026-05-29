"""Shared helpers for in-process and subprocess code executors."""

from __future__ import annotations

import builtins as py_builtins
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


def build_safe_builtins(authorized_imports: set[str] | None = None) -> dict[str, object]:
    """Return safe builtins map with restricted __import__."""
    allowlist = {item.strip() for item in (authorized_imports or set()) if item.strip()}

    def _safe_import(
        name: str,
        globals_: dict[str, object] | None = None,
        locals_: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        root = name.split(".", maxsplit=1)[0]
        if root not in allowlist:
            raise CodePolicyError(f"unauthorized import '{root}'")
        return py_builtins.__import__(name, globals_, locals_, fromlist, level)

    payload = dict(SAFE_BUILTINS)
    payload["__import__"] = _safe_import
    return payload


class CodeExecutionError(RuntimeError):
    """Raised when sandbox policy or execution fails."""


class CodePolicyError(CodeExecutionError):
    """Raised when sandbox policy validation fails."""


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
        raise CodePolicyError("; ".join(item.message for item in violations))


def build_exec_namespace(
    tool_wrappers: dict[str, object],
    *,
    authorized_imports: set[str] | None = None,
) -> tuple[dict[str, object], dict[str, str]]:
    """Build restricted shared namespace for exec/eval and final_answer holder."""
    final_answer_holder: dict[str, str] = {}

    def _final_answer(value: object) -> str:
        text = str(value)
        final_answer_holder["text"] = text
        return text

    namespace: dict[str, object] = {
        "__builtins__": build_safe_builtins(authorized_imports),
        "final_answer": _final_answer,
        **tool_wrappers,
    }
    return namespace, final_answer_holder


def build_exec_environments(
    tool_wrappers: dict[str, object],
    *,
    authorized_imports: set[str] | None = None,
) -> tuple[dict[str, object], dict[str, object], dict[str, str]]:
    """Backward-compatible wrapper returning shared namespace as globals/locals."""
    namespace, final_answer_holder = build_exec_namespace(
        tool_wrappers,
        authorized_imports=authorized_imports,
    )
    return namespace, namespace, final_answer_holder


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
    "CodePolicyError",
    "CodeExecutionRequest",
    "SAFE_BUILTINS",
    "build_safe_builtins",
    "build_exec_namespace",
    "build_exec_environments",
    "build_execution_result",
    "enforce_elapsed_limit",
    "observations_from_streams",
    "validate_or_raise",
]
