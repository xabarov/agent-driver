# Chat Demo Compaction Notification Plan

Status: closed decision log / reference. Keep as history for chat-demo
compaction notification UI; active context-pressure work is tracked in
[Unified Work Plan Phase 2](unified-work-plan-2026-05-31.md#phase-2---harness-context-pressure).

Date: 2026-05-30

## Goal

Make context summarization visible, calm, and useful in chat-demo.

When the runtime compacts conversation memory, the user should understand that
the agent is preserving context and continuing work. The notification must not
look like an assistant answer, must not steal focus, and must expose enough
metadata for debugging: mode, outcome, retained artifacts/digests, message
count, token estimates, and failure/circuit-breaker state.

## Product Principles

- Keep the demo clean: reusable compaction event contracts, summaries, and trace
  metadata belong in `agent_driver`; chat-demo only renders them.
- Prefer simple runtime events plus a compact UI component over a new
  orchestration layer.
- Treat compaction as system activity, not model prose. It should be visible but
  secondary to the user's task.
- Be honest about lossy behavior. If the system summarized or dropped old
  context, say so in concise language and link it to trace/debug metadata.
- Preserve flow. No modal for automatic compaction; use a small inline/status
  card and an accessible live region.

## Current State

- `agent_driver.runtime.single_agent.compaction_stage` already emits
  `RuntimeEventType.MEMORY_COMPACTED` after a skipped/successful/failed
  outcome.
- The event payload includes `outcome`, `mode`, `compaction_id`,
  `compaction_state`, and mode-specific fields such as
  `summarized_message_count`, token estimates, retained artifact ids, or
  failure details.
- There is no `memory_compaction_started` / progress event. Long LLM compaction
  can therefore feel like the agent is hanging until the outcome arrives.
- Chat-demo frontend currently knows about tool, planning, subagent, steering,
  token, and terminal events, but has no dedicated compaction UI.
- Hermes Agent surfaces compaction as status activity:
  `status.update { kind: "compressing", text: ... }` goes both into status and
  transcript, and `/compress` prints concise result lines.
- OpenClaude maps SDK status `compacting` to a system informational message
  "Compacting conversation..." and maps `compact_boundary` to a distinct
  system message "Conversation compacted" with metadata. It also tracks
  `pendingPostCompaction` so the first post-compaction API call can be tagged.

## External Findings

- OpenAI describes compaction as a first-class mechanism for long-running
  agent loops: when context fills, the system preserves key prior state and
  removes extraneous detail so the workflow can continue across context
  boundaries.
  Source: https://openai.com/index/equip-responses-api-computer-environment/
- Claude Code documents `/compact` as replacing conversation history with a
  structured summary and explicitly lists what survives compaction.
  Source: https://code.claude.com/docs/en/context-window
- Material Design's progress guidance distinguishes determinate and
  indeterminate indicators; for compaction we usually start indeterminate, then
  show a concrete outcome when the runtime knows it.
  Source: https://m2.material.io/components/progress-indicators/web
- W3C's ARIA technique for progress status recommends a live region so screen
  readers get status updates without moving focus.
  Source: https://www.w3.org/WAI/WCAG22/Techniques/aria/ARIA25
- MDN recommends native `<progress>` where possible and `aria-busy` /
  `aria-describedby` when progress describes a region.
  Source: https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Reference/Roles/progressbar_role
- NN/g guidance from loading UX is relevant: routine system-status messages
  should be discoverable but not intrusive; when users wait, a progress
  indication is better than making them guess.
  Source: https://media.nngroup.com/media/reports/free/Tablet_Website_and_Application_UX.pdf

## Target UX

### During Compaction

Show a small inline system card near the active run, not a chat bubble from the
assistant:

- title: `Compacting context`
- subtitle: `Summarizing older context so the run can continue`
- state: indeterminate progress line or subtle spinner
- badge: mode if known (`partial`, `session memory`, `LLM summary`)
- screen reader text: `Compacting conversation context`

If compaction starts while the assistant is streaming, the card should appear
below the current assistant/tool activity and should not reset the user's scroll
unless they are pinned to bottom.

### After Successful Compaction

Collapse the running card into a compact success card:

- title: `Context compacted`
- summary line examples:
  - `Partial compaction: 12 old messages summarized`
  - `Session memory reused; 3 artifacts retained`
  - `LLM summary created in 842ms`
- badges:
  - `mode`
  - `retained artifacts`
  - `retained digests`
  - `tokens saved` when available
- default collapsed details:
  - compaction id
  - retained artifact/digest ids
  - token estimates
  - orchestrator circuit-breaker state

### Skipped Compaction

Do not show a visible card for routine `outcome=skipped` by default. Keep it in
trace summary and replay. Optionally show it only in debug/replay mode.

### Failed Compaction

Show a non-blocking warning card:

- title: `Context compaction failed`
- summary: concise failure kind/message
- if circuit breaker opened, make that visible with a warning badge
- explain that the run will continue if possible, or that quality may degrade
- details contain compaction id and failure metadata

### Replay / Trace

Replay should show every compaction event, including skipped outcomes, because
it is a diagnostic view. The trace summary should count compactions and expose:

- `compaction.attempts`
- `compaction.successful`
- `compaction.failed`
- `compaction.skipped`
- `compaction.modes`
- `compaction.circuit_breaker_open`
- `compaction.latest`

## Proposed Runtime Contract

Keep existing `memory_compacted` as the terminal compaction outcome event.

Add a small optional lifecycle event before long work:

```text
memory_compaction_started
```

Payload:

```json
{
  "compaction_id": "cmp_...",
  "mode": "llm_full | partial | session_memory",
  "reason": "token_pressure | session_memory_stale | manual",
  "token_pressure_state": "soft | hard | none",
  "estimated_input_tokens": 123400
}
```

For very fast `partial` and `session_memory` paths we can still emit start and
success back-to-back; frontend will debounce the visible running card for
~300ms to avoid flicker.

Keep `memory_compacted` payload as canonical outcome:

- `outcome`: `skipped | successful | failed`
- `mode`
- `compaction_id`
- `compaction_state`
- optional mode/result fields already emitted today

## Implementation Phases

### Phase 1. Contract And Trace

- [x] Add `MEMORY_COMPACTION_STARTED` to runtime event enum.
- [x] Emit `memory_compaction_started` after `orchestrator.start_attempt()` and
  before any potentially long compaction path.
- [x] Keep existing `memory_compacted` outcome event unchanged for backward
  compatibility.
- [x] Add trace-summary compaction section with attempts, outcomes, modes,
  latest event, and circuit-breaker state.
- [x] Add focused runtime tests for started -> successful, started -> failed,
  skipped-only, and circuit-breaker warning.

### Phase 2. Frontend State Model

- [x] Extend `RunStreamEvent` type with `memory_compaction_started` and
  `memory_compacted`.
- [x] Add `CompactionNotice` / `CompactionLifecycle` type in chat store.
- [x] Upsert compaction notices by `compaction_id`; if no id is present, derive
  a stable id from run id and event sequence.
- [x] Hide skipped compactions in normal chat lane but keep them available for
  replay/debug.
- [x] Preserve notices when session transcript reloads after a run completes.

### Phase 3. UI Component

- [x] Add `CompactionNoticeCard`:
  - compact system-card styling, visually distinct from assistant/tool cards;
  - indeterminate state while running;
  - success/warning variants;
  - no raw JSON in default view.
- [x] Add `aria-live="polite"` status text and `aria-busy` on the message list
  region while compaction is running.
- [x] Use one subtle indicator type consistently; avoid both spinner and large
  progress bar unless determinate progress becomes available.
- [x] Add copy that is calm and concrete:
  `Summarizing older context so the run can continue.`

### Phase 4. Replay And Diagnostics

- [x] Render compaction lifecycle in `ReplayPage`.
- [x] Add compaction badges to run trace summary UI if/when trace summary is
  surfaced in chat.
- [x] Add Phoenix tags/events:
  `compaction.mode`, `compaction.outcome`, `compaction.id`,
  `compaction.summarized_message_count`, `compaction.circuit_breaker_open`.

### Phase 5. Scenario Coverage

- [x] Add deterministic Playwright scenario:
  `compaction-start-success` with start -> token_delta -> success -> final.
- [x] Add deterministic Playwright scenario:
  `compaction-failed-warning` with start -> failed outcome -> warning card.
- [x] Add deterministic Playwright scenario:
  `compaction-skipped-hidden` proving skipped does not clutter the happy path.
- [x] Add accessibility test:
  status is reachable via `role="status"`, card toggle is keyboard clickable,
  and message list gets `aria-busy` during active compaction.
- [x] Add one live probe trigger using a small synthetic/fake provider path if
  real token pressure is hard to reproduce deterministically.

  Synthetic path: run chat-demo with
  `AGENT_DRIVER_PROVIDER=fake CHAT_DEMO_FAKE_SCENARIO=compaction_notice` and
  execute live probe scenario `compaction-notice`. The backend lowers token
  thresholds only for that fake scenario, emits real runtime compaction events,
  and the frontend verifies the resulting notice.

## Acceptance Criteria

- The user sees a clear, non-intrusive notification when compaction is actually
  running.
- Successful compaction leaves a compact audit card with useful metadata, not a
  fake assistant message.
- Failed compaction is visible but does not block the chat UI.
- Skipped compaction does not pollute the main chat lane.
- Replay and trace-summary expose all compaction outcomes.
- Screen readers receive status updates without focus jumps.
- The implementation keeps reusable contracts and trace logic in
  `agent_driver`; chat-demo only renders normalized state.

## Risks

- Over-notifying can make normal long runs feel noisy. Use debounce and hide
  skipped outcomes.
- A `memory_compaction_started` event without a matching outcome would leave a
  stale card. Add timeout/final-run cleanup that marks it unknown/ended.
- If LLM full compaction blocks before emitting start, the UI still feels hung.
  Emit start immediately after the decision and attempt id are known.
- If compaction summary text is exposed too prominently, users may treat it as
  the final answer. Keep details collapsed and label it as system context.
