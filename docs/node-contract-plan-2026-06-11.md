# NodeContract: contract-following + early-finalize for workflow/harness runs

Plan + design. Driven by a downstream consumer (the **Zion** recon harness) that
embeds agent-driver and runs single-node workflow steps with a fixed
`tool_policy.allowed_tools` and a concrete task+target in the prompt.

## The two problems (as reported by the consumer)

**1. Weak contract-following.** A node with `allowed_tools=[subfinder, ctfr]` and
target `culmen.com` in context produced a *generic assistant* reply instead of
tool calls — e.g. *"What domain would you like me to enumerate?"* or *"I don't
have access to command execution tools."* Downstream `normalized.json` came back
empty until Zion bolted on a deterministic prelude / short-circuit on its side.

**2. Over-long LLM continuation.** Streaming kept the LLM running well past the
point where tool evidence was already sufficient — +200–400s and a raised risk of
`provider request timed out`.

## Root cause (mapped to code)

**Problem 1 has two independent causes:**

- **`allowed_tools` is a *filter* over the registry, not a guarantee.** Request
  assembly (`runtime/single_agent/llm_step/build.py:338`,
  `_request_tools_from_registry(registry, allowed=…, denied=…)`) silently drops
  any allowed name that isn't present in the registry. If `subfinder`/`ctfr`
  aren't registered (or names mismatch), the model is handed an empty/short tool
  list and *correctly* says "I don't have tools." There is **no diagnostic** for a
  policy↔registry mismatch today.
- **A weak model may refuse or ask for clarification** even with tools surfaced.
  The loop accepts that as the final answer:
  `tool_stage/__init__.py:277` sets `continue_with_llm=False` whenever
  `finish_reason != TOOL_CALLS`, routing straight to `finalize`. There is **no
  layer** that treats "finalized on the first turn with zero tool calls, while the
  node contract requires tool use" as a recoverable violation.

**Problem 2:** the force-final mechanism already exists
(`runtime/metadata_state.py` `ToolLoopState.force_final_answer`, reasons in
`tool_stage/__init__.py:_force_final_reason` — `near_*_budget`,
`python_result_ready`, `deliverable_request_satisfied`, …) but (a) it is wired to
built-in heuristics only — a host app cannot trigger it; and (b) when it fires it
still runs **one more LLM pass** (`tool_choice="none"`) to synthesize the answer.
The consumer wants to finalize **directly from tool evidence, no extra pass**.

## Prior art we lean on

- **deepagents** (LangChain): explicit tool catalog + planning prelude + "use your
  tools" discipline in the system prompt. → our proactive tool-use prelude.
- **OpenClaude / Claude Code**: a strong environment/context block and an
  "act, don't ask for confirmation" stance. → inject the concrete target+tools and
  forbid clarifying questions when the target is already present.
- **Hermes (NousResearch function-calling)**: rigid tool-call format with a
  reprompt when the model emits prose instead of a tool call. → our recoverable
  reprompt on a zero-tool-call violation.

The shared lesson: for harness/workflow mode you do **not** rely on the model to
decide to call tools and to stop — you wrap the loop in a contract that (i) makes
the available tools and target unmissable, (ii) catches a no-tool refusal and
re-drives it, and (iii) lets the host finalize from evidence.

## Design: `AgentRunInput.node_contract`

A new **opt-in** typed field. Absent ⇒ behaviour is byte-for-byte unchanged
(agent-driver is a library with other consumers).

```python
class NodeContract(ContractModel):
    # A: policy↔registry validation
    require_callable_tools: bool = False
    # ^ when True, validate allowed_tools against the registry at run start and
    #   emit a structured warning for any name that isn't callable.

    # B: tool-use contract
    require_tool_use: bool = False
    # ^ finalizing with zero tool calls is a recoverable violation.
    max_tool_use_reprompts: int = 1
    on_violation: Literal["reprompt_then_error"] = "reprompt_then_error"
    target: str | None = None          # concrete target woven into the prelude
    task_hint: str | None = None       # one-line task description for the prelude

    # C: early finalize from tool evidence
    finalize_when_tools: list[str] = []
    # ^ once all listed tools have produced a successful (non-error) envelope,
    #   finalize directly without another LLM continuation.
```

Config surface decision: **explicit field on `AgentRunInput`** (typed, easy for
Zion to build, plumbs like `tool_policy` / `tool_choice`). Enforcement decision:
**reprompt, then structured error** (runtime-driven autonomy for harness mode).

### Layer A — policy↔registry validation (fail-loud)

- **Seam:** run start. A built-in `on_run_start`-time check (the lifecycle hook
  surface already exists: `runtime/lifecycle_hooks.py` `dispatch_run_start`).
