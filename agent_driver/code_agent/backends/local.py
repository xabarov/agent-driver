"""Stateful local python backend with per-session worker process."""

from __future__ import annotations

import ast
import contextlib
import io
import queue
from dataclasses import dataclass, field
from multiprocessing import get_context
from multiprocessing.queues import Queue
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
    build_safe_builtins,
    CodeExecutionError,
    CodeExecutionRequest,
    validate_or_raise,
)
from agent_driver.code_agent.serialization import serialize_payload
from agent_driver.contracts.serialization import ExecutorSerializationPolicy

_QUEUE_SLICE_SECONDS = 0.05


@dataclass(slots=True)
class _SessionWorker:
    session_id: str
    process: Any
    request_queue: Queue
    response_queue: Queue
    request_id: int = 0
    last_used_at: float = 0.0


def _split_exec_and_last_expr(code: str) -> tuple[ast.Module, ast.Expression | None]:
    tree = ast.parse(code)
    body = list(tree.body)
    if body and isinstance(body[-1], ast.Expr):
        expr_node = ast.Expression(body=body[-1].value)
        ast.fix_missing_locations(expr_node)
        module = ast.Module(body=body[:-1], type_ignores=[])
        ast.fix_missing_locations(module)
        return module, expr_node
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    return module, None


def _worker_main(request_queue: Queue, response_queue: Queue) -> None:
    namespace: dict[str, object] = {"__builtins__": build_safe_builtins(set())}
    while True:
        request = request_queue.get()
        if not isinstance(request, dict):
            continue
        if request.get("kind") == "shutdown":
            break
        if request.get("kind") == "ping":
            response_queue.put({"kind": "pong"})
            continue
        if request.get("kind") != "exec":
            continue
        request_id = int(request.get("request_id") or 0)
        code = str(request.get("code") or "")
        max_output_chars = int(request.get("max_output_chars") or 400)
        authorized_imports_raw = request.get("authorized_imports")
        authorized_imports: set[str] = set()
        if isinstance(authorized_imports_raw, list):
            authorized_imports = {
                str(item).strip()
                for item in authorized_imports_raw
                if isinstance(item, str) and item.strip()
            }
        namespace["__builtins__"] = build_safe_builtins(authorized_imports)
        final_answer_holder: dict[str, str] = {}

        def _final_answer(value: object) -> str:
            text = str(value)
            final_answer_holder["text"] = text
            return text

        namespace["final_answer"] = _final_answer
        stdout = io.StringIO()
        stderr = io.StringIO()
        started = monotonic()
        result_repr: str | None = None
        try:
            module_ast, tail_expr = _split_exec_and_last_expr(code)
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                if module_ast.body:
                    exec(
                        compile(module_ast, "<python_tool>", "exec"),  # noqa: S102
                        namespace,
                    )
                if tail_expr is not None:
                    value = eval(
                        compile(tail_expr, "<python_tool_expr>", "eval"),  # noqa: S307
                        namespace,
                    )
                    result_repr = repr(value)
        except Exception as exc:  # noqa: BLE001
            response_queue.put(
                {
                    "request_id": request_id,
                    "error": str(exc),
                    "elapsed_ms": int((monotonic() - started) * 1000),
                }
            )
            continue
        elapsed_ms = int((monotonic() - started) * 1000)
        output_observations: list[dict[str, object]] = []
        for source, raw_text in (("stdout", stdout.getvalue()), ("stderr", stderr.getvalue())):
            if not raw_text:
                continue
            truncated = len(raw_text) > max_output_chars
            preview = raw_text[:max_output_chars]
            if truncated:
                preview += "..."
            output_observations.append(
                {
                    "source": source,
                    "text_preview": preview,
                    "truncated": truncated,
                    "metadata": {"original_length": len(raw_text)},
                }
            )
        response_queue.put(
            {
                "request_id": request_id,
                "elapsed_ms": elapsed_ms,
                "observations": output_observations,
                "final_answer": final_answer_holder.get("text"),
                "result_repr": result_repr,
            }
        )


