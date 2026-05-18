# Pylint suppressions baseline

Last updated: **2026-05-19** after structure refactor wave.

Policy: see [README.md](README.md) § Principles — **decompose first**, disable last.

---

## Removed (do not reintroduce)

| Former location | Codes | Resolution |
|-----------------|-------|------------|
| `tests/runtime/test_live_agent_tool_smoke.py` | `too-many-lines`, `duplicate-code` | Split → `tests/runtime/live_smoke/` + `tests/support/live_harness.py` |
| `tests/runtime/test_tool_governance_filesystem_tools.py` | `duplicate-code` | Removed; harness available for future dedup |
| `runtime/single_agent/steps.py` | `too-many-branches` | `tool_stage.py`, helpers |
| `runtime/single_agent/output.py` | `too-many-locals` | `output_builders.py` |
| `runtime/single_agent/llm.py` | `too-many-arguments`, `too-many-locals` | `LlmRequestBuildContext` |
| `subagents/executor.py` | `too-many-locals` | `SubagentParentHandoff`, `_run_single_child_task` |
| `code_agent/executor.py` | `too-many-arguments`, `too-many-locals` (file-level) | `CodeExecutionRequest`, `execution_common.py` |
| `code_agent/subprocess_executor.py` | file-level `exec-used`, many args/locals | Simplified + point `noqa` on `exec()` |
| `code_agent/profile.py` | `too-many-locals`, `protected-access` | `stage_planning.py`, `runner.deps` / `runner.config` |
| `context/projections.py` | `too-many-arguments` | `MemoryProjectionInput` |
| `context/token_pressure.py` | `too-many-arguments` | `TokenPressureInput` |

---

## Remaining — follow-up (fix with structure, not blanket disable)

| File | Code | Proposed fix | Effort |
|------|------|--------------|--------|
| `context/observations/memory.py` | `too-many-arguments` | `ObservationMemoryInput` dataclass | S |
| `context/trimming/deterministic.py` | `too-many-locals` | Extract per-strategy trim functions | M |
| `code_agent/policy.py` | `too-many-locals` | Split validation vs reporting | S |
| `evals/baseline.py` | `too-many-locals` | Extract comparison sections | S |

---

## Remaining — acceptable (no action required unless file grows)

| File | Code | Rationale |
|------|------|-----------|
| `runtime/single_agent/journal.py` | `too-few-public-methods` | Mixin |
| `runtime/single_agent/resume.py` | `too-few-public-methods` | Mixin |
| `runtime/tools.py` | `too-few-public-methods` | Protocol |
| `observability/contracts.py` | `too-few-public-methods` | Protocol |
| `observability/exporters.py` | `too-few-public-methods` | Stub exporter |
| `observability/optional_exporters.py` | `too-few-public-methods` | Optional backends |
| `tests/tools/test_builtin_web_tools.py` | `too-few-public-methods` | Test double |
| `tests/context/test_compaction_orchestrator.py` | `protected-access` | Intentional internal probe |

---

## Watchlist (no pylint disable today — split before adding features)

| File | ~LOC | Trigger |
|------|------|---------|
| `tools/builtin/filesystem.py` | 795 | **Split now** → see [structure-status.md](structure-status.md) |
| `tools/builtin/tasking.py` | 398 | Split if grows past 400 |
| `tools/builtin/web.py` | 368 | Monitor |
| `tools/builtin/shell.py` | 343 | Monitor |
| `runtime/single_agent/resume.py` | 239 | Optional: extract resume command handlers |
| `runtime/single_agent/compaction_stage.py` | 194 | OK if stable |
| `tools/planning.py` | 234 | Optional subpackage |

---

## How to record a new suppression (rare)

1. Attempt decomposition or input object first.
2. If truly unavoidable, allow **one line**, narrowest code only.
3. Add a row to **Follow-up** or **Acceptable** above with rationale.
4. Never use file-level `too-many-lines` / `duplicate-code` in tests — split file or extract harness.
