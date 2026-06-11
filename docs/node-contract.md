# NodeContract — reliable tool-first workflow nodes

`AgentRunInput.node_contract` is an **opt-in** envelope for driving agent-driver as
a single workflow/harness node: a fixed `tool_policy.allowed_tools` plus a concrete
task + target in the prompt, where the bare ReAct loop can otherwise drift into a
generic assistant reply ("What target?", "I don't have tools", "I can give
instructions instead") or keep the LLM running long after tool evidence is already
sufficient.

Absent (the default `NodeContract()`), behaviour is byte-for-byte unchanged — other
consumers of the library are unaffected.

```python
from agent_driver.contracts import AgentRunInput, NodeContract, ToolPolicyInput, ToolPolicyMode

run_input = AgentRunInput(
    input="enumerate passive subdomains",
    agent_id="recon",
    graph_preset="single_react",
    tool_policy=ToolPolicyInput(
        mode=ToolPolicyMode.ALLOW_TOOLS,
        allowed_tools=["subfinder", "ctfr"],
    ),
    node_contract=NodeContract(
        require_callable_tools=True,     # Layer A
        require_tool_use=True,           # Layer B
        target="culmen.com",
        task_hint="enumerate passive subdomains",
        finalize_when_tools=["subfinder", "ctfr"],  # Layer C
    ),
)
```

## The three layers

### Layer A — policy ↔ registry validation (`require_callable_tools`)

`allowed_tools` is a *filter* over the registry, not a guarantee. If a declared name
is not actually registered (a typo, a missing plugin), it is silently dropped and the
model is handed a short/empty tool list — then *correctly* says "I don't have tools".

When `require_callable_tools` is set, the runtime diffs `allowed_tools` and
`finalize_when_tools` against the live registry at run start. Any name that is not
callable is:

- emitted as a `node_contract_warning` runtime event (`payload.tools`), and
- recorded on `output.metadata["node_contract"]["tool_policy_warnings"]`.

The host learns about a policy↔registry mismatch *explicitly* instead of via an empty
result downstream.

### Layer B — tool-use contract (`require_tool_use`)

Two mechanisms make tool use reliable:

- **Proactive prelude.** At run start the runtime weaves a system-prompt addendum
  naming the callable tools, the `target`, and the `task_hint`, with an explicit
  "call the tools now; do not ask which target" instruction. It only names tools that
  will actually surface, so the promise is honest.
- **Reactive guard.** If the run reaches finalize having made **zero** tool calls,
  that is treated as a *recoverable* violation rather than an answer: the runtime
  reprompts (up to `max_tool_use_reprompts`, default `1`) re-stating the tools +
  target. The trigger is the structural fact `tool_calls == 0` — not a brittle scan
  for "I cannot" phrasing.

If the reprompts are exhausted with still-zero tool calls, the run finalizes but
stamps a typed violation instead of returning a silent generic answer:

```json
output.metadata["node_contract"]["violation"] = {
  "kind": "no_tool_use",
  "detail": "...finalized with zero tool calls after exhausting reprompts",
  "reprompts": 1,
  "max_reprompts": 1
}
```

### Layer C — early finalize from tool evidence

After successful tool outputs the runtime can keep the LLM generating well past the
point where evidence is sufficient (extra latency, cost, and provider-timeout risk).
Two opt-in ways to finalize **directly from tool evidence, with no extra LLM pass**:

- **Declarative** (`finalize_when_tools`): once every listed tool has produced a
  successful (non-error, non-denied) envelope, the run finalizes immediately. The
  terminal answer is synthesised from the tool summaries.
- **Programmatic hook** (`on_tool_evidence`): a host lifecycle hook inspects the
  envelopes and returns `FinalizeNow(answer=...)` to finalize now with its own
  answer. This is the generic `stop_after_tool_evidence` /
  `finalize_when_tools_satisfy_contract` escape hatch.

```python
from agent_driver.contracts import FinalizeNow
from agent_driver.runtime.lifecycle_hooks import BaseRunLifecycleHook

class StopAfterEvidence(BaseRunLifecycleHook):
    name = "stop_after_tool_evidence"

    async def on_tool_evidence(self, context, envelopes):
        if any(e.error is None for e in envelopes):
            return FinalizeNow(answer="...synthesised from envelopes...")
        return None

agent = create_agent(provider=provider, lifecycle_hooks=(StopAfterEvidence(),))
```

When either trigger fires the stream ends with a clean `done`, the tool outputs are
preserved, and no wasted LLM continuation runs. `output.metadata["node_contract"]
["early_finalize_reason"]` records which path fired.

## Machine-readable outcome

Whenever the contract is active (or a warning / violation / early-finalize signal was
recorded) the run carries a compact summary for downstream consumers:

```json
output.metadata["node_contract"] = {
  "active": true,
  "require_tool_use": true,
  "require_callable_tools": true,
  "finalize_when_tools": ["subfinder", "ctfr"],
  "tool_calls": 2,
  "executed_tools": [
    {"tool_name": "subfinder", "tool_call_id": "...", "status": "completed",
     "summary": "...", "structured_output": {...}, "error_code": null}
  ],
  "tool_policy_warnings": [],
  "violation": null,
  "early_finalize_reason": "finalize_when_tools_satisfied",
  "reprompts": 0
}
```

Per-call tool events on the stream (`tool_call_completed`) carry the stable fields
`tool_name`, `tool_call_id`, `status`, `output_preview`, and `structured_output`
(when the adapter returned a structured payload) for machine-checkable normalization.
