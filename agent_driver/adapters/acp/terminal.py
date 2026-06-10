"""Run the agent's shell commands in the ACP client's (editor's) terminal.

When the connected ACP client advertises the ``terminal`` capability, the
builtin ``bash`` tool's execution is routed here instead of a local subprocess:
the command runs in the editor's terminal pane (visible to the user) via the
``terminal/*`` lifecycle — ``create`` → ``wait_for_exit`` → ``output`` →
``release``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import acp


class AcpTerminalRunner:
    """An ``AsyncCommandRunner`` backed by the ACP client's terminal methods."""

    def __init__(self, conn: "acp.Client", session_id: str) -> None:
        self._conn = conn
        self._session_id = session_id

    async def run_command(
        self, command: str, *, cwd: str, timeout_seconds: float
    ) -> dict[str, Any]:
        """Create a terminal, wait for exit, collect output, then release it.

        ``timeout_seconds`` is advisory here — the editor owns the terminal
        lifecycle; we report a non-timed-out result and surface the editor's
        exit status. Always releases the terminal, even on error.
        """
        created = await self._conn.create_terminal(
            command=command, session_id=self._session_id, cwd=cwd
        )
        terminal_id = created.terminal_id
        try:
            exited = await self._conn.wait_for_terminal_exit(
                session_id=self._session_id, terminal_id=terminal_id
            )
            out = await self._conn.terminal_output(
                session_id=self._session_id, terminal_id=terminal_id
            )
        finally:
            await self._conn.release_terminal(
                session_id=self._session_id, terminal_id=terminal_id
            )
        exit_code = int(getattr(exited, "exit_code", 0) or 0)
        return {
            # The editor terminal reports a single combined stream.
            "stdout": getattr(out, "output", "") or "",
            "stderr": "",
            "timed_out": False,
            "exit_code": exit_code,
        }


def client_terminal_enabled(client_capabilities: object | None) -> bool:
    """Whether the ACP client advertised the ``terminal`` capability."""
    return bool(getattr(client_capabilities, "terminal", False))


__all__ = ["AcpTerminalRunner", "client_terminal_enabled"]
