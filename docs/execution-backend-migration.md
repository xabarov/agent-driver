# Execution-backend migration guide

The `agent_driver.execution` package (EPIC-01..05) is the supported way to run
the built-in `bash`/`read`/`write` (and, for a leased workspace, the full
filesystem tool set) in a host-prepared local or remote environment. This guide
is for hosts still using the legacy per-run execution hooks.

## What is deprecated

The pre-execution-package run-scoped hooks remain available but are **soft-
deprecated** in favor of an injected `ExecutionBackend`:

| Legacy hook | Replacement |
| --- | --- |
| `command_runner_scope(AsyncCommandRunner)` | inject an `ExecutionBackend` (its `run_command`) |
| `fs_io_scope(AsyncFileIO)` | inject an `ExecutionBackend` (its `read_text`/`write_text`) |
| direct local subprocess / disk (no injection) | unchanged — this is the default when no backend is injected |

The scopes are still public and still work (the new adapters use them
internally), so **existing code keeps running**. They will remain for the
documented pre-1.0 deprecation window; new integrations should use a backend.

## Migrating an `AsyncCommandRunner` / `AsyncFileIO` host (e.g. ACP)

A host that already provides the legacy `AsyncCommandRunner` and/or `AsyncFileIO`
can lift them into the new seam with **zero rewrite** using
`CompositeExecutionBackend`, then inject that backend:

```python
from agent_driver.execution import CompositeExecutionBackend
from agent_driver.runtime import RunnerConfig

backend = CompositeExecutionBackend(
    command_runner=my_async_command_runner,  # existing AsyncCommandRunner (or None)
    file_io=my_async_file_io,                # existing AsyncFileIO (or None)
    backend_id="my-host",
)
config = RunnerConfig(execution_backend=backend)   # process default
# ...or per run:  await agent.run(run_input, execution_backend=backend)
```

A missing half is reported truthfully: `CompositeExecutionBackend.capabilities()`
marks a present runner/file-IO `SUPPORTED` and an absent one `UNSUPPORTED` (never
`UNKNOWN`), so capability-gated tools behave correctly.

## Writing a native backend

Implement the `ExecutionBackend` protocol (command + text read/write) and,
optionally, `CapabilityAwareBackend`, `LeaseCapableBackend`,
`WorkspaceCapableBackend`, and `JobCapableBackend` for the capabilities you
genuinely provide. Use **only** `agent_driver.execution` public imports — see
`examples/cookbook/20_execution_backend.py` (surfaces) and
`examples/cookbook/21_backend_compliance.py` (a minimal backend qualified by the
suite).

## Proving what your backend supports

Run the deterministic compatibility suite (no live LLM / Docker / network /
credentials) and publish the report:

```python
from agent_driver.execution import run_compliance, render_markdown

report = await run_compliance(my_backend)
print(render_markdown(report))
assert report.ok  # no FAILED among the groups you advertise
```

The report distinguishes `passed` / `failed` / `unsupported` / `skipped` /
`stale` / `no_claim`. **Advertise only the capabilities the report proves** for
your exact contract + environment revision. A group you do not advertise stays
`no_claim` and never inflates the result; a guarantee you advertise but do not
prove (e.g. hard teardown you only acknowledge) is a `failed`.

## What Agent Driver does NOT certify

Container/VM/tenant/network/secret/image hardening is the external backend's own
concern and belongs in its own test layer (link it as separate evidence). The
deterministic suite proves protocol conformance and the runtime guarantees
(governance ordering, identity/fencing, bounds/redaction, reconnect, cleanup) —
not infrastructure security.
