"""ACP (Agent Client Protocol) adapter.

Importing this package requires the optional ``agent-client-protocol``
dependency (install ``agent-driver[acp]``). The core import graph never pulls
it in; only code that opts into ACP imports this package.
"""

from __future__ import annotations

from agent_driver.adapters.acp.entry import serve_acp, serve_acp_async
from agent_driver.adapters.acp.server import AgentAcpServer

__all__ = ["AgentAcpServer", "serve_acp", "serve_acp_async"]
