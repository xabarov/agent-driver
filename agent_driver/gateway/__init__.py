"""Headless, session-routed gateway over an Agent (run + approval lifecycle)."""

from agent_driver.gateway.events import GatewayEvent, GatewayEventKind
from agent_driver.gateway.gateway import AgentGateway, GatewayError

__all__ = [
    "AgentGateway",
    "GatewayError",
    "GatewayEvent",
    "GatewayEventKind",
]
