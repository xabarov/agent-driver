# SDK Streaming

OpenAI-compatible providers optionally accept `Config.transport_diagnostics=True`.
The extra content-free diagnostic stream event is folded into response metadata
and the runner's `llm_call_completed.provider_transport`. Off by default; times
are monotonic client-observed milliseconds from that stream invocation. Attempt
headers, first SSE data (possibly `[DONE]`), first public text, first private
reasoning presence and stream end are distinct. A missing field is unobserved,
not zero. This does not partition provider queue/prefill/compute; transport retries
within a call are listed separately, and a validated generation ID enables a
provider-side readback. No prompt, response, headers or reasoning text is stored
in this metadata field.

For object-style streaming, use `RunStream`:

```python
stream = agent.stream_run(
    AgentRunInput(
        input="Write a short answer",
        run_id="run_stream",
        agent_id="agent",
        graph_preset="single_react",
        stream=True,
    )
)

async for delta in stream.text_deltas():
    print(delta, end="")

output = await stream.final_output()
```

For lower-level event iteration:

```python
async for event in stream.events():
    print(event.event, event.data)
```

For a background run without consuming a stream, use `Agent.start(...)`:

```python
handle = agent.start(run_input)
events = handle.events()
output = await handle.final()
checkpoint = handle.checkpoint()
```

The stream reads the durable runtime event log, so callers can reconnect by
tracking the last seen sequence and reading later events from the handle.
