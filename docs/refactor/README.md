# Refactor backlog and quality rules (`agent-driver`)

This folder tracks **structural** refactors (layout, boundaries, complexity) separately from feature work in [`docs/roadmap.md`](../roadmap.md).

Canonical layout policy: [`.cursor/rules/repo-structure.mdc`](../../.cursor/rules/repo-structure.mdc) and roadmap § *Repository structure policy*.

## Documents

| File | Purpose |
|------|---------|
| [structure-status.md](structure-status.md) | Package map: **done** vs **open**, with scope and acceptance |
| [pylint-suppressions-baseline.md](pylint-suppressions-baseline.md) | Pylint disables: removed targets, remaining acceptable vs follow-up |

## Principles (no cheating, no sloppiness)

### 1. Fix structure, not the linter

- Do **not** add `# pylint: disable=too-many-lines`, `duplicate-code`, `too-many-branches`, `too-many-locals`, or `too-many-arguments` to “make CI green”.
- Prefer: **extract module**, **value object / dataclass input**, **parameterized tests**, **shared test harness** — then remove the disable.
- Acceptable disables (use sparingly, one line, with a short comment if non-obvious):
  - `too-few-public-methods` on **Protocol** or **mixin** that intentionally exposes one hook;
  - `exec-used` / `broad-exception-caught` **only** at sandbox/subprocess boundary;
  - `protected-access` **only** in tests when probing internal state.

### 2. Packages grow by subpackages, not monoliths

- New behavior goes into a **named submodule** under the right package (`runtime/single_agent/`, `tools/builtin/`, `context/compaction/`, …).
- `__init__.py` is a **facade** (`__all__`, re-exports). No business logic in facades.
- If a module approaches **~400 LOC** (Python under `agent_driver/`), plan a split **before** adding large features.
- Do **not** add `agent_driver/foo.py` next to `agent_driver/foo/` (import resolves to the package; sibling `.py` is dead or confusing).

### 3. Clear boundaries between packages

| Package | Owns | Must not own |
|---------|------|----------------|
| `agent_driver.runtime` | Runner, checkpoints, events, store wiring, step orchestration | Tool registry, governed executor, subagent merge logic |
| `agent_driver.tools` | Registry, policy, guardrails, builtins, planning **tools** | Run loop, checkpoint persistence |
| `agent_driver.subagents` | Fan-out, join, merge, handoff, child store | LLM prompts, tool execution |
| `agent_driver.context` | Sessions, artifacts, trimming, compaction orchestration | HTTP adapters |
| `agent_driver.code_agent` | Sandbox, profile stage, code prompt surface | Generic ReAct tool stage |

Import rule: **consumers import from the owning package**, not via `agent_driver.runtime` as a mega-facade for tools/subagents.

### 4. Refactor rhythm

1. **Scoped change** — deliver the feature/fix with minimal diff.
2. **Structure pass** (this folder) — when a module crosses size/complexity thresholds or duplicates appear across test layers.
3. One theme per session when possible (e.g. only `tools/builtin/filesystem/`, only live test harness).

After a structure pass: run `pytest` (see [structure-status.md](structure-status.md) § Verification) and pylint on touched packages.

### 5. Tests mirror production layers

- **Unit** — tool handler / pure helper (`tests/tools/`).
- **Governed** — `GovernedToolExecutor` + policy (`tests/runtime/test_tool_governance_*`).
- **Live** (optional) — `tests/runtime/live_smoke/` + `tests/support/live_harness.py`.

Avoid copying the same scenario three times; use **shared harness** and **parametrize** where behavior is identical.

## Quick verification

```bash
# From repo root, with .venv active
AGENT_DRIVER_RUN_LIVE_TESTS=0 .venv/bin/pytest tests/ -q --ignore=tests/llm
.venv/bin/pylint agent_driver/runtime/single_agent agent_driver/subagents agent_driver/code_agent --fail-under=8.0
```

Live lane (operator, keys required):

```bash
export AGENT_DRIVER_RUN_LIVE_TESTS=1
# set AGENT_DRIVER_OPENAI_* or OPENROUTER_* — see tests/support/live_harness.py
.venv/bin/pytest tests/runtime/live_smoke -q
```

## Wave summary (2026-05 refactor)

| Wave | Status | See |
|------|--------|-----|
| Runtime step decomposition | **Done** | [structure-status.md § single_agent](structure-status.md) |
| Runtime public facade narrowed | **Done** | same |
| Test live smoke split + harness | **Mostly done** | same (3 live scenarios to restore) |
| Code-agent / subagents context objects | **Done** | same |
| Builtin tools subpackages | **Open** | `tools/builtin/filesystem/` |
| Store layer consolidation | **Open** | `runtime/storage/` vs flat stores |
| Remaining complexity pylint | **Partial** | [pylint-suppressions-baseline.md](pylint-suppressions-baseline.md) |
