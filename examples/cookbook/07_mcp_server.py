"""MCP server: expose the agent to external MCP clients.

``AgentMcpServer.handle_request`` is a transport-agnostic JSON-RPC handler;
``serve_stdio(server)`` would bind it to stdin/stdout for a real client. Here
we drive it with in-process JSON-RPC requests.

    python examples/cookbook/07_mcp_server.py
"""

from __future__ import annotations

import asyncio

from agent_driver.llm import FakeProvider
from agent_driver.mcp_server import AgentMcpServer
from agent_driver.sdk import ToolSet, create_agent


async def main() -> None:
    agent = create_agent(
        provider=FakeProvider(response_text="42"), tools=ToolSet.only()
    )
    server = AgentMcpServer(agent)

    tools = await server.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )
    print("tools:", [t["name"] for t in tools["result"]["tools"]])

    call = await server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "agent_query", "arguments": {"input": "the answer?"}},
        }
    )
    result = call["result"]
    print("isError:", result["isError"])
    print("answer:", result["content"][0]["text"])


if __name__ == "__main__":
    asyncio.run(main())
