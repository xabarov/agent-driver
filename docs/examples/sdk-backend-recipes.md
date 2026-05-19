# SDK Backend Recipes

Ниже examples для backend embedding (без CLI workflows).

## 1) FastAPI + SSE stream endpoint

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from agent_driver.adapters import sse_event_stream
from agent_driver.contracts import AgentRunInput
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet

app = FastAPI()
agent = create_agent(provider=FakeProvider(response_text="ok"), tools=ToolSet.only("web_search"))


@app.get("/chat/stream")
async def chat_stream(q: str):
    run_input = AgentRunInput(
        input=q,
        run_id="run_backend_stream_1",
        agent_id="backend-agent",
        graph_preset="single_react",
        stream=True,
    )

    async def _frames():
        async for frame in sse_event_stream(
            agent=agent,
            run_input=run_input,
            event_log=agent.runner.deps.event_log,
        ):
            yield frame

    return StreamingResponse(_frames(), media_type="text/event-stream")
```

## 2) Postgres runtime store factory from env

```python
from agent_driver.runtime.storage.factory import (
    create_runtime_store_bundle,
    runtime_store_config_from_env,
)
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import create_agent

cfg = runtime_store_config_from_env()
bundle = create_runtime_store_bundle(cfg)
agent = create_agent(
    provider=FakeProvider(response_text="ok"),
    checkpoint_store=bundle.checkpoint_store,
    event_log=bundle.event_log,
)
```

Required env for Postgres:

```bash
export AGENT_DRIVER_RUNTIME_STORE_KIND=postgres
export AGENT_DRIVER_POSTGRES_DSN=postgresql://agent:agent@127.0.0.1:55432/agent_driver
export AGENT_DRIVER_POSTGRES_SCHEMA=public
export AGENT_DRIVER_POSTGRES_CONNECT_TIMEOUT_SECONDS=5
export AGENT_DRIVER_POSTGRES_APPLICATION_NAME=agent_driver_backend
```

## 3) One custom tool + one built-in pack

```python
from agent_driver.contracts import ToolManifest
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import RunnerConfig
from agent_driver.sdk import build_default_registry, create_agent
from agent_driver.tools import ToolSet
from agent_driver.contracts import ToolRisk

registry = build_default_registry()

async def ticket_lookup(args):
    ticket_id = args.get("ticket_id", "")
    return {"summary": f"ticket {ticket_id} is in progress"}

registry.register(
    ToolManifest(name="ticket_lookup", description="Fetch ticket status"),
    ticket_lookup,
)

agent = create_agent(
    provider=FakeProvider(response_text="ok"),
    config=RunnerConfig(tool_registry=registry),
    tools=ToolSet.packs("web").with_max_risk(ToolRisk.MEDIUM),
)
```

## 4) MCP tools from JSON descriptor catalog

```python
from agent_driver.contracts import AgentRunInput, ToolCall
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet

agent = create_agent(
    provider=FakeProvider(response_text="ok"),
    tools=ToolSet.only("mcp_tool"),
)

output = await agent.run(
    AgentRunInput(
        input="lookup docs",
        run_id="run_mcp_catalog_1",
        agent_id="backend-agent",
        graph_preset="single_react",
        tool_policy={
            "metadata": {
                "planned_tool_calls": [
                    ToolCall(
                        tool_name="mcp_tool",
                        args={
                            "server": "ext-docs",
                            "tool_name": "lookup",
                            "catalog_json_path": "./mcp_catalog.json",
                            "tool_allowlist": ["lookup"],
                        },
                    ).model_dump(mode="json")
                ]
            }
        },
    )
)
```

## 5) Persisted replay + support bundle

```python
from agent_driver.evals import replay_from_persisted
from agent_driver.observability import build_persisted_support_bundle

persisted = replay_from_persisted(
    run_id="run_backend_stream_1",
    event_log=agent.runner.deps.event_log,
    checkpoint_store=agent.runner.deps.checkpoint_store,
)
bundle = build_persisted_support_bundle(persisted)
```

`build_persisted_support_bundle(...)` redacts sensitive keys (`token`, `secret`,
`password`, `api_key`, `auth`) for operator-safe ticket attachments.

## 6) Durable subagent rows via sqlite store

```python
from agent_driver.runtime import RunnerConfig
from agent_driver.subagents import SqliteSubagentStore
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import create_agent

runner_config = RunnerConfig(
    subagent_store=SqliteSubagentStore(path="./.subagents.sqlite3"),
    enable_subagents=True,
)
agent = create_agent(
    provider=FakeProvider(response_text="ok"),
    config=runner_config,
)
```
