"""Per-session state for the ACP adapter."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AcpSession:
    """Binds one ACP session to a runtime thread and working directory."""

    session_id: str
    thread_id: str
    cwd: str | None = None
