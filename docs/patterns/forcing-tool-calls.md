# Forcing Tool Calls

Use `AgentRunInput.tool_choice` when prompt guidance is not enough and the next
LLM call must produce a tool call or must avoid tools.

## Accepted Shapes

- `None` - provider default, usually auto.
- `"auto"` - model decides.
- `"required"` - model must call some visible tool.
- `"none"` - model must produce text only.
- `{"type": "tool", "name": "tool_name"}` - provider-neutral request to call a
  specific tool.
- `{"type": "function", "function": {"name": "tool_name"}}` - OpenAI-native
  shape, passed through for callers that intentionally target that backend.

The provider-neutral dict uses Anthropic's shape. OpenAI-compatible providers
translate it to the function envelope internally.

## When To Use It

Good fits:

- a focused retry after the model promised an artifact but skipped the tool;
- a one-shot typed extraction through a schema-like tool;
- a constrained subagent that must call one tool before returning;
- an allowlisted tool group plus `"required"` when any data tool is acceptable.

Bad fits:

- ordinary chat where the model should decide whether tools are needed;
- hiding policy mistakes that should instead be fixed in `ToolPolicyInput`;
- forcing repeated tool calls after the runtime has already detected a loop.

## Example

```python
from agent_driver.contracts import AgentRunInput

run_input = AgentRunInput(
    input="Search the web for current Phoenix tracing docs.",
    agent_id="agent.default",
    graph_preset="single_react",
    tool_choice={"type": "tool", "name": "web_search"},
    max_tool_calls=1,
)
```

For a category-level choice, combine schema filtering with `"required"`:

```python
from agent_driver.contracts import AgentRunInput, ToolPolicyInput
from agent_driver.contracts.enums import ToolPolicyMode

run_input = AgentRunInput(
    input="Use one search tool and return the result.",
    agent_id="agent.default",
    graph_preset="single_react",
    tool_choice="required",
    tool_policy=ToolPolicyInput(
        mode=ToolPolicyMode.ALLOW_TOOLS,
        allowed_tools=["web_search", "web_fetch"],
    ),
    max_tool_calls=1,
)
```

## Runtime Interaction

The runtime can override caller `tool_choice` when a safety rail must win. For
example, repeated zero-result loops or deliverable final-answer guards can set
`tool_choice="none"` to force a final answer. Caller-provided `tool_choice` is
the starting preference, not a way to disable runtime safety.

## Related Tests

- `tests/contracts/test_runtime_contracts.py`
- `tests/llm/test_tool_choice_normalization.py`
- `tests/runtime/test_run_input_tool_choice.py`
- `tests/runtime/test_tool_schema_filtering.py`
