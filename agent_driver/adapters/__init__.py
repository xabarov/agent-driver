"""Adapter layer for SSE/CLI stream consumers."""

from agent_driver.adapters.cli import (
    cli_replay_lines,
    cli_run_lines,
    cli_tail_lines,
    cli_tree_lines,
    render_cli_line,
)
from agent_driver.adapters.sse import (
    parse_after_seq,
    render_sse_line,
    sse_event_stream,
    to_sse_envelope,
)

__all__ = [
    "cli_replay_lines",
    "cli_run_lines",
    "cli_tail_lines",
    "cli_tree_lines",
    "parse_after_seq",
    "render_cli_line",
    "render_sse_line",
    "sse_event_stream",
    "to_sse_envelope",
]
