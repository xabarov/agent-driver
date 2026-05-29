# Forcing a specific tool call

> **When this applies:** your agent should reach a particular tool no
> matter what, and prompt nudges ("you MUST call X next") aren't
> reliable enough. Two common shapes:
>
> 1. **Promised-but-skipped tools.** The model gathers data, writes prose
>    promising a chart / a summary / a PR / a Slack message, then stops
>    without invoking the tool that would actually produce it.
> 2. **Structured one-shot extractions.** You want a typed payload (a
>    risk classification, a permission rationale, a JSON object that
>    fills a downstream form), and a free-text completion is unusable.
>
> **TL;DR:** set `tool_choice` on `AgentRunInput` so the provider
> guarantees what the model returns. Don't rely on the prompt to enforce
> tool use when the consequence of the model skipping it is "broken
> user-facing output."

## The motivating case

excel_ai's chart workflow was: user asks "create an infographic for this
sheet" → agent calls `sandbox_execute_pandas` → gets aggregated data →
writes prose "Here is the infographic showing..." → **never calls
`chart_vegalite`**. The amber warning in the UI surfaces "agent promised
a chart but didn't build one." The user still has no chart.

A trial against two different model families (`qwen3-235b-a22b-2507`,
`deepseek-v4-pro`) showed both reproducing the same bug on the same
prompt — including a system prompt that *explicitly* says
"TWO required tool calls in this exact order: sandbox_execute_pandas,
THEN chart_vegalite." The fix has to live below prompt engineering.

## How `tool_choice` solves it

`tool_choice` is a provider-level parameter that every modern API
exposes (OpenAI, Anthropic, OpenAI-compatible relays like OpenRouter).
agent-driver now surfaces it on the public `AgentRunInput` contract:

```python
from agent_driver.contracts import AgentRunInput

# Caller-side: the model MUST emit a tool_use block for chart_vegalite.
# Text-only completion is impossible — the provider rejects it.
run_input = AgentRunInput(
    input="<user question + the data we already extracted>",
    agent_id="agent.excel",
    graph_preset="single_react",
    tool_choice={"type": "tool", "name": "chart_vegalite"},
    max_tool_calls=1,
    ...
)
output = await agent.run(run_input)
# output.tool_trace is guaranteed to contain exactly one chart_vegalite call.
```

### Accepted shapes

| Value | Meaning | When |
|-------|---------|------|
| `None` (default) | Provider applies its default (usually `"auto"`) — model decides | Legacy behaviour; unchanged |
| `"auto"` | Same as `None` for backends that distinguish | Explicit "model decides" signal |
| `"required"` | Model MUST call **some** tool; text-only rejected | You know a tool is the right answer but don't care which |
| `"none"` | Model MUST NOT call any tool; text-only required | You want a final summary / explanation only |
| `{"type": "tool", "name": "X"}` | Model MUST call tool `X` — **provider-neutral shape** | The primary use case — guarantee a specific tool fires |
| `{"type": "function", "function": {"name": "X"}}` | OpenAI-native shape; pass-through | When you explicitly know you're on an OpenAI-compatible backend |

The exact string set is enforced by the **provider**, not by
agent-driver — passing `"vendor-specific"` for an experimental backend
is intentionally allowed.

#### Provider-neutral vs native dict shapes

OpenAI and Anthropic disagree on the specific-tool envelope:

  * Anthropic: `{"type": "tool", "name": "X"}`
  * OpenAI / OpenRouter / vLLM / Together / Groq: `{"type": "function", "function": {"name": "X"}}`

agent-driver standardizes on the Anthropic shape as the **canonical
provider-neutral form** and silently translates to the OpenAI shape in
the OpenAI-compatible adapter (see
`agent_driver/llm/providers_impl/openai_compatible.py::_normalize_tool_choice_for_openai`).
Native shapes pass through unchanged so callers who *do* know they're
targeting a specific backend can use the native form. The neutral form
is preferred because it survives provider swaps unchanged.

### Interaction with the inner-loop safety rail

agent-driver's ReAct loop watches for repeated identical tool calls and
internally forces `tool_choice="none"` on the next call to break the
loop and steer the model toward a final answer (see
`tool_stage._update_zero_result_policy`). **That override always wins**
over a caller-supplied `tool_choice` — the safety rail can't be
silently disabled. The caller's value is the starting point and the
fallback when the inner loop isn't intervening.

In practice:

  1. Turn 1: provider gets caller's `{"type": "tool", "name": "X"}`.
  2. Turn 2 (still no progress): same.
  3. Turn 3 (loop detected, forced to finalize): provider gets `"none"`.

See `tests/runtime/test_run_input_tool_choice.py::test_inner_loop_override_wins_over_caller_tool_choice`
for the regression that pins this behaviour.

## Pattern: focused retry after a "promised but skipped" turn

