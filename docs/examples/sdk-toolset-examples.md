# SDK + ToolSet Examples

Ниже короткие сценарии, которые покрывают минимальный app-facing путь через
`agent_driver.sdk` и `ToolSet`.

## 1) Агент без инструментов

```python
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet

agent = create_agent(
    provider=FakeProvider(response_text="ok"),
    tools=ToolSet.only(),  # empty surface
)
```

## 2) Агент с одним custom tool

```python
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import RunnerConfig
from agent_driver.contracts import ToolManifest
from agent_driver.sdk import build_default_registry, create_agent
from agent_driver.tools import ToolSet

registry = build_default_registry()

async def hello_tool(args):
    return {"summary": f"hello {args.get('name', 'world')}"}

registry.register(
    ToolManifest(name="hello_tool", description="Simple hello tool"),
    hello_tool,
)

agent = create_agent(
    provider=FakeProvider(response_text="ok"),
    config=RunnerConfig(tool_registry=registry),
    tools=ToolSet.only("hello_tool"),
)
```

## 3) Built-in pack + custom tool

```python
from agent_driver.contracts import ToolRisk
from agent_driver.runtime import RunnerConfig

toolset = ToolSet.packs("web").with_max_risk(ToolRisk.MEDIUM)
agent = create_agent(provider=provider, config=RunnerConfig(tool_registry=registry), tools=toolset)
```

## 4) Streaming facade

```python
from agent_driver.contracts import AgentRunInput

async for event in agent.stream(
    AgentRunInput(
        input="search docs",
        run_id="run_stream_1",
        agent_id="agent",
        graph_preset="single_react",
        stream=True,
    )
):
    print(event.seq, event.event)
```

## 5) Streaming to SSE

```python
from agent_driver.adapters import sse_event_stream
from agent_driver.contracts import AgentRunInput

run_input = AgentRunInput(
    input="stream over sse",
    run_id="run_sse_1",
    agent_id="agent",
    graph_preset="single_react",
    stream=True,
)

async for frame in sse_event_stream(
    agent=agent,
    run_input=run_input,
    event_log=agent.runner.deps.event_log,
    last_event_id="run_sse_1:12",  # optional reconnect
):
    yield frame  # send via FastAPI StreamingResponse
```

## 6) CLI replay/tail

```python
from agent_driver.adapters import cli_replay_lines, cli_tail_lines

event_log = agent.runner.deps.event_log
for line in cli_replay_lines(event_log, run_id="run_stream_1"):
    print(line)

for line in cli_tail_lines(event_log, run_id="run_stream_1", last_n=10):
    print(line)
```

## 7) Resume approval via SDK shortcut

```python
paused = await agent.run(...)
if paused.interrupt is not None:
    resumed = await agent.approve(
        run_id=paused.run_id,
        interrupt_id=paused.interrupt.interrupt_id,
    )
```

## 8) Postgres-backed runtime store

```python
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.postgres_store import (
    PostgresRuntimeStore,
    PostgresRuntimeStoreConfig,
)
from agent_driver.sdk import create_agent

store = PostgresRuntimeStore(
    config=PostgresRuntimeStoreConfig(
        dsn="postgresql://agent:agent@127.0.0.1:55432/agent_driver",
    )
)

agent = create_agent(
    provider=FakeProvider(response_text="ok"),
    checkpoint_store=store,
    event_log=store,
)
```
