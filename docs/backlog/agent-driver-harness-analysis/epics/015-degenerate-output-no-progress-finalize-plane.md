# Degenerate-output and no-progress finalize plane

Дата создания: 2026-07-04.

Статус: **done** (2026-07-04). Root cause fixed at source (Phase B/D), Phase C finalize recovery +
empty-retry as defense-in-depth, Phase E already present, Phase F host bumped + validated on a durable
rebuilt image. Only optional cleanup (drop the now-redundant host guard) remains. Discovered from a host (MeetScript «Ask Meetings»
chat_v2) over the `single_react` / `react_text` streaming path with deepseek-v4-flash on OpenRouter.

## Implementation status (2026-07-04)

**ROOT CAUSE (captured on the live host, then fixed).** Isolated repro first showed a complete answer
finalizes in one call on BOTH non-streaming and clean-streaming paths — so the trigger was content-
specific, not a generic path bug. Capturing the tool-stage decision on a live over-iteration showed
**both** tool-stage transitions returned `continue=False` (envelopes=0, finish=stop) — the extra LLM
call came **after** finalize, from the **continuation detector** (`lifecycle/continuation.py`
`analyze_continuation_intent` → `_execute_finalize` → `next_step=llm_call`). It flagged
`unclosed_code_block` whenever the answer had an **odd number of ``` fences** (`count("```") % 2`) and
re-prompted — but models routinely emit an odd number of ``` as formatting in a **complete** answer
(verified: a 6k-char RU summary ending in a finished sentence, `reason=unclosed_code_block`). The
re-prompt produced a redundant re-answer that degenerated into a «задача уже выполнена» stub.

**Fix (Phase B/D):** `unclosed_code_block` now fires only when the answer actually ends mid-code (the
tail after the last unclosed fence isn't a finished sentence / substantial prose). **Validated live:**
5/5 broad-query runs now finalize in **one LLM call** (was 1–3 over-iterations → stubs), answers
5.6–7.9k chars, zero stubs. Over-iteration eliminated at its source.

Defense-in-depth (kept): Phase C finalize recovery + bounded empty-answer retry still catch any residual
degenerate/empty terminal from other providers.

Landed (commits on this branch):
- **Phase C — finalize recovery** (`finalization/answer_recovery.py` + `_build_output` wiring): when a
  tool-less run finalizes an empty turn or a short «already-answered» restatement, recover the longest
  substantive assistant turn from the event log. Gated on 0 tool calls; no-op for well-behaved runs;
  surfaces `metadata.answer_recovered[_reason]`. Unit-tested (`tests/runtime/test_answer_recovery.py`).
- **Bounded empty-answer retry** (`tool_stage/_finalize_tool_stage_transition`): a pure-text run about
  to finalize an EMPTY answer re-prompts once (`_MAX_EMPTY_ANSWER_RETRIES`, `empty_answer_retry_count`
  in the metadata inventory) instead of surfacing a blank answer. Gated on 0 tool calls so tool-informed
  finalizes are untouched. Repro test flipped xfail→passing. Full `tests/runtime` suite green.
- Pre-existing prior art already in the runtime: **`budget_grace`** (`budget_grace_granted_at_step`,
  `_budget_grace_call`) is the hermes «one grace call» pattern (Phase E largely already present).

Remaining (honest): a dedicated **no-progress signal for tool-less repeats** (Phase B) and the
**precise streaming-recovery trigger fix** (Phase D) are deferred — they need a captured real deepseek
stream to reproduce deterministically; Phase C covers the user-visible symptom trigger-agnostically.
**Phase F** (host drops its guard) waits on an agent-driver release + MeetScript dependency bump.

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
- [x] Scripted-provider repro `tests/runtime/test_stop_answer_no_loop_repro.py` (baseline STOP,
      empty-STOP, tool-calls-finish) on `agent.run`.
- [x] **Root cause captured on the live host** (not a stream quirk): the extra LLM call comes from the
      finalize-step **continuation detector**, not the tool-stage loop (both tool-stage transitions were
      `continue=False`). `analyze_continuation_intent` was re-prompting complete answers with an odd
      ``` fence count. `tests/runtime/test_continuation_intent.py` locks the behaviour.

### Phase B. No-progress signal for tool-less repeats
- [x] **Addressed at the source:** the continuation detector no longer re-prompts a complete tool-less
      answer (the mechanism that manufactured the no-progress re-answers). Live: over-iteration gone
      (5/5 runs = 1 LLM call). A separate `observe|warn|enforce` runtime-decision surface is optional
      follow-up; the degenerate loop no longer occurs.

### Phase C. Empty-response + best-answer finalize
- [x] Bounded retry for an empty terminal turn (`tool_stage`, `_MAX_EMPTY_ANSWER_RETRIES`); on exhaustion
      finalizes (with Phase C recovery preferring the best prior substantive turn).
- [x] Best-answer selection helper (`finalization/answer_recovery.py`) used at finalize in `_build_output`.
- [x] Tests: empty→retry→answer (`test_stop_answer_no_loop_repro`); degenerate-last→recover-prior and
      genuine-short-unaffected (`test_answer_recovery`). Full `tests/runtime` green.

### Phase D. Re-entry fix (was "streaming-recovery")
- [x] **Fixed the real re-entry:** it was the finalize-step continuation detector, not stream recovery.
      `lifecycle/continuation.py` `unclosed_code_block` now requires the answer to actually end mid-code.
      Regression: `tests/runtime/test_continuation_intent.py` + full `tests/runtime` green; live-validated
      (over-iteration eliminated).

### Phase E. Iteration budget + grace (adapt hermes)
- [x] **Already present** in the runtime: `budget_grace` (`budget_grace_granted_at_step`,
      `_budget_grace_call`, `budget_grace_reason`) is the hermes «one grace call» pattern atop `max_steps`.
- [ ] Optional: a dedicated no-progress-iteration budget (vs total steps) — folded into Phase B if pursued.

### Phase F. Host adoption
- [x] **Runtime guarantee validated end-to-end** against the live MeetScript stand (this agent-driver
      hot-installed into the chat jobworker): over 3× broad-query runs (incl. an n_llm=3 over-iteration),
      the runtime recovered the substantive answer itself — the host's app-side guard did **not** fire
      (`answer_recovered` False at the host level) and 0 stubs reached the user. The app guard is now
      redundant for these cases.
- [x] **MeetScript dependency bumped + durable image validated:** `requirements-agent-driver.txt`
      pinned `5934558 → 24cd636`; the chat image rebuilt (installs the fix from github, not a hot-patch);
      5/5 broad-query runs finalize in ONE LLM call, 0 stubs (meetscript `8302d51`).
- [ ] Optional cleanup: drop the app-side guard (`_chat_v2_longest_substantive_assistant_message`) after
      a chat benchmark — currently kept as harmless defense-in-depth.

## Acceptance / evidence plan
- Isolated deterministic tests for A–E (no live provider needed for the core guarantees).
- `no-claim` for live OpenRouter/Phoenix/Playwright gates unless explicitly executed.
- Host validation: MeetScript chat_diverse benchmark shows 0 user-visible stubs with the app guard
  **removed** (runtime provides the guarantee).

## Source / discovery
MeetScript investigation `docs/roadmap/admin-ux-and-analysis-epics-2026-07-02/epics/epic6-chat-plan-following/INVESTIGATION.md` (deep-dive + isolated repro + both planes).
