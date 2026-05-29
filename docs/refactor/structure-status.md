# Structure refactor status

Last reviewed: **2026-05-19** (post wave: runtime decomposition, facade cleanup, test harness).

Legend: **[DONE]** completed in repo · **[OPEN]** planned · **[PARTIAL]** started, acceptance not met.

Size heuristic: modules **> ~400 LOC** under `agent_driver/` should be split or tracked here.

---

## Summary

| Area | Done | Open / partial |
|------|------|----------------|
| `runtime/single_agent/` | Phase modules, config sections, thin `steps.py` | `resume.py` still large; optional `llm_step` trim |
| `runtime/` facade | Tools/subagents removed from `__init__.py` | Flat `sqlite_store` / `postgres_store` vs `storage/` |
| `subagents/` | `handoff.py`, slimmer `executor.py` | — |
| `code_agent/` | `execution_common`, `stage_planning`, public `runner.deps/config` | `policy.py` locals |
| `context/` | `projection_input`, `token_pressure`, `ObservationMemoryInput`, split deterministic trimming helpers | optional `tools/planning.py` subpackage |
| `tools/builtin/` | Registry facade + filesystem split package | watchlist only (`web.py`, `tasking.py`) |
| Tests | `live_smoke/`, `tests/support/`, restored `resume_edit`/`todo_write`/`ask_user_question` lanes | parametrize unit/governed filesystem matrix |

---

## [DONE] `agent_driver.runtime.single_agent`

**Was:** `steps.py` ~735 LOC, pylint `too-many-branches`; `output.py` `too-many-locals`; `llm.py` many arguments.

**Now:**

| Module | Role |
|--------|------|
| `steps.py` | Step dispatcher + thin glue (~175 LOC) |
| `llm_step.py` | LLM call, microcompact, trim metadata |
| `tool_stage.py` | Tool stage transitions |
| `compaction_stage.py` | Compaction orchestration |
| `subagent_stage.py` | Subagent fan-out after tools |
| `step_observations.py` | Observation extraction |
| `step_planning.py` | Planning tool updates |
| `step_events.py` | Shared `_emit` helper |
| `output_builders.py` | Output assembly helpers |
| `config_sections.py` | `TrimmingSettings`, `CompactionSettings`, … |
| `types.py` | `RunnerConfig` with legacy kwargs + nested sections |

**Acceptance:** No `too-many-branches` / `too-many-locals` on `steps.py` / `output.py` / `llm.py`; targeted runtime tests green.

---

## [DONE] `agent_driver.runtime` public surface

**Was:** `runtime/__init__.py` re-exported `ToolRegistry`, `GovernedToolExecutor`, `SubagentGroupSpec`, …

**Now:** `runtime/__init__.py` exports runner, stores factory, checkpoint/event helpers, `wrap_governed_executor` only.

**Acceptance:** `tests/contracts/test_public_exports.py` enforces runtime-only exports; tools/subagents imported from `agent_driver.tools` / `agent_driver.subagents`.

---

## [DONE] `agent_driver.subagents`

| Change | Detail |
|--------|--------|
| `handoff.py` | `SubagentParentHandoff` replaces 10+ `parent_*` kwargs |
| `executor.py` | `_run_single_child_task` helper; no pylint `too-many-locals` on group entry |

**Acceptance:** Subagent unit/integration tests pass; no `execute_subagent_group_sync(parent_run_id=...)` at call sites.

---

## [DONE] `agent_driver.code_agent`

| Change | Detail |
|--------|--------|
| `execution_common.py` | Shared sandbox helpers, `CodeExecutionRequest` |
| `stage_planning.py` | Tool approval planning extracted from `profile.py` |
| `runner.deps` / `runner.config` | Removed `protected-access` from profile |
| Executors | File-level `exec-used` removed; `noqa` on `exec()` lines only |

**Acceptance:** `tests/runtime/test_code_agent_*`, `tests/code_agent/*` pass.

---

## [DONE] `agent_driver.context`

| Item | Status |
|------|--------|
| `projection_input.py` + `build_memory_projection(inp)` | **[DONE]** |
| `token_pressure.py` + `TokenPressureInput` | **[DONE]** |
| `observations/memory.py` `too-many-arguments` | **[DONE]** — `ObservationMemoryInput` + wrapper path |
| `trimming/deterministic.py` `too-many-locals` | **[DONE]** — split into observation/message/max-message stages |

Subpackages already healthy: `compaction/`, `planning/`, `artifacts/`, `sessions/`, `trimming/`, `observations/`.

---

## [DONE] `agent_driver.tools.builtin` — filesystem split

**Was:** [`agent_driver/tools/builtin/filesystem.py`](../../agent_driver/tools/builtin/filesystem.py) **~795 LOC** — six tools + notebook logic in one file.

**Now:** subpackage `agent_driver/tools/builtin/filesystem/`:

```
filesystem/
  __init__.py      # register_filesystem_tools()
  read.py          # read_file, list_dir
  write.py         # file_write, file_edit
  search.py        # glob, grep
  notebook.py      # notebook_edit
  _paths.py        # shared path policy helpers (if any)
```

