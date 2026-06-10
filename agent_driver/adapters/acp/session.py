"""Per-session state for the ACP adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_driver.contracts.messages import ChatMessage
    from agent_driver.runtime.tool_gate import ToolGate


@dataclass
class AcpSession:
    """Binds one ACP session to a runtime thread and working directory."""

    session_id: str
    thread_id: str
    cwd: str | None = None
    # Permission mode selected via set_session_mode. ``"default"`` means "use
    # the agent's construction-time gate" (no per-run override).
    mode_id: str = "default"
    gate_override: "ToolGate | None" = None
    # Full user/assistant transcript, kept by the adapter so load_session can
    # replay the whole conversation (the runtime's session store persists only
    # assistant turns).
    transcript: list["ChatMessage"] = field(default_factory=list)
