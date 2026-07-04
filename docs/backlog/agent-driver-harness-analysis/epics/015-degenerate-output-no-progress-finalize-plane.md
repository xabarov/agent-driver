# Degenerate-output and no-progress finalize plane

Дата создания: 2026-07-04.

Статус: **proposed** (not implemented). Discovered from a host (MeetScript «Ask Meetings» chat_v2)
over the `single_react` / `react_text` streaming path with deepseek-v4-flash on OpenRouter.

Workstream: runtime loop robustness. Builds on the existing step backstop
(`single_agent/types.py` `default_max_steps`), the `tool_loop_no_progress` policy
(`runtime/policy.py`), node-contract reprompt/finalize guards (`single_agent/node_contract.py`),
and stream recovery (`single_agent/llm_step/stream_recovery.py`).

## Motivation

A host observed the agent produce a **complete, correct answer** and then, over extra loop
iterations, **overwrite it with a degenerate «task already done / see previous answer» stub** — the
last assistant message wins, so the user receives the stub instead of the real answer. The host had
to add an app-side recovery guard (recover the longest substantive assistant message from the event
log) to survive it. That guard is a symptom workaround; the loop should not manufacture the stub.

### Isolated reproduction in this repo (evidence)

`tests/runtime/test_stop_answer_no_loop_repro.py` (scripted `FakeProvider` + `agent.run`,
`graph_preset="single_react"`, `agent_profile="react_text"`, `tool_choice="auto"`):

| Case | Behaviour today | Verdict |
|---|---|---|
| Complete `finish_reason=STOP` prose, no tool call, tools available | **1 LLM call**, finalizes | ✅ core loop correct |
| **Empty `STOP` first response** | **finalizes the EMPTY answer, no retry** | ❌ gap (`xfail`) — matches host live `answer_len=0` |
| `finish_reason=TOOL_CALLS` but no parseable call | finalizes the answer | ✅ no infinite loop |

The **3-call stub over-iteration** does **not** reproduce on the non-streaming `agent.run` path →
it is **streaming-path specific** (`stream_run` → `stream_recovery`), where empty/broken forced-final
retries and text-form recovery re-enter the model. `extract_text_form_tool_calls` does **not**
mis-parse the prose answer (returns 0), so the continuation is not the text-form-envelope path.

### Rejected root causes (disproven with data)
- Node-contract `require_tool_use` — default `False`; the host sets no contract → not the trigger.
- Text-form tool-call mis-parse of the answer prose — parser returns 0 planned/0 errors on real content.
- Microcompaction — `memory_compacted` is a **symptom** (one microcompaction per `llm_step`), not an
  extra model turn.

## Gap analysis (why existing machinery misses this)

- `tool_loop_no_progress` (`runtime/policy.py`) fires only on **tool** signals:
  `idempotent_read_no_progress`, `repeated_identical_tool_args`, `repeated_failed_tool_call`.
  **Repeated tool-LESS assistant answers (0 tool calls) are not covered.**
- There is a `default_max_steps` backstop, but it caps at a large N and does not detect *semantic*
  no-progress (a second/third assistant turn that adds no tool evidence and no new content).
- Empty model responses are finalized as the answer instead of being retried/guarded.
- No «best-answer» selection: when the terminal turn is degenerate but an earlier turn was
  substantive, the earlier answer is discarded.

## Prior art to adopt (`reference/`, marked copy/adapt/inspired-by/defer)

- **`reference/hermes-agent/agent/conversation_loop.py:589`** — explicit **iteration budget**:
  `while api_call_count < agent.max_iterations and agent.iteration_budget.remaining > 0`, an
  `iteration_budget.consume()` gate, a **grace-call** («one last chance» then exit), and a clean
  `break` on exhaustion. → **adapt**: bound degenerate loops with a budget + grace instead of relying
  only on `max_steps`.
