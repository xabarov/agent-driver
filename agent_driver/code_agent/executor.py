"""Sandboxed code-action executor abstraction."""

# pylint: disable=exec-used

from __future__ import annotations

import contextlib
import io
from collections.abc import Callable
from dataclasses import dataclass
from inspect import isawaitable
from time import monotonic
from typing import Protocol

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

_SAFE_BUILTINS = {
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


class CodeActionExecutor(Protocol):  # pylint: disable=too-few-public-methods
    """Protocol for code action execution inside CodeAgent profile."""

    async def execute(  # pylint: disable=too-many-arguments
        self,
        *,
        action: CodeAgentAction,
        limits: CodeAgentLimits,
        authorized_imports: set[str],
        serialization_policy: ExecutorSerializationPolicy | None,
        callable_tools: dict[str, Callable[..., object]],
    ) -> CodeAgentExecutionResult:
        """Execute one code action in a restricted environment."""
        raise NotImplementedError


@dataclass(slots=True)
class FakeRestrictedCodeExecutor:
    """Minimal restricted code executor for offline tests."""

    def _build_tool_wrappers(
        self, callable_tools: dict[str, Callable[..., object]]
    ) -> tuple[dict[str, Callable[..., object]], list[dict[str, object]]]:
        calls: list[dict[str, object]] = []
        wrappers: dict[str, Callable[..., object]] = {}
        for name in sorted(callable_tools):
            handler = callable_tools[name]

            def _wrapped(
                *, _name: str = name, _handler=handler, **kwargs: object
            ) -> object:
                payload = {"tool_name": _name, "args": dict(kwargs)}
                calls.append(payload)
                try:
                    outcome = _handler(kwargs)
                except TypeError:
                    outcome = _handler(**kwargs)
                if isawaitable(outcome):
                    raise CodeExecutionError(
                        "async tool handlers are not supported in local code executor"
                    )
                return outcome

            wrappers[name] = _wrapped
        return wrappers, calls

    async def execute(  # pylint: disable=too-many-arguments,too-many-locals
        self,
        *,
        action: CodeAgentAction,
        limits: CodeAgentLimits,
        authorized_imports: set[str],
        serialization_policy: ExecutorSerializationPolicy | None,
        callable_tools: dict[str, Callable[..., object]],
    ) -> CodeAgentExecutionResult:
        """Execute one code action with static checks and bounded observations."""
        violations = validate_code_action(
            code=action.code,
            limits=limits,
            authorized_imports=authorized_imports,
        )
        if violations:
            raise CodeExecutionError("; ".join(item.message for item in violations))
        tool_wrappers, planned_calls = self._build_tool_wrappers(callable_tools)
        final_answer_holder: dict[str, str] = {}

        def _final_answer(value: object) -> str:
            """Capture final answer in deterministic helper state."""
            text = str(value)
            final_answer_holder["text"] = text
            return text

        locals_env: dict[str, object] = {"final_answer": _final_answer, **tool_wrappers}
        globals_env: dict[str, object] = {"__builtins__": _SAFE_BUILTINS}
        stdout = io.StringIO()
        stderr = io.StringIO()
        started = monotonic()
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                # Restricted builtins + policy checks are applied before execution.
                exec(
                    action.code, globals_env, locals_env
                )  # noqa: S102  # pylint: disable=exec-used
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise CodeExecutionError(str(exc)) from exc
        elapsed_ms = int((monotonic() - started) * 1000)
        if elapsed_ms > limits.max_exec_ms:
            raise CodeExecutionError("execution time limit exceeded")

        observations: list[CodeAgentObservation] = []
        for source, raw_text in (
            ("stdout", stdout.getvalue()),
            ("stderr", stderr.getvalue()),
        ):
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
        final_answer = (
            CodeAgentFinalAnswer(
                text=final_answer_holder["text"],
                source="helper",
                metadata={"action_id": action.action_id},
            )
            if "text" in final_answer_holder
            else None
        )
        payload = serialize_payload(
            {"planned_tool_calls": planned_calls}, serialization_policy
        )
        return CodeAgentExecutionResult(
            final_answer=final_answer,
            observations=observations,
            tool_results=planned_calls,
            metadata={"elapsed_ms": elapsed_ms, **payload},
        )


__all__ = ["CodeActionExecutor", "CodeExecutionError", "FakeRestrictedCodeExecutor"]
