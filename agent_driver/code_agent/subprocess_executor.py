"""Subprocess-backed CodeActionExecutor with hard timeout enforcement."""

from __future__ import annotations

import contextlib
import io
from collections.abc import Callable
from dataclasses import dataclass, field
from multiprocessing import get_context
from time import monotonic
from typing import Any

from agent_driver.code_agent.contracts import (
    CodeAgentAction,
    CodeAgentExecutionResult,
    CodeAgentFinalAnswer,
    CodeAgentLimits,
    CodeAgentObservation,
)
from agent_driver.code_agent.execution_common import (
    SAFE_BUILTINS,
    CodeExecutionError,
    CodeExecutionRequest,
    validate_or_raise,
)
from agent_driver.code_agent.executor import FakeRestrictedCodeExecutor
from agent_driver.contracts.serialization import ExecutorSerializationPolicy


def _worker(payload: dict[str, Any], queue: Any) -> None:
    """Run restricted python action in child process and push result payload."""
    action_code = str(payload.get("code", ""))
    max_output_chars = int(payload.get("max_output_chars", 400))
    final_answer_holder: dict[str, str] = {}

    def _final_answer(value: object) -> str:
        text = str(value)
        final_answer_holder["text"] = text
        return text

    locals_env: dict[str, object] = {"final_answer": _final_answer}
    globals_env: dict[str, object] = {"__builtins__": SAFE_BUILTINS}
    stdout = io.StringIO()
    stderr = io.StringIO()
    started = monotonic()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exec(action_code, globals_env, locals_env)  # noqa: S102
    except Exception as exc:
        queue.put({"error": str(exc)})
        return
    elapsed_ms = int((monotonic() - started) * 1000)
    observations: list[dict[str, Any]] = []
    for source, raw_text in (("stdout", stdout.getvalue()), ("stderr", stderr.getvalue())):
        if not raw_text:
            continue
        truncated = len(raw_text) > max_output_chars
        preview = raw_text[:max_output_chars]
        if truncated:
            preview += "..."
        observations.append(
            {
                "source": source,
                "text_preview": preview,
                "truncated": truncated,
                "metadata": {"original_length": len(raw_text)},
            }
        )
    queue.put(
        {
            "elapsed_ms": elapsed_ms,
            "observations": observations,
            "final_answer": final_answer_holder.get("text"),
        }
    )


@dataclass(slots=True)
class SubprocessRestrictedCodeExecutor:
    """Code executor that enforces wall-clock timeout via child process."""

    _fallback: FakeRestrictedCodeExecutor = field(
        default_factory=FakeRestrictedCodeExecutor
    )

    async def execute(
        self,
        *,
        action: CodeAgentAction,
        limits: CodeAgentLimits,
        authorized_imports: set[str],
        serialization_policy: ExecutorSerializationPolicy | None,
        callable_tools: dict[str, Callable[..., object]],
    ) -> CodeAgentExecutionResult:
        """Execute code action with policy checks and process-level timeout."""
        request = CodeExecutionRequest(
            action=action,
            limits=limits,
            authorized_imports=authorized_imports,
            serialization_policy=serialization_policy,
        )
        validate_or_raise(request)
        if callable_tools:
            fallback = await self._fallback.execute(
                action=action,
                limits=limits,
                authorized_imports=authorized_imports,
                serialization_policy=serialization_policy,
                callable_tools=callable_tools,
            )
            fallback.metadata["executor_mode"] = "local_fallback"
            return fallback

        ctx = get_context("spawn")
        queue = ctx.Queue()
        process = ctx.Process(
            target=_worker,
            args=(
                {
                    "code": action.code,
                    "max_output_chars": limits.max_output_chars,
                },
                queue,
            ),
        )
        started = monotonic()
        process.start()
        process.join(timeout=limits.max_exec_ms / 1000)
        if process.is_alive():
            process.terminate()
            process.join(timeout=0.2)
            if process.is_alive():
                process.kill()
                process.join(timeout=0.2)
            raise CodeExecutionError("execution time limit exceeded")

        elapsed_ms = int((monotonic() - started) * 1000)
        if process.exitcode not in (0, None):
            raise CodeExecutionError("subprocess execution failed")
        if queue.empty():
            raise CodeExecutionError("subprocess returned no payload")
        payload = queue.get()
        error = payload.get("error")
        if isinstance(error, str) and error:
            raise CodeExecutionError(error)

        from agent_driver.code_agent.serialization import serialize_payload

        serialized_meta = serialize_payload(
            {"planned_tool_calls": []}, serialization_policy
        )
        final_answer = payload.get("final_answer")
        observations_raw = payload.get("observations", [])
        observations = [
            CodeAgentObservation.model_validate(item)
            for item in observations_raw
            if isinstance(item, dict)
        ]
        return CodeAgentExecutionResult(
            final_answer=(
                CodeAgentFinalAnswer(
                    text=str(final_answer),
                    source="helper",
                    metadata={"action_id": action.action_id},
                )
                if isinstance(final_answer, str)
                else None
            ),
            observations=observations,
            tool_results=[],
            metadata={
                "elapsed_ms": elapsed_ms,
                "executor_mode": "subprocess",
                **serialized_meta,
            },
        )


__all__ = ["SubprocessRestrictedCodeExecutor"]