- Same file — handling of `finish_reason=content_filter` and empty/malformed message tails
  (`empty content on malformed sequences`). → **inspired-by**: guard empty/degenerate provider turns
  before they become the answer.

## Scope

1. **No-progress on tool-less repeats.** Extend no-progress detection so a run whose latest assistant
   turn produced **no tool call and no new evidence** (e.g. near-duplicate of a prior assistant turn,
   or a «done/already-answered» degenerate) **finalizes** rather than re-prompting.
2. **Empty-response guard/retry.** An empty terminal model turn is retried (bounded) or finalized with
   the best prior substantive turn, never surfaced as an empty answer.
3. **Best-answer selection.** On finalize, if the terminal turn is degenerate, prefer the longest
   substantive assistant turn of the run (the host guard, lifted into the runtime).
4. **Streaming-recovery robustness.** Reproduce and fix the streaming-path retries that manufacture the
   extra turns (`llm_step/stream_recovery.py`).
5. **Iteration budget + grace.** Adopt the hermes iteration-budget/grace pattern as a bounded backstop
   distinct from `max_steps`.

### Non-goals
- No change to the correct single-STOP finalize path (case A stays 1 call).
- No new default that forces tool use, ends multi-tool ReAct loops early, or changes provider selection.
- No host-specific heuristics; all detection is content/structure-general and raw-free.

## Checklist (proposed)

### Phase A. Repro harness
- [ ] Streaming `FakeProvider` (or scripted stream) that mimics deepseek-on-OpenRouter empty/partial
      forced-final + re-answer, driving `stream_run` (not just `run`).
- [ ] Deterministic test that reproduces the 3-turn stub over-iteration on the streaming path.
- [ ] Promote `tests/runtime/test_stop_answer_no_loop_repro.py` (baseline + empty-STOP xfail) into the
      suite; flip the empty-STOP xfail to pass once Phase C lands.

### Phase B. No-progress signal for tool-less repeats
- [ ] Add a `no_progress_tool_less_answer` signal (near-duplicate / degenerate «already-done» terminal
      turn with 0 tool calls and no new evidence). Raw-free, generic markers + structural similarity.
- [ ] Route it into the finalize decision in `tool_stage`/loop-control (finalize, not reprompt).
- [ ] Emit a runtime decision (`observe|warn|enforce`) mirroring `tool_loop_no_progress` vocabulary.

### Phase C. Empty-response + best-answer finalize
- [ ] Bounded retry for an empty terminal turn; on exhaustion finalize the best prior substantive turn.
- [ ] Best-answer selection helper (longest substantive non-degenerate assistant turn) used at finalize.
- [ ] Tests: empty→retry→answer; degenerate-last→recover-prior; genuine short answer unaffected.

### Phase D. Streaming-recovery fix
- [ ] Fix the streaming path so a complete answer is not re-entered by forced-final/text-form recovery.
- [ ] Regression tests on the streaming repro from Phase A.

### Phase E. Iteration budget + grace (adapt hermes)
- [ ] Add a bounded iteration budget + one grace call distinct from `max_steps`; wire into loop control.
- [ ] Config surface + defaults that preserve current behavior for well-behaved runs.

### Phase F. Host adoption
- [ ] Provide the runtime guarantee so MeetScript can drop its app-side recovery guard
      (`routes/chat.py` `_chat_v2_longest_substantive_assistant_message`) — validated on its chat bench.

## Acceptance / evidence plan
- Isolated deterministic tests for A–E (no live provider needed for the core guarantees).
- `no-claim` for live OpenRouter/Phoenix/Playwright gates unless explicitly executed.
- Host validation: MeetScript chat_diverse benchmark shows 0 user-visible stubs with the app guard
  **removed** (runtime provides the guarantee).

## Source / discovery
MeetScript investigation `docs/roadmap/admin-ux-and-analysis-epics-2026-07-02/epics/epic6-chat-plan-following/INVESTIGATION.md` (deep-dive + isolated repro + both planes).
