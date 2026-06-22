"""Hardened, object-returning Python sandbox (subprocess-isolated).

This is a deliberately *lower-level* sibling to the contract-based
``PythonExecutorBackend`` family. Those return a JSON-only
``CodeAgentExecutionResult`` (a ``result_repr`` string at most); this primitive
returns the actual result **object** (e.g. a pandas ``DataFrame``) so the caller
can apply its own serialization.

Isolation model (see ``docs/archive/2026-06/python-sandbox-design-2026-06-04.md``):

* code runs in a fresh ``spawn`` subprocess — a sandbox escape is confined to a
  throwaway process, not the host (which may hold DB creds / object-store keys);
* wall-clock timeout enforced parent-side (kill on overrun);
* ``RLIMIT_AS`` / ``RLIMIT_CPU`` caps inside the worker (best-effort, Unix);
* network disabled (``socket`` creation raises) — the main exfil vector;
* user-code builtins are full builtins minus ``open/exec/eval/compile/input/
  breakpoint/__import__`` (replaced by an allowlisted importer). Library internals
  keep their real builtins, so pandas/numpy are not crippled.

Generous builtins are acceptable precisely because the *process* is the security
boundary here, not an in-process AST allowlist.
"""

from __future__ import annotations

import ast
import contextlib
import io
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from multiprocessing import get_context
from queue import Empty
from time import monotonic
from typing import Any

logger = logging.getLogger(__name__)

# Default result-variable lookup order (matches excel_ai's pandas convention).
DEFAULT_RESULT_VARS: tuple[str, ...] = ("result", "output", "data", "df", "results")

# Builtins withheld from user code. OS isolation is the real boundary; this only
# keeps parity with the prior in-process sandbox's FS/exec posture.
_BLOCKED_BUILTINS = frozenset(
    {"open", "exec", "eval", "compile", "input", "breakpoint", "help", "__import__"}
)


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    """Execution caps for one sandboxed run.

    Defaults are sized for pandas analytics (not the 2s agent-action default).
    """

    max_exec_seconds: float = 30.0
    max_memory_mb: int = 4096  # RLIMIT_AS; generous to avoid spurious numpy MemoryError
    max_cpu_seconds: int = 30  # RLIMIT_CPU backstop to the wall-clock join
    max_output_chars: int = 10_000


@dataclass(slots=True)
class SandboxResult:
    """Outcome of a sandboxed run with the real result object."""

    result: Any = None
    stdout: str = ""
    stderr: str = ""
    elapsed_ms: int = 0
    truncated_output: bool = False
    result_repr_only: bool = False  # result wasn't picklable → ``result`` is a repr str


class SandboxError(RuntimeError):
    """Base class for sandbox failures."""


class SandboxTimeoutError(SandboxError):
    """Raised when execution exceeds the wall-clock limit."""


class SandboxPolicyError(SandboxError):
    """Raised on an unauthorized import or similar policy violation."""


def _split_exec_and_last_expr(code: str) -> tuple[ast.Module, ast.Expression | None]:
    """Split a module so the final bare expression can be captured as a value."""
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


