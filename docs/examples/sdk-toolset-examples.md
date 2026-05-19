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

## 3.1) Typed custom tool via decorator/builder

```python
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import RunnerConfig
from agent_driver.sdk import build_default_registry, create_agent
from agent_driver.tools import ToolSet, custom_tool, register_custom_tool

@custom_tool(
    remediation_hints=["Pass city and units when response is empty."],
)
async def weather_lookup(city: str, units: str = "metric") -> dict[str, object]:
    return {"summary": f"{city}:{units}"}

registry = build_default_registry()
register_custom_tool(registry, weather_lookup)
agent = create_agent(
    provider=FakeProvider(response_text="ok"),
    config=RunnerConfig(tool_registry=registry),
    tools=ToolSet.only("weather_lookup"),
)
```

## 3.2) Code-agent compatible tool naming

```python
from agent_driver.contracts import AgentProfile

toolset = ToolSet.only("weather_lookup").with_profile(AgentProfile.CODE_AGENT)
agent = create_agent(
    provider=FakeProvider(response_text="ok"),
    config=RunnerConfig(tool_registry=registry),
    tools=toolset,
)
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

## 8.1) MCP allowlisted subset via merged pool

```python
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet

agent = create_agent(provider=FakeProvider(response_text="ok"), tools=ToolSet.only("mcp_tool"))

# Per-call allowlist is passed via tool args:
# {"tool_name": "mcp_tool", "args": {"server": "...", "tool_name": "...", "tool_allowlist": ["..."]}}
```

## 8.2) ToolSet safety helpers (`without`, fail-fast unknown names)

```python
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet

# Start from a pack and remove one tool explicitly.
safe_web = ToolSet.packs("web").without("web_search")
agent = create_agent(provider=FakeProvider(response_text="ok"), tools=safe_web)

# ToolSet.only validates names during create_agent(...):
# create_agent(..., tools=ToolSet.only("missing_tool")) -> ValueError
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

OpenRouter via unified env:

```bash
AGENT_DRIVER_PROVIDER=openrouter \
AGENT_DRIVER_API_KEY=... \
AGENT_DRIVER_BASE_URL=https://openrouter.ai/api/v1 \
AGENT_DRIVER_MODEL=openai/gpt-4o-mini \
agent-driver chat --provider openrouter --provider-healthcheck
```

Explicit key/base/model via flags:

```bash
agent-driver run "Summarize latest changes" \
  --provider openrouter \
  --base-url https://openrouter.ai/api/v1 \
  --model openai/gpt-4o-mini \
  --api-key "$AGENT_DRIVER_API_KEY"
```

Local Ollama:

```bash
agent-driver chat --provider ollama --base-url http://localhost:11434 --model llama3.2:3b
```

## 12) Tool surface selection in `run/chat`

Safe useful default (read/search/web/planning):

```bash
agent-driver chat --provider openrouter --tools default
```

Disable tools entirely:

```bash
agent-driver chat --provider openrouter --tools none
```

Exact tools:

```bash
agent-driver run "Search docs and summarize" \
  --provider openrouter \
  --tool read_file \
  --tool grep_search \
  --tool web_search
```

Tool packs:

```bash
agent-driver chat \
  --provider openrouter \
  --tool-pack filesystem_read \
  --tool-pack web \
  --tool-pack planning
```

Dangerous tool packs require explicit opt-in:

```bash
agent-driver chat --provider openrouter --tool-pack shell --allow-dangerous-tools
```

Inside chat use `/tools` and `/tools verbose` to inspect selected tool surface.

## 13) Provider tool-calling bridge (OpenRouter / vLLM)

When `--provider openrouter` or `--provider vllm` is used with non-empty tool surface, CLI now
passes OpenAI-compatible function schemas and runtime can execute tool calls.

```bash
AGENT_DRIVER_PROVIDER=openrouter \
AGENT_DRIVER_API_KEY=... \
AGENT_DRIVER_BASE_URL=https://openrouter.ai/api/v1 \
AGENT_DRIVER_MODEL=openai/gpt-4o-mini \
agent-driver chat --provider openrouter --tools default --plain
```

Inside chat:

```text
/tools
поищи в интернете новости о Илоне Маске
```

Expected behavior: model can emit tool calls, runtime executes selected tool(s),
then requests follow-up model response with tool results context.

## 14) Tool-loop hardening smoke checks

Offline deterministic regression command:

```bash
.venv/bin/python -m pytest \
  tests/cli/test_chat.py \
  tests/cli/test_main.py \
  tests/sdk/test_sdk_agent.py \
  tests/llm/test_provider_normalization.py -q
```

Optional live manual check for bounded tool-calling behavior:

```bash
agent-driver chat --provider openrouter --tools default --plain \
  --max-steps 8 --max-tool-calls 4 --deadline-seconds 60
```

Expected live behavior: no infinite `tool=?` loop; run finishes with either
final assistant text or bounded failure (`run_failed reason=...`) with compact
tool event lines.

## 15) CLI productization workflows

Show resolved CLI config (flags/env/config precedence):

```bash
agent-driver config show
```

Doctor diagnostics (safe summary + optional live check):

```bash
agent-driver doctor --provider openrouter --live-check
```

Resume pending interrupt from CLI:

```bash
agent-driver resume approve --run-id run_123 --interrupt-id interrupt_456 --provider openrouter
```

Inspect and export run artifacts:

```bash
agent-driver inspect --run-id run_123 --format json --store-kind sqlite --sqlite-path ./.runtime_store.sqlite3
agent-driver export --run-id run_123 --format markdown --output ./run_123.md --store-kind sqlite --sqlite-path ./.runtime_store.sqlite3
```

Session metadata management:

```bash
agent-driver sessions list
agent-driver sessions show --session-id session_abc123
```

## 16) CLI live evaluation and trace inspect

Run 10-scenario eval suite (live mode with env gate):

```bash
AGENT_DRIVER_RUN_LIVE_CLI_EVALS=1 \
agent-driver eval run \
  --provider openrouter \
  --output-dir .agent-driver/evals
```

Offline baseline (deterministic fake provider):

```bash
agent-driver eval run --provider fake --offline --output-dir .agent-driver/evals
```

Inspect eval bundle summaries:

```bash
agent-driver eval inspect --summary-json .agent-driver/evals/<timestamp>/summary.json
agent-driver eval inspect --summary-json .agent-driver/evals/<timestamp>/summary.json --scenario-id news_web_search
```

Inspect one scenario artifact timeline:

```bash
agent-driver eval inspect --artifact-json .agent-driver/evals/<timestamp>/news_web_search.json
```

## 17) Backend-only recipes

For non-CLI backend embedding (FastAPI/SSE, runtime store factory, MCP catalog,
persisted replay support bundle), see:

- [`sdk-backend-recipes.md`](sdk-backend-recipes.md)