The original chart-promise problem can now be solved with a focused
retry instead of a full-run prompt-nudge retry (the workaround
landed in excel_ai as
`docs/backlog/agent_driver_contributions_2026-05-28.md` Task #54).

```python
# After the main run completes
output = await agent.run(main_run_input)

if (
    "график" in (output.answer or "")
    and not any(t.tool_name == "chart_vegalite" for t in output.tool_trace)
    and any(t.tool_name == "sandbox_execute_pandas" for t in output.tool_trace)
):
    # The model has data and promised a chart but never built it.
    # Re-invoke with tool_choice forcing chart_vegalite — single LLM call,
    # provider-guaranteed outcome, no prompt fighting.
    retry_input = AgentRunInput(
        input=output.answer + "\n\nNow render the chart you described.",
        agent_id=main_run_input.agent_id,
        graph_preset=main_run_input.graph_preset,
        tool_choice={"type": "tool", "name": "chart_vegalite"},
        max_tool_calls=1,
        ...
    )
    chart_output = await agent.run(retry_input)
```

This collapses the chart-retry from "2× full system prompt + tool docs"
to "one focused call with the data already extracted." Cheaper, faster,
and removes the "model might still skip the second step" risk.

## Pattern: structured one-shot extraction (sideQuery)

Same field, different use case: get a typed payload back without
running the full agent loop.

```python
# Pure structured-output side query — no tools, just JSON shape.
# (Use a tool definition as the schema and force the call.)
RISK_SCHEMA = ToolManifest(
    name="explain_risk",
    description="...",
    args_schema={
        "type": "object",
        "properties": {
            "level": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
            "reasoning": {"type": "string"},
        },
        "required": ["level", "reasoning"],
    },
)

await agent.run(
    AgentRunInput(
        input=command_to_classify,
        tool_choice={"type": "tool", "name": "explain_risk"},
        max_tool_calls=1,
        ...
    )
)
# output.tool_trace[0].args is guaranteed to fit the schema.
```

This mirrors the `sideQuery` pattern used by OpenClaude's permission
explainer and risk classifier (see
`openclaude/src/utils/permissions/permissionExplainer.ts`).

## Pattern: allowlist + `tool_choice="required"` (no specific tool to force)

Use this when you know **what category of tool the model should call**
but not the specific one — e.g. "any data tool, never a planning tool"
in excel_ai's plan-retry. Combining the SDK's
``ToolPolicyInput.allowed_tools`` filter (which strips forbidden tools
from the LLM-visible schema entirely) with ``tool_choice="required"``
gives the model a curated short list AND forces it to pick one:

```python
from agent_driver.contracts import AgentRunInput, ToolPolicyInput
from agent_driver.contracts.enums import ToolPolicyMode

DATA_TOOLS = ["sandbox_execute_pandas", "excel_read_table_page", "excel_find"]

await agent.run(
    AgentRunInput(
        input="<original question + 'use a data tool'>",
        tool_choice="required",
        tool_policy=ToolPolicyInput(
            mode=ToolPolicyMode.ALLOW_TOOLS,
            allowed_tools=DATA_TOOLS,
        ),
        max_tool_calls=1,
        ...
    )
)
```

What this buys you over ``tool_choice={"type":"tool","name":"X"}``:

  * **The model picks the right tool for the question.** Forcing a
    specific tool can be wrong — e.g. ``sandbox`` for a find-shaped
    question gives garbage args. The allowlist lets the model pick
    ``excel_find`` instead.
  * **Two-layer enforcement.** Even if the provider's tool_choice
    enforcement is buggy, the policy evaluator will deny calls to
    anything outside the allowlist. Defence in depth.

The filtering happens at request-build time so the LLM never sees the
forbidden tools in its schema. The runtime policy check
(`tools/policy/evaluator.py::evaluate_tool_policy`) is still the second
line of defence — both should agree on the allowlist.

Tests:
``tests/runtime/test_tool_schema_filtering.py`` pins the schema-layer
filter; ``tests/tools/test_policy_evaluator.py`` covers the runtime
gate.

## Limitations

  * **Streaming finishes before the tool runs.** Streaming consumers
    still see the tool-call delta, but `tool_choice="required"` means
    you won't get partial text content for free.
  * **`tool_choice` is per-LLM-call, not per-run.** The runtime applies
    it on every LLM step until the inner loop forces an override. If
    you want a one-shot, set `max_tool_calls=1` so the loop terminates
    after the forced tool fires.
  * **Provider coverage varies.** All major OpenAI-compatible relays
    (OpenAI, OpenRouter, Together, vLLM) support both string forms and
    the `{"type": "tool", "name": "X"}` shape. Some vendor-specific
    backends only support the strings. agent-driver passes the value
    through unchanged so the provider's own error message is what
    surfaces on a bad choice.

## Tests

  * `tests/contracts/test_runtime_contracts.py` — accepts string forms,
    accepts `{"type": "tool", "name": "X"}`, rejects non-JSON payloads,
    round-trips via `model_dump`/`model_validate`.
  * `tests/runtime/test_run_input_tool_choice.py` — the runtime
    actually plumbs the value to the provider, and the inner-loop
    safety rail still wins.
