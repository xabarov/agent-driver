"""Sandboxed code-action executor abstraction."""

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
    CodeAgentLimits,
)
from agent_driver.code_agent.execution_common import (
    CodeExecutionError,
    CodeExecutionRequest,
    build_exec_namespace,
    build_execution_result,
    enforce_elapsed_limit,
    observations_from_streams,
    validate_or_raise,
)
from agent_driver.contracts.serialization import ExecutorSerializationPolicy


class CodeActionExecutor(Protocol):
    """Protocol for code action execution inside CodeAgent profile."""

    async def execute(
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
                    close_fn = getattr(outcome, "close", None)
                    if callable(close_fn):
                        close_fn()
                    raise CodeExecutionError(
                        "async tool handlers are not supported in local code executor"
                    )
                return outcome

            wrappers[name] = _wrapped
        return wrappers, calls

    async def execute(
        self,
        *,
        action: CodeAgentAction,
        limits: CodeAgentLimits,
        authorized_imports: set[str],
        serialization_policy: ExecutorSerializationPolicy | None,
        callable_tools: dict[str, Callable[..., object]],
    ) -> CodeAgentExecutionResult:
        """Execute one code action with static checks and bounded observations."""
        request = CodeExecutionRequest(
            action=action,
            limits=limits,
            authorized_imports=authorized_imports,
            serialization_policy=serialization_policy,
        )
        validate_or_raise(request)
        tool_wrappers, planned_calls = self._build_tool_wrappers(callable_tools)
        namespace, final_answer_holder = build_exec_namespace(
            tool_wrappers,
            authorized_imports=authorized_imports,
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        started = monotonic()
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exec(action.code, namespace)  # noqa: S102
        except Exception as exc:
            raise CodeExecutionError(str(exc)) from exc
        elapsed_ms = int((monotonic() - started) * 1000)
        enforce_elapsed_limit(elapsed_ms=elapsed_ms, limits=limits)
        return build_execution_result(
            request=request,
            elapsed_ms=elapsed_ms,
            observations=observations_from_streams(
                stdout=stdout, stderr=stderr, limits=limits
            ),
            final_answer_holder=final_answer_holder,
            planned_calls=planned_calls,
            executor_mode="in_process",
        )


__all__ = ["CodeActionExecutor", "CodeExecutionError", "FakeRestrictedCodeExecutor"]
