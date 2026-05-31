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

```python
from agent_driver.sdk import ToolSet, create_agent
from agent_driver.runtime import RunnerConfig
from agent_driver.tools import ToolRegistry, tool

async def lookup_city(city: str, limit: int = 3) -> dict:
    """Lookup city facts."""
    return {"city": city, "limit": limit}

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
