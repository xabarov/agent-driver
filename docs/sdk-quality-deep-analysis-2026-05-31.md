# Agent Driver SDK Quality Decision Record

Status: reference / compacted. Unified Work Plan phases 3 and 7 closed the SDK
P0/P1 productization work. Use [SDK](sdk.md) for current developer guidance and
[Unified Work Plan](unified-work-plan-2026-05-31.md) for remaining roadmap.

Date: 2026-05-31.

## Decision

The SDK should feel like a product surface, not a thin import path into
`runtime.single_agent`.

Closed SDK principles:

- app-facing code should start from `Agent`, top-level `query`, `Session`,
  `RunHandle` and stream helpers;
- low-level runner wiring stays available as an advanced escape hatch;
- provider errors, request ids, timeout/retry config and trace summaries should
  be typed enough for backend products;
- custom tools should be easy for simple cases while preserving governed
  manifests for risky tools;
- public SDK fields should come from deliberate contract mappings, not raw
  internal metadata.

## Closed Work

- `Agent.query` and top-level `query`.
- `Session` facade for send, stream, resume, history, runs, start and fork.
- `RunHandle` and object-oriented stream helper.
- SDK import isolation tests.
- Typed provider errors with provider request ids where available.
- `SdkTransportConfig` timeout/retry defaults.
- Custom `tool(...)` helper, docstring/signature defaults and catalog
  projections.
- Stable `TraceSummary`, `summarize_output(...)` and support-bundle helpers.
- SDK-visible context pressure/recommendation diagnostics.
- SDK docs:
  `docs/sdk.md`, `docs/sdk-sessions.md`, `docs/sdk-tools.md`,
  `docs/sdk-streaming.md`, `docs/sdk-errors.md`.
- README quick start rewritten around SDK entrypoints.

## Remaining Optional Product Work

Only one SDK-area item remains active in the unified plan:

- decide whether to build an in-process tool-server helper or
  OpenAI-compatible managed-agent gateway with SSE/tool-progress support.

This is useful, but not required for the current SDK to be usable. If deferred,
record the deferral in `docs/roadmap.md` so it does not keep reappearing as an
implicit promise.

## Quality Bar

The SDK remains good when:

- a backend can build chat endpoints without importing `runtime.single_agent`;
- sessions, streaming, resume/fork and trace summaries work through public
  SDK contracts;
- provider failures surface typed errors and useful request diagnostics;
- examples use SDK entrypoints first and internal runner APIs only in advanced
  sections.
