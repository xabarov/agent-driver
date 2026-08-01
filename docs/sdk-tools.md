# SDK Tools

The SDK has three tool paths:

- select built-ins with `ToolSet`;
- register plain async Python functions with `tool(...)`;
- import external catalog entries with `register_contract_tool(...)`.

## Selecting Tools

```python
from agent_driver.sdk import ToolSet, create_agent

agent = create_agent(provider=provider, tools=ToolSet.packs("web", "planning"))
```

Common selectors:

- `ToolSet.only("web_search", "web_fetch")`
- `ToolSet.packs("web")`
- `ToolSet.from_preset("safe")`
- `ToolSet.all()`

## Custom Function Tool

The shortest path — `agent.add_tool(...)` — registers the tool on an existing
agent and makes it callable on the next turn. No separate registry construction,
and no `ToolSet.only(...)` that must be kept in sync (registering but forgetting to
select was the classic foot-gun):

```python
from agent_driver.sdk import create_agent, ToolSet

agent = create_agent(provider=provider, tools=ToolSet.only())

async def lookup_city(city: str, limit: int = 3) -> dict:
    """Lookup city facts."""
    return {"city": city, "limit": limit}

agent.add_tool(lookup_city)          # callable immediately
# or as a decorator: @agent.add_tool(name="lookup_city")
```

`add_tool` accepts an async function (its name, description and JSON schema are
inferred from the signature) or a `tool(...)` definition, and returns the registered
`ToolManifest`.

### Building a registry explicitly

When you need a registry up front (e.g. to share across agents or pre-validate a
`ToolSet`), the primitives are re-exported from `agent_driver.sdk`:

```python
from agent_driver.sdk import ToolSet, create_agent, tool, ToolRegistry
from agent_driver.runtime import RunnerConfig

definition = tool(lookup_city)
registry = ToolRegistry()
registry.register(definition.manifest, definition.handler)

agent = create_agent(
    provider=provider,
    config=RunnerConfig(tool_registry=registry),
    tools=ToolSet.only("lookup_city"),
)
```

`tool(...)` infers the tool name, description, JSON schema types and signature
defaults. The registry exposes `catalog(projection="sdk"|"prompt"|"full")` for
SDK/UI catalog views.
