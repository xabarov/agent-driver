"""Serve an agent over MCP Streamable-HTTP, offline.

Phase 3 platform adapter: the transport-agnostic ``AgentMcpServer`` JSON-RPC
core (the same one ``serve_stdio`` drives) is served over HTTP at ``/mcp`` so
remote MCP clients can reach it without stdio. In production you run it via
``agent-driver serve --mcp`` (which mounts ``/mcp`` next to the OpenAI
``/v1/...`` surface on one ASGI app). Here we drive the standalone MCP app
in-process with Starlette's ``TestClient`` — no open port, no network.

    python examples/cookbook/18_mcp_http_server.py

Requires the optional dependencies: ``pip install 'agent-driver[server]'``.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.mcp_server.http import SESSION_HEADER, create_mcp_app
from agent_driver.sdk import ToolSet, create_agent


def rpc(method: str, params: dict | None = None, *, id: int = 1) -> dict:
    body: dict = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        body["params"] = params
    return body


def main() -> None:
    agent = create_agent(
        provider=FakeProvider(response_text="Answer from the MCP server."),
        tools=ToolSet.only(),
    )
    client = TestClient(create_mcp_app(agent, server_name="agent-driver-demo"))

    init = client.post(
        "/mcp", json=rpc("initialize", {"protocolVersion": "2025-03-26"})
    )
    print("initialize     ->", init.json()["result"]["serverInfo"])
    print("session id     ->", init.headers.get(SESSION_HEADER))

    tools = client.post("/mcp", json=rpc("tools/list", id=2))
    print("tools          ->", [t["name"] for t in tools.json()["result"]["tools"]])

    call = client.post(
        "/mcp",
        json=rpc(
            "tools/call", {"name": "agent_query", "arguments": {"input": "hi"}}, id=3
        ),
    )
    print("agent_query    ->", call.json()["result"]["content"][0]["text"])


if __name__ == "__main__":
    main()