@dataclass(slots=True)
class LocalPythonBackend:
    """Local session-persistent backend implemented with worker subprocesses."""

    mode: str = "local"
    session_idle_seconds: float = 300.0
    _ctx: Any = field(init=False, repr=False)
    _sessions: dict[str, _SessionWorker] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._ctx = get_context("spawn")
        self._sessions: dict[str, _SessionWorker] = {}

    def _spawn_session(self, session_id: str) -> _SessionWorker:
        request_queue = self._ctx.Queue()
        response_queue = self._ctx.Queue()
        process = self._ctx.Process(
            target=_worker_main,
            args=(request_queue, response_queue),
        )
        process.start()
        request_queue.put({"kind": "ping"})
        try:
            _ = response_queue.get(timeout=2.0)
        except queue.Empty as exc:
            process.terminate()
            process.join(timeout=0.2)
            if process.is_alive():
                process.kill()
                process.join(timeout=0.2)
            raise CodeExecutionError("python worker failed to start") from exc
        worker = _SessionWorker(
            session_id=session_id,
            process=process,
            request_queue=request_queue,
            response_queue=response_queue,
            request_id=0,
            last_used_at=monotonic(),
        )
        self._sessions[session_id] = worker
        return worker

    def _expire_idle_sessions(self) -> None:
        if self.session_idle_seconds <= 0:
            return
        cutoff = monotonic() - self.session_idle_seconds
        stale_ids = [
            item.session_id
            for item in self._sessions.values()
            if item.last_used_at < cutoff
        ]
        for session_id in stale_ids:
            self._terminate_session(session_id)

    def _terminate_session(self, session_id: str) -> None:
        worker = self._sessions.pop(session_id, None)
        if worker is None:
            return
        with contextlib.suppress(Exception):
            worker.request_queue.put_nowait({"kind": "shutdown"})
        worker.process.join(timeout=0.2)
        if worker.process.is_alive():
            worker.process.terminate()
            worker.process.join(timeout=0.2)
        if worker.process.is_alive():
            worker.process.kill()
            worker.process.join(timeout=0.2)

    def _worker_for(self, session_id: str) -> _SessionWorker:
        self._expire_idle_sessions()
        worker = self._sessions.get(session_id)
        if worker is None or not worker.process.is_alive():
            if worker is not None:
                self._terminate_session(session_id)
            worker = self._spawn_session(session_id)
        worker.last_used_at = monotonic()
        return worker

    async def execute(
        self,
        *,
        code: str,
        session_id: str,
        authorized_imports: set[str],
        limits: CodeAgentLimits,
        serialization_policy: ExecutorSerializationPolicy | None,
    ) -> CodeAgentExecutionResult:
        request = CodeExecutionRequest(
            action=CodeAgentAction(action_id=f"python_{session_id}", code=code),
            limits=limits,
            authorized_imports=authorized_imports,
            serialization_policy=serialization_policy,
        )
        validate_or_raise(request)
        worker = self._worker_for(session_id)
        worker.request_id += 1
        request_id = worker.request_id
        started = monotonic()
        worker.request_queue.put(
            {
                "kind": "exec",
                "request_id": request_id,
                "code": code,
                "max_output_chars": limits.max_output_chars,
                "authorized_imports": sorted(authorized_imports),
            }
        )
        timeout_seconds = max(limits.max_exec_ms / 1000.0, _QUEUE_SLICE_SECONDS)
        payload: dict[str, Any] | None = None
        deadline = monotonic() + timeout_seconds
        while monotonic() < deadline:
            remaining = max(deadline - monotonic(), _QUEUE_SLICE_SECONDS)
            try:
                candidate = worker.response_queue.get(timeout=min(remaining, _QUEUE_SLICE_SECONDS))
            except queue.Empty:
                continue
            if isinstance(candidate, dict) and int(candidate.get("request_id") or -1) == request_id:
                payload = candidate
                break
        if payload is None:
            self._terminate_session(session_id)
            raise CodeExecutionError("execution time limit exceeded")
        if not worker.process.is_alive():
            self._terminate_session(session_id)
            raise CodeExecutionError("python worker session exited unexpectedly")
        error = payload.get("error")
        if isinstance(error, str) and error:
            raise CodeExecutionError(error)
        observations = [
            CodeAgentObservation.model_validate(item)
            for item in payload.get("observations", [])
            if isinstance(item, dict)
        ]
        final_answer_raw = payload.get("final_answer")
        final_answer = (
            CodeAgentFinalAnswer(
                text=str(final_answer_raw),
                source="helper",
                metadata={"session_id": session_id},
            )
            if isinstance(final_answer_raw, str)
            else None
        )
        elapsed_ms = int(payload.get("elapsed_ms") or ((monotonic() - started) * 1000))
        serialized = serialize_payload(
            {"planned_tool_calls": []},
            serialization_policy,
        )
        metadata: dict[str, Any] = {
            "elapsed_ms": elapsed_ms,
            "executor_mode": self.mode,
            "session_id": session_id,
            "result_repr": payload.get("result_repr"),
            **serialized,
        }
        return CodeAgentExecutionResult(
            final_answer=final_answer,
            observations=observations,
            tool_results=[],
            metadata=metadata,
        )

    async def close_session(self, session_id: str) -> None:
        self._terminate_session(session_id)

    async def aclose(self) -> None:
        for session_id in list(self._sessions):
            self._terminate_session(session_id)


__all__ = ["LocalPythonBackend"]
