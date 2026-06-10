"""Entry points for serving an agent over ACP (stdio by default)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import acp

from agent_driver.adapters.acp.server import AgentAcpServer

if TYPE_CHECKING:
    from agent_driver.sdk.agent import Agent


async def serve_acp_async(
    agent: "Agent",
    *,
    name: str = "agent-driver",
    version: str = "0.1.0",
    input_stream: Any | None = None,
    output_stream: Any | None = None,
    use_unstable_protocol: bool = False,
) -> None:
    """Run the ACP server over the given (default stdin/stdout) streams."""
    server = AgentAcpServer(agent, name=name, version=version)
    await acp.run_agent(
        server,
        input_stream,
        output_stream,
        use_unstable_protocol=use_unstable_protocol,
    )


def serve_acp(
    agent: "Agent",
    *,
    name: str = "agent-driver",
    version: str = "0.1.0",
    use_unstable_protocol: bool = False,
) -> None:
    """Blocking helper that serves an agent over ACP on stdio."""
    asyncio.run(
        serve_acp_async(
            agent,
            name=name,
            version=version,
            use_unstable_protocol=use_unstable_protocol,
        )
    )