**Acceptance:** `read/write/search/notebook/_paths` modules in place, behavior unchanged in `tests/tools/test_builtin_filesystem_tools.py`, and governed integration uses shared harness setup.

**Follow-up:** keep `web` / `tasking` under watchlist if they cross ~400 LOC.

---

## [OPEN] `agent_driver.tools` — planning module

**Issue:** [`tools/planning.py`](../../agent_driver/tools/planning.py) ~234 LOC mixes tool handlers and `apply_planning_state_tool_update` bridge to `context`.

**Proposal:**

- `tools/planning/tools.py` — handlers + registration;
- `tools/planning/state_bridge.py` — `apply_planning_state_tool_update` (or keep bridge in `context/planning/`).

**Acceptance:** Clear import direction `tools → context` only; tests in `tests/tools/test_planning_tools.py` pass.

**Scope:** ~½ session (small).

---

## [OPEN] `agent_driver.runtime` — store layout

**Issue:** Parallel implementations:

- Package: `runtime/storage/` (factory, protocols, payloads, postgres SQL)
- Flat: `runtime/sqlite_store.py`, `runtime/postgres_store.py`, `runtime/checkpoints.py`, `runtime/checkpoint_factory.py`

**Proposal:** Move sqlite/postgres store implementations under `runtime/storage/sqlite.py`, `runtime/storage/postgres.py`; keep thin facades or delete flat modules in the same change (no shims per roadmap policy).

**Acceptance:** Single import path for store creation; conformance tests (`test_storage_conformance`, `test_store_factory`) pass.

**Scope:** ~1–2 sessions (medium–high; touch persistence carefully).

---

## [OPEN] Cognitive: `runtime/tools.py` vs `agent_driver.tools`

**Issue:** `agent_driver/runtime/tools.py` defines `ToolExecutor` protocol and `wrap_governed_executor` — name collides with the `tools` package.

**Proposal (later):** rename to `runtime/tool_execution.py` or `runtime/execution_bridge.py` when touching imports anyway.

**Scope:** ~½ session + grep-driven import updates.

---

## [PARTIAL] Tests layout

### [DONE]

| Item | Location |
|------|----------|
| Live smoke split | `tests/runtime/live_smoke/{test_basic,test_filesystem,test_interrupt_resume,test_tasking_mcp}.py` |
| Live harness | `tests/support/live_harness.py` |
| Subagent test helper | `tests/subagents/parent_handoff.py` |
| Package marker | `tests/__init__.py`, `pyproject.toml` `pythonpath = ["."]` |

### [OPEN]

| Item | Scope | Notes |
|------|-------|-------|
| Restore live scenarios | Done | `resume_edit`, `todo_write`, `ask_user_question` covered in split live smoke suite |
| Use `governed_tool_harness.py` | Done | filesystem governed integration tests now use shared harness helpers |
| Parametrize unit/governed filesystem | Medium | Reduce duplication across `tests/tools/` and `tests/runtime/test_tool_governance_filesystem_tools.py` |

**Current live smoke count:** 13 tests collected (2 + 4 + 4 + 3).

---

## [OPEN] Facade “god” `__init__.py` files (low priority)

| Module | ~LOC | Note |
|--------|------|------|
| `contracts/__init__.py` | 170 | Re-export surface; split only if it blocks navigation |
| `context/__init__.py` | 77 | Acceptable facade for Phase 6 consumers |

---

## Package map (target state)

```text
agent_driver/
  runtime/
    single_agent/     # step loop [DONE structure]
    storage/          # stores [OPEN: absorb sqlite/postgres]
    runner.py
    ...
  tools/
    builtin/
      filesystem/     # [DONE split]
      registry.py
    executor/
    planning/         # [OPEN optional]
  subagents/          # [DONE handoff]
  context/            # [DONE input objects + trim split]
  code_agent/         # [DONE common + stage_planning]
  contracts/          # enums + models (stable)
  evals/
  observability/
  llm/
tests/
  support/            # shared harnesses [DONE]
  runtime/live_smoke/ # [DONE restored scenarios]
```

---

## Verification

| Gate | Command |
|------|---------|
| Default CI-style | `AGENT_DRIVER_RUN_LIVE_TESTS=0 .venv/bin/pytest tests/ -q --ignore=tests/llm` |
| Pylint (hot packages) | `.venv/bin/pylint agent_driver/runtime/single_agent agent_driver/subagents agent_driver/code_agent agent_driver/context/projections.py agent_driver/context/token_pressure.py --fail-under=8.0` |
| Live (optional) | `AGENT_DRIVER_RUN_LIVE_TESTS=1 .venv/bin/pytest tests/runtime/live_smoke -q` |

---

## Suggested next sessions (priority)

1. **[OPEN]** Phase 10 kickoff: `agent_driver.sdk`, `contracts/stream`, `runtime/stream`.
2. **[OPEN]** `runtime/storage/` consolidation (plan carefully, one backend at a time).
3. **[OPEN]** Optional `tools/planning.py` subpackage split (readability/import direction).
4. **[OPEN]** Parametrize unit/governed filesystem matrix to reduce test duplication.
