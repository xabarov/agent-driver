"""A2A (Agent2Agent) adapter — expose an agent to other agents over A2A.

The JSON-RPC core (:class:`A2aServer`) is dependency-free; the HTTP transport
(:mod:`agent_driver.adapters.a2a.http`) needs the ``[server]`` extra (Starlette)
and is imported separately.
"""

from __future__ import annotations

from agent_driver.adapters.a2a.server import A2A_PROTOCOL_VERSION, A2aServer

__all__ = ["A2aServer", "A2A_PROTOCOL_VERSION"]
