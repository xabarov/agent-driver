# Unified Work Plan

Status: active roadmap / current sequence.

Date: 2026-05-31.

Purpose: keep one short truth for remaining work. Earlier unified phases
0-8 are closed: provider/research baseline, typed runtime state, context
pressure, SDK P0/P1, Skills, Deep Research contracts, chat-demo UX,
structural refactor and docs cleanup.

## Current Verdict

Most of the previous plan is done and should not be reopened from stale
checkboxes in reference docs. The real remaining work is narrower:

1. Make long Deep Research output artifact-first so the model patches a report
   file instead of re-streaming long drafts after todo repair.
2. Turn the new artifact workflow into measured evals and reusable fixtures.
3. Finish storage/eval infrastructure convergence that was intentionally left
   outside the structural refactor.
4. Decide whether to productize an OpenAI-compatible managed-agent gateway or
   in-process tool-server helper.
5. Keep provider/model live-gate practice current without letting old run logs
   become the roadmap.

## Active Inputs

- [Efficient Deep Research Workspace Architecture](efficient-deep-research-workspace-architecture-2026-05-31.md) -
  active design for session workspaces, report artifacts, scoped write tools,
  artifact events, artifact-aware compaction and regression tests.
- [Provider And Model Debugging](provider-model-debugging.md) - active
  live-gate playbook and model matrix practice.
- [Runtime Metadata Inventory](runtime-metadata.md) - current owner map for
  runtime metadata and typed state boundaries.
- [Research Quality Summary](research-quality-improvement-plan-2026-05-31.md) -
  completed baseline and acceptance run IDs.

## Reference Records

These are decision records, not active checklists:

- [Deep Research And Skills Analysis](deep-research-and-skills-analysis-2026-05-31.md)
- [SDK Quality Deep Analysis](sdk-quality-deep-analysis-2026-05-31.md)
- [Agent Driver Refactoring Plan](agent-driver-refactoring-plan-2026-05-31.md)
- [Archived May 2026 plans](archive/2026-05/README.md)

If one of these pages contains older unchecked items, treat them as historical
unless they are copied into the active phases below.

## Closed Baseline

Closed by the earlier unified plan:

- dynamic prompt assembly by effective tool surface;
- live todos vs modal approval planning;
- steering controls and queue semantics;
- subagent basics: spawn, child rows, join, synthesis UI;
- Python tool autonomy and UI;
- Markdown/math/code rendering and citation shelf;
- compaction notification lifecycle;
- research evidence gate: `web_search` candidates vs verified reads;
- provider failure UX for 4xx/stream errors;
- unknown-tool repair and bounded research repair;
- typed runtime state helpers for loop/tool/planning/research/streaming/
  compaction state;
- context-pressure ladder and diagnostics;
- SDK `Agent`, `Session`, `RunHandle`, stream helpers, typed provider errors,
  trace summary and docs;
- Skills metadata, `skill_view`, invocation records, curated research skills
  and trusted subagent preload;
- provider-neutral `deep_parallel_research` contract and chat-demo UX;
- structural runtime/provider/observability splits with compatibility shims;
- root docs cleanup and archival of closed long plans.

## Phase 1 - Artifact-First Deep Research P0

Goal: stop the long-answer rewrite loop.

- Add a session workspace for deep research runs with scoped filesystem-write
  policy.
- Add prompt/runtime rule: long report drafts go to `research/report.md`, not
  only to assistant chat text.
- Enable `filesystem_write` for `deep_parallel_research` through a dedicated
  preset or equivalent policy, constrained to the session workspace.
- Add a write-through guard for oversized assistant messages in Deep Research.
- If a report artifact exists and todos remain unfinished, force todo update or
  artifact patch, not full answer regeneration.

Acceptance:

- A fork-join deep research run creates or updates `research/report.md`.
- The next repair step after unfinished todos does not re-stream the whole
  report.
- Writes outside the session workspace are denied.

## Phase 2 - Visible Artifacts And Efficient Editing

Goal: make artifacts visible, resumable and cheap to patch.

- Add artifact index builder over the session workspace.
- Add backend list/read endpoints through SDK/runtime-shaped contracts.
- Add chat-demo artifact panel and markdown preview.
- Emit `artifact_created` / `artifact_updated` runtime events from filesystem
  write tools.
- Add `file_patch` or equivalent structured patch helper for report updates.
- Add stale-read guard before editing existing report sections.
- Add repeated-read dedupe for unchanged file regions.

Acceptance:

- Reconnect/replay reconstructs artifact metadata.
- The UI can show the report while the run is still active.
- Final chat answer is concise and points to the artifact.

## Phase 3 - Research Storage And Artifact-Aware Compaction

Goal: keep source evidence and oversized tool output out of chat history.

- Add durable `sources.jsonl` or equivalent source-ledger storage for research
  sessions.
- Add `source_ledger_write` or a runtime-owned ledger update path.
- Spill oversized web/tool outputs into workspace files and expose stable refs.
- Project artifact/source summaries into context after compaction without
  loading full bodies by default.

Acceptance:

- Large tool outputs can be read back by ref.
- Compaction keeps artifact path, source refs and current report state.
- Final-readiness still depends on verified evidence, not search candidates.

## Phase 4 - Eval Harness And Provider Cost Discipline

Goal: make the artifact workflow measurable before expanding it.

- Reproduce the fork-join deep research case as a deterministic scenario.
- Assert no assistant message over threshold is emitted before a report
  artifact update.
- Assert no full-report rewrite after unfinished todo repair.
- Move long live scenarios into data fixtures so harness changes are not mixed
  with behavior changes.
- Define a reusable eval result contract usable from CLI, pytest and chat-demo
  backend.
- Keep the provider live ladder cheap-to-expensive; GPT-5.5 remains acceptance,
  not first debugging target.

Acceptance:

- Focused deterministic tests fail with labels for rewrite loop, missing
  artifact, missing source ledger, provider failure and stale todos.
- Live checks are opt-in and recorded in the provider debugging page only when
  they explain an active regression or acceptance gate.

## Phase 5 - Storage Backend Convergence

Goal: remove checkpoint/storage ambiguity left outside the structural refactor.

- Ensure memory/sqlite/jsonl/postgres use one checkpoint payload serialization
  source of truth.
- Add shared ordering tests for `latest` and `list_checkpoints`: created-at
  ties, parent checkpoint chain and resume after replacement.
- Add table-driven backend capability tests.
- Reduce duplicated SQL/JSON payload conversion so storage-specific code owns
  persistence, not runtime semantics.

Acceptance:

- Backend behavior differences are explicit capabilities, not accidental
  serialization or ordering drift.
- Session/history/resume SDK behavior is stable across supported stores.

## Phase 6 - Optional SDK Gateway / Tool Server

Goal: decide whether the next SDK product step is worth building now.

Candidates:

- in-process SDK tool-server helper for packaging local custom tools;
- OpenAI-compatible endpoint for simple clients;
- SSE stream bridge with support for tool progress;
- docs explaining differences from hosted managed-agent APIs.

Acceptance:

- Either build a minimal reusable gateway/tool-server slice with tests and docs,
  or explicitly defer it in `docs/roadmap.md` so it stops appearing as an
  implicit P2 promise.

## Ongoing Docs Rule

- Keep `docs/README.md` and `docs/roadmap.md` short.
- Closed plans go to `docs/archive/` or become compact decision records.
- Reference docs must not contain active-looking unchecked checklists unless
  the items are also present in this unified plan.
- Provider run IDs stay only where they explain current acceptance or a live
  regression class.