def _build_sandbox_builtins(authorized_imports: frozenset[str]) -> dict[str, Any]:
    """Full builtins minus dangerous names, with an allowlisted ``__import__``."""
    import builtins as py_builtins

    namespace: dict[str, Any] = {
        name: getattr(py_builtins, name)
        for name in dir(py_builtins)
        if not name.startswith("_") and name not in _BLOCKED_BUILTINS
    }
    # Dunder builtins legitimately needed by user code (class defs etc.).
    namespace["__build_class__"] = py_builtins.__build_class__
    namespace["__name__"] = "__sandbox__"

    def _safe_import(
        name: str,
        globals_: dict[str, Any] | None = None,
        locals_: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        root = name.split(".", 1)[0]
        if root not in authorized_imports:
            raise ImportError(f"unauthorized import '{root}'")
        return py_builtins.__import__(name, globals_, locals_, fromlist, level)

    namespace["__import__"] = _safe_import
    return namespace


def _install_hardening(limits: SandboxLimits) -> None:
    """Apply rlimits and disable network inside the worker (best-effort)."""
    try:
        import resource

        if limits.max_memory_mb > 0:
            cap = limits.max_memory_mb * 1024 * 1024
            with contextlib.suppress(Exception):
                resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
        if limits.max_cpu_seconds > 0:
            with contextlib.suppress(Exception):
                resource.setrlimit(
                    resource.RLIMIT_CPU,
                    (limits.max_cpu_seconds, limits.max_cpu_seconds + 1),
                )
    except Exception:  # pragma: no cover - resource is Unix-only
        pass

    # Block network: the principal exfiltration vector. No root/namespaces needed.
    try:
        import socket

        def _blocked(*_args: Any, **_kwargs: Any) -> Any:
            raise OSError("network access is disabled in the sandbox")

        socket.socket = _blocked  # type: ignore[assignment,misc]
        with contextlib.suppress(Exception):
            socket.create_connection = _blocked  # type: ignore[assignment]
    except Exception:  # pragma: no cover
        pass


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _sandbox_worker(payload: dict[str, Any], queue: Any) -> None:
    """Child-process entrypoint: harden, run user code, push the result object."""
    limits: SandboxLimits = payload["limits"]
    _install_hardening(limits)

    code = str(payload.get("code", ""))
    initial_state = dict(payload.get("initial_state") or {})
    result_vars: tuple[str, ...] = tuple(payload.get("result_vars") or ())
    authorized = frozenset(payload.get("authorized_imports") or ())

    namespace: dict[str, Any] = dict(initial_state)
    namespace["__builtins__"] = _build_sandbox_builtins(authorized)

    stdout, stderr = io.StringIO(), io.StringIO()
    started = monotonic()
    try:
        module_ast, tail_expr = _split_exec_and_last_expr(code)
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            if module_ast.body:
                exec(compile(module_ast, "<sandbox>", "exec"), namespace)  # noqa: S102
            tail_value = None
            if tail_expr is not None:
                tail_value = eval(  # noqa: S307
                    compile(tail_expr, "<sandbox-expr>", "eval"), namespace
                )
    except BaseException as exc:  # noqa: BLE001 - report MemoryError/SystemExit too
        with contextlib.suppress(Exception):
            queue.put({"error": f"{type(exc).__name__}: {exc}"})
        return

    elapsed_ms = int((monotonic() - started) * 1000)

    chosen: Any = None
    for name in result_vars:
        if name in namespace:
            chosen = namespace[name]
            break
    else:
        chosen = tail_value

    # The result object crosses the Queue (pickled). Non-picklable → repr fallback.
    result_repr_only = False
    result_payload: Any = chosen
    try:
        import pickle

        pickle.dumps(chosen)
    except Exception:
        result_payload = repr(chosen)
        result_repr_only = True

    out, out_tr = _truncate(stdout.getvalue(), limits.max_output_chars)
    err, err_tr = _truncate(stderr.getvalue(), limits.max_output_chars)
    message = {
        "result": result_payload,
        "result_repr_only": result_repr_only,
        "stdout": out,
        "stderr": err,
        "truncated_output": out_tr or err_tr,
        "elapsed_ms": elapsed_ms,
    }
    try:
        queue.put(message)
    except Exception:  # serialization at put-time failed → repr fallback
        message["result"] = repr(chosen)
        message["result_repr_only"] = True
        with contextlib.suppress(Exception):
            queue.put(message)


def _kill_process(process: Any) -> None:
    with contextlib.suppress(Exception):
        process.terminate()
        process.join(timeout=0.2)
    if process.is_alive():
        with contextlib.suppress(Exception):
            process.kill()
            process.join(timeout=0.2)


def run_sandboxed(
    code: str,
    *,
    initial_state: dict[str, Any] | None = None,
    result_vars: Sequence[str] = DEFAULT_RESULT_VARS,
    authorized_imports: Iterable[str] = (),
    limits: SandboxLimits | None = None,
) -> SandboxResult:
    """Run ``code`` in a hardened subprocess and return the result object.

    Args:
        code: Python source. The chosen result is the first of ``result_vars``
            present in the namespace, else the value of the final bare expression.
        initial_state: variables pre-injected into the namespace (must be
            picklable to cross to the child — e.g. ``{"parquet_path": "/x.parquet"}``).
        result_vars: ordered names to look up for the result object.
        authorized_imports: root module names the user code may import.
        limits: execution caps (defaults sized for pandas analytics).

    Raises:
        SandboxTimeoutError: wall-clock limit exceeded.
        SandboxPolicyError: unauthorized import.
        SandboxError: any other in-sandbox failure.
    """
    limits = limits or SandboxLimits()
    authorized = frozenset(
        str(item).strip() for item in authorized_imports if str(item).strip()
    )

    ctx = get_context("spawn")
    queue = ctx.Queue()
    process = ctx.Process(
        target=_sandbox_worker,
        args=(
            {
                "code": code,
                "initial_state": dict(initial_state or {}),
                "result_vars": tuple(result_vars),
                "authorized_imports": sorted(authorized),
                "limits": limits,
            },
            queue,
        ),
    )
    started = monotonic()
    process.start()

    # Read BEFORE join so we don't race the Queue feeder thread on large payloads.
    payload: dict[str, Any] | None = None
    try:
        payload = queue.get(timeout=limits.max_exec_seconds)
    except Empty:
        _kill_process(process)
        raise SandboxTimeoutError("execution time limit exceeded") from None
    finally:
        process.join(timeout=1.0)
        if process.is_alive():
            _kill_process(process)

    if not isinstance(payload, dict):
        raise SandboxError("sandbox returned no payload")

    error = payload.get("error")
    if isinstance(error, str) and error:
        if "unauthorized import" in error:
            raise SandboxPolicyError(error)
        raise SandboxError(error)

    return SandboxResult(
        result=payload.get("result"),
        stdout=str(payload.get("stdout") or ""),
        stderr=str(payload.get("stderr") or ""),
        elapsed_ms=int(payload.get("elapsed_ms") or (monotonic() - started) * 1000),
        truncated_output=bool(payload.get("truncated_output")),
        result_repr_only=bool(payload.get("result_repr_only")),
    )


__all__ = [
    "DEFAULT_RESULT_VARS",
    "SandboxError",
    "SandboxLimits",
    "SandboxPolicyError",
    "SandboxResult",
    "SandboxTimeoutError",
    "run_sandboxed",
]
