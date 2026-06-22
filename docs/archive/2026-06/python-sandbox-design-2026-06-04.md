# Hardened Python sandbox — object-returning primitive (2026-06-04)

## Why

excel_ai's `sandbox_execute_pandas` runs LLM-generated pandas code via
smolagents' `evaluate_python_code` **in the backend process**. Two problems:

1. **Dependency** — `smolagents` is pinned solely for this evaluator.
2. **Security** — an in-process AST sandbox (smolagents, RestrictedPython) is
   best-effort; an escape = backend RCE (Postgres creds, MinIO keys, network).

Goal: move execution into an engine-owned, OS-isolated subprocess so excel_ai
drops smolagents AND the worst-case escape is confined to a throwaway process.

## What already exists in agent-driver

`agent_driver/code_agent/` has a pluggable executor subsystem:
`PythonExecutorBackend` Protocol, `LocalPythonBackend` (session-persistent spawn
worker), the live builtin `python` tool. BUT those return a JSON contract
(`CodeAgentExecutionResult`, `metadata` is `ensure_json_serializable`) — they
return a `result_repr` string, never the actual object. excel_ai needs the real
`DataFrame`/`Series`/scalar back to apply its own row/col-limited serialization.

So we add a **sibling low-level primitive**, not a new contract backend.

## New module: `agent_driver/code_agent/sandbox.py`

```python
@dataclass(frozen=True)
class SandboxLimits:
    max_exec_seconds: float = 30.0      # wall clock (excel pandas, not the 2s agent default)
    max_memory_mb: int = 2048           # RLIMIT_AS
    max_cpu_seconds: int = 30           # RLIMIT_CPU
    max_output_chars: int = 10_000

@dataclass
class SandboxResult:
    result: Any                          # the chosen result object (unpickled in parent)
    stdout: str
    stderr: str
    elapsed_ms: int
    truncated_output: bool

def run_sandboxed(
    code: str, *,
    initial_state: dict[str, Any] | None = None,   # e.g. {"parquet_path": "..."} — must be picklable
    result_vars: Sequence[str] = ("result", "output", "data", "df", "results"),
    authorized_imports: Iterable[str] = (),
    limits: SandboxLimits = SandboxLimits(),
) -> SandboxResult: ...
```

- spawn (`multiprocessing.get_context("spawn")`) worker; parent `join(timeout)` →
  terminate/kill on overrun → raise `SandboxTimeoutError`.
- Worker result selection: first present name in `result_vars`, else value of the
  last top-level expression (split via `ast`, like `LocalPythonBackend`).
- Result object crosses the Queue (pickled). Worker is memory-capped, so it can't
  build an unbounded object; parent unpickles. Non-picklable result → `repr` string
  + flag.

## Isolation model (the security win)

Applied in the spawned worker before running user code (`_install_sandbox_hardening`):

- **Memory**: `resource.setrlimit(RLIMIT_AS, max_memory_mb)`.
- **CPU**: `resource.setrlimit(RLIMIT_CPU, max_cpu_seconds)` (SIGXCPU backstop to the
  wall-clock join).
- **Wall clock**: parent-side `join(timeout)` + kill.
- **Network**: monkeypatch `socket.socket` to raise — blocks the main exfil vector
  with no root/namespaces needed.
- **Builtins**: full builtins MINUS `open, exec, eval, compile, input, breakpoint,
  __import__` (replaced by an allowlisted importer). Matches smolagents' FS/exec
  posture so we don't regress, while OS isolation does the heavy lifting. Generous
  builtins are safe because the *process* is the boundary, not the AST allowlist.
- **Imports**: allowlisted `__import__` (root module must be in `authorized_imports`),
  reusing the `execution_common.build_safe_builtins` idea.

Graceful degradation: `resource`/`setrlimit` failures are logged, not fatal
(non-Linux dev). The backend container is Linux, where all limits apply.

**Out of scope (follow-up):** full filesystem jail (needs nsjail/bubblewrap
namespaces) and the `docker`/`e2b`/`wasm` contract backends. Network-block + rlimits
+ process isolation is a strict improvement over today's in-process eval.

## excel_ai migration

`pandas_tool.py`: behind env flag `EXCEL_PYTHON_EXECUTOR` (`smolagents` default →
`engine`), replace the `evaluate_python_code` call with `run_sandboxed(code,
initial_state={"parquet_path": ...}, authorized_imports=..., limits=...)` and feed
`SandboxResult.result` into the existing `serialize_exec_result` path. smolagents
stays as the fallback until parity is proven on real tasks; then flip default and
remove the dep from `requirements.txt`/`setup.py` + the import.

Rollback = one env var. No engine consumers of the existing backends change.
