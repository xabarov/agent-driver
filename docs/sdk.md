# SDK

The SDK is the product-facing surface over the runtime. Prefer it over direct
`SingleAgentRunner` wiring in applications.

```python
from agent_driver.llm import FakeProvider
from agent_driver.sdk import ToolSet, create_agent

agent = create_agent(
    provider=FakeProvider(response_text="ok"),
    tools=ToolSet.only(),
)
output = await agent.query("Summarize this task", run_id="run_1")
print(output.answer)
```

Core entrypoints:

- `create_agent(...)` builds an `Agent` facade with stores, tool registry and
  governed execution wired.
- `query(...)` is a one-shot helper for simple integrations.
- `Agent.query(...)` and `Agent.run_text(...)` accept plain text.
- `Agent.run(...)` accepts a full `AgentRunInput` for advanced control.
- `Agent.session(...)` returns a thread-scoped `Session`.
- `Agent.start(...)`, `Agent.stream_run(...)` and `Agent.stream(...)` expose
  background and streaming workflows.

Output diagnostics:

- `output.context.pressure` is the stable context-pressure state.
- `output.context.recommendation` gives the caller a compact next-action hint.
- `agent.summarize(output)` or `summarize_output(output)` returns
  `TraceSummary`.
- `agent.support_bundle(output)` returns a redacted support-bundle recipe.

See also:

- [SDK sessions](sdk-sessions.md)
- [SDK tools](sdk-tools.md)
- [SDK streaming](sdk-streaming.md)
- [SDK errors](sdk-errors.md)