- **Behaviour:** when `require_callable_tools`, diff `tool_policy.allowed_tools`
  (and `finalize_when_tools`) against the live registry. Any name not callable →
  collect into `tool_policy_unsatisfiable`, emit a `RuntimeEvent`
  (`TOOL_POLICY_WARNING`) and stash on `output.metadata["tool_policy_warnings"]`
  so both the stream and the final output carry it.
- **Why:** directly answers "the model must not claim tools are unavailable when
  the registry/policy holds callable tools" — and when it genuinely doesn't, the
  host learns *explicitly* instead of via an empty `normalized.json`.

### Layer B — tool-use contract (recoverable → reprompt → error)

- **Proactive prelude (deepagents/openclaude):** when `require_tool_use`, weave a
  system-prompt addendum at run start: the concrete callable tool names, the
  `target`/`task_hint`, and "call the tools now; do not ask for the target — it is
  `<target>`." Injected via the request seam so the model calls tools on turn 1.
- **Reactive guard (Hermes):** in `_execute_finalize`
  (`lifecycle/steps.py:220`), *before* the rubric dispatch, if
  `require_tool_use and context.tool_calls == 0`, build a continuation
  (`_build_continuation_transition`, the same machinery rubric revision uses) with
  a `count_key="tool_use_contract_reprompt_count"` bounded by
  `max_tool_use_reprompts`. The nudge re-states the tools + target + "you have not
  called any tool; call one now."
- **Escalation:** after the reprompts are exhausted with still-zero tool calls,
  do **not** loop forever — finalize, but stamp
  `output.metadata["node_contract_violation"] = {"kind": "no_tool_use", …}` and
  emit a structured warning event. The answer is no longer a silent generic reply;
  the host sees a typed violation.
- **Signal, not string-matching:** the trigger is the structural fact
  `tool_calls == 0` under an active `require_tool_use`, not a brittle scan for
  "I cannot" phrasing.

### Layer C — early finalize from tool evidence (no extra LLM pass)

- **Seam:** `_finalize_tool_stage_transition` (`tool_stage/__init__.py:285`),
  the exact point where `continue_with_llm=True` would loop back to `llm_call`.
- **Declarative:** when every tool in `finalize_when_tools` has a successful
  envelope (tracked across the run), set `next_step="finalize"` instead of
  `"llm_call"`.
- **Programmatic hook (generic escape hatch the consumer asked for):** a new
  optional lifecycle method
  `on_tool_evidence(context, envelopes) -> FinalizeNow | None`. A host returns
  `FinalizeNow(answer=…)` ⇒ runtime finalizes now with that answer.
- **Terminal answer without an LLM call:** stash
  `context.metadata["early_finalize_answer"]` and have
  `finalization/output.py:_sanitize_terminal_answer` prefer it when set — mirrors
  the existing `_deep_research_artifact_handoff_answer` precedent (a synthesized
  terminal answer with no model turn). The tool envelopes already carry the
  evidence on `output.metadata`/stream for downstream normalization.
- **Result:** stream ends with a clean `done`, no timeout, no wasted 200–400s.

## Acceptance-criteria mapping

| Criterion | Layer | Where |
| --- | --- | --- |
| allowed `[subfinder, ctfr]` + "enumerate passive subdomains for culmen.com" calls ≥1 tool, no provider-forced `tool_choice` | B prelude | request prelude at run start |
| no-tool refusal with target+tools present ⇒ recoverable: reprompt or structured warning/error | B guard+escalation | `lifecycle/steps.py:_execute_finalize` |
| stream events carry tool call/output envelope (tool_name, status, job_id/artifact_refs) for downstream normalization | A + existing | tool envelopes already streamed; A adds policy warnings |
| runtime option `stop_after_tool_evidence` / `finalize_when_tools_satisfy_contract` | C declarative | `finalize_when_tools` |
| generic hook: host returns "evidence satisfies node contract, finalize now" without extra LLM continuation | C hook | `on_tool_evidence → FinalizeNow` |
| stream ends clean `done`, not timeout, keeps final answer/tool outputs | C | `early_finalize_answer` + envelopes |

## Implementation order (incremental, suite green + commit per layer)

1. **Schema** — `NodeContract` + `FinalizeNow`; `AgentRunInput.node_contract`;
   SDK/adapter passthrough. No behaviour change yet.
2. **Layer A** — run-start validation + `tool_policy_warnings` in output/stream.
3. **Layer C** — `on_tool_evidence` hook + `finalize_when_tools` +
   `early_finalize_answer` terminal-answer path.
4. **Layer B** — proactive prelude + reactive reprompt guard + structured
   violation escalation.
5. **Docs** — `docs/runtime/node-contract.md` consumer guide; cross-link from the
   tool_choice / forcing-tool-calls patterns.

Each layer is opt-in and independently testable. Live-validated against the Zion
recon scenario (allowed `[subfinder, ctfr]`, target `culmen.com`) where feasible
with a fake tool registry; the provider-side calls reuse the existing offline
`FakeProvider` harness.
