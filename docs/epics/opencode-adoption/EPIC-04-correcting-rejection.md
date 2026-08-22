# EPIC-04 — Correcting-rejection feedback + pending-cascade (S)

Status: **DONE (2026-08-22)** (correcting-rejection). Track:
[opencode-adoption](README.md). Source idea: opencode's `permission/index.ts reply()` —
a reject carrying a message fails the awaiting call with a `CorrectedError({feedback})`
(the session continues and the model self-corrects) rather than a bare `RejectedError`
(hard abort); and rejecting/allow-always one pending ask cascades to the session's other
pending asks.

## What was built: correcting rejection

Today a `ResumeAction.REJECT` always terminates the run `FAILED` (`APPROVAL_REJECTED`).
That is opencode's `RejectedError` — the right default for a bare "no". What was missing
is opencode's `CorrectedError`: a rejection that *steers*.

New opt-in `RunnerConfig.corrective_rejection_enabled` (default **False** →
behaviour-neutral, every REJECT still aborts). When **True**, a REJECT that carries a
`message` on a **non-plan** tool-approval interrupt is a *correcting* rejection:

- the pending tool call is denied and **never executes**;
- its reserved tool-call budget is refunded (`context.tool_calls -= 1`);
- the operator's feedback is recorded in `context.metadata["rejection_feedback"]`
  (`{tool_name, feedback}`) and the loop routes back to `llm_call`;
- the LLM-request builder folds it into the next turn as a one-shot USER steering
  message ("The operator rejected `<tool>` and it was NOT executed. Do not repeat that
  call. Operator feedback: …"), then **pops** the key so it fires exactly once;
- the run continues so the model adjusts, instead of terminating.

A REJECT with **no message** — or on a `PLAN_APPROVAL_REQUIRED` interrupt (which has its
own CLARIFY→refinement flow) — still takes the terminal `_apply_resume_reject` path. So
the message-present split mirrors opencode's Corrected-vs-Rejected exactly, gated behind
the opt-in flag.

Files: `RunnerConfig.corrective_rejection_enabled` (`runtime/single_agent/types.py`);
`_apply_corrective_rejection` + the REJECT branch (`lifecycle/resume.py`); one-shot
injection in `llm_step/request.py`; `rejection_feedback` documented in
`docs/runtime-metadata.md`. Tests: `tests/runtime/test_corrective_rejection.py`
(continues with feedback + tool never runs; default flag aborts; bare reject aborts even
when enabled). Broad runtime/contracts/sdk sweep green.

## Pending-cascade: N/A in the single-agent runtime (deliberately deferred)

opencode holds a **Map of many concurrent pending asks per session** (parallel tool calls
each awaiting permission), so rejecting one can batch-reject siblings and an allow-always
can batch-approve every sibling the new rule now covers. Our single-agent tool loop
**stops at the first INTERRUPT** (`_execute_one_call` returns a stop signal → the batch
breaks), so **exactly one interrupt is ever pending**. There are no concurrent siblings to
cascade over — the remaining planned calls are simply re-planned on resume. The cascade is
therefore structurally a no-op here.

The forward-looking half we already own: `ResumeCommand.approved_prompts` /
`AllowedPrompt` categories are the durable analog of opencode's allow-`always` — an
approval that auto-collapses *future* matching INTERRUPTs to ALLOW for the run (see the
`_match_run_approved_prompts` short-circuit in `GovernedToolExecutor`). We approve-forward
by category rather than approve-sideways across concurrent asks.

If a layer with genuinely concurrent asks is ever built (e.g. the coordination /
multi-agent stack fanning out independent approval-gated calls in one session), revisit the
sibling-cascade there — it belongs where the concurrency lives, not in the single-agent
loop.

## Not done (deliberately)

- No sibling pending-cascade (see above — N/A for one pending interrupt).
- Default left **off**: turning a REJECT-with-message from terminal into continue is a
  behaviour change, so it is opt-in per run via the config flag.
