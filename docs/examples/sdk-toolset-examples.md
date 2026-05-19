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

## 6.1) Live CLI logs (rich + fallback)

```python
from agent_driver.adapters import cli_run_live_lines, is_rich_available

prefer_rich = is_rich_available()
async for line in cli_run_live_lines(
    agent.stream(run_input),
    prefer_rich=prefer_rich,
):
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

## 9) Product CLI baseline (`run/replay/tail/tree`)

Install optional rich output first:

```bash
pip install -e .[cli]
```

Run one prompt with deterministic fake provider and persist events to sqlite:

```bash
agent-driver run "Summarize this test run" \
  --store-kind sqlite \
  --sqlite-path ./.runtime_store.sqlite3 \
  --run-id run_demo_1 \
  --plain
```

Replay and inspect the same run:

```bash
agent-driver replay --run-id run_demo_1 --store-kind sqlite --sqlite-path ./.runtime_store.sqlite3
agent-driver tail --run-id run_demo_1 --last-n 10 --store-kind sqlite --sqlite-path ./.runtime_store.sqlite3
agent-driver tree --run-id run_demo_1 --store-kind sqlite --sqlite-path ./.runtime_store.sqlite3
```

Follow mode (for in-progress runs) polls durable runtime events and continues
until terminal event:

```bash
agent-driver tail --run-id run_demo_1 --follow --poll-interval-ms 200 \
  --store-kind sqlite --sqlite-path ./.runtime_store.sqlite3
```

## 10) Terminal chat CLI (`agent-driver chat`)

Start interactive chat loop with deterministic fake provider:

```bash
agent-driver chat --plain --store-kind sqlite --sqlite-path ./.runtime_store.sqlite3
```

Inside chat:

```text
hello
/runs
/replay
/tail
/help
/exit
```

Notes:
- each user turn gets a new `run_id` under one chat `thread_id`;
- `/replay` and `/tail` default to the latest run in the session when `run_id`
  is omitted.

## 11) Real provider wiring for CLI

OpenAI-compatible (OpenRouter-style) via env:

```bash
AGENT_DRIVER_OPENAI_API_KEY=... \
AGENT_DRIVER_OPENAI_BASE_URL=https://openrouter.ai/api/v1 \
AGENT_DRIVER_OPENAI_MODEL=openai/gpt-4o-mini \
agent-driver chat --provider openai-compatible --provider-healthcheck
```

Explicit API key env variable selection:

```bash
agent-driver run "Summarize latest changes" \
  --provider openai-compatible \
  --base-url https://openrouter.ai/api/v1 \
  --model openai/gpt-4o-mini \
  --api-key-env OPENROUTER_API_KEY
```

Local Ollama:

```bash
agent-driver chat --provider ollama --base-url http://localhost:11434 --model llama3.2:3b
```
