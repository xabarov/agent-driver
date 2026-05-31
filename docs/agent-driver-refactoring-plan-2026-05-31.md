# Agent Driver Refactoring Decision Record

Status: reference / compacted. The original long refactoring checklist was
absorbed by [Unified Work Plan](unified-work-plan-2026-05-31.md) phases 1, 2,
6 and 7. Keep this page as a structural decision record, not an active backlog.

Date: 2026-05-31.

## What Was Closed

The previous refactoring plan targeted the main maintenance risks:

- unowned `context.metadata[...]` state;
- long `runtime/single_agent` stage files;
- provider adapters that mixed payload, parsing, usage and retry quirks;
- trace summary logic growing into one large analyzer;
- public contracts needing snapshot protection before SDK productization.

Closed work:

- `docs/runtime-metadata.md` now owns the metadata map.
- Typed runtime state helpers exist for loop, tool, planning, research,
  streaming and compaction state.
- Contract snapshots cover the public runtime/tool shapes that the SDK relies
  on.
- `runtime/single_agent` was split into lifecycle, LLM step, tool stage,
  finalization, planning and context-management packages while preserving old
  import paths as compatibility shims.
- `GovernedToolExecutor.execute()` now follows an explicit pipeline.
- OpenAI-compatible provider payload and normalization logic moved into the
  `openai_compatible/` package.
- Run-trace analyzers moved into domain modules under `observability/run_trace/`.
- SDK P0/P1 and documentation work were completed on top of those boundaries.

## Remaining Refactor Work

Only these items remain active, and they are tracked in the unified plan:

- **Storage backend convergence**: shared checkpoint payload serialization,
  ordering semantics and backend capability tests.
- **Eval/CLI boundary**: fixture-based long scenarios and a reusable eval
  result contract.
- **Docs ownership hygiene**: keep module ownership and active sequencing in
  short current docs instead of reviving the old phase checklist.

## Guardrails

- New runtime state should go through owned helpers or typed state objects.
- Compatibility shims should be explicit re-exports and should not become the
  place for new behavior.
- File moves should stay separate from behavior changes unless a test-proven
  bug requires both.
- Storage-specific code should own persistence mechanics, not runtime-state
  semantics.
- Any new public SDK field needs an intentional contract mapping, not an
  accidental leak from internal metadata.

## Active Links

- [Unified Work Plan](unified-work-plan-2026-05-31.md)
- [Runtime Metadata Inventory](runtime-metadata.md)
- [Runtime overview](runtime.md)
- [SDK](sdk.md)
