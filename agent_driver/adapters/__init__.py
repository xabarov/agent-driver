"""Adapter layer for SSE/CLI stream consumers."""

from agent_driver.adapters.cli import (
    cli_follow_lines,
    cli_replay_lines,
    cli_run_lines,
    cli_tail_lines,
    cli_tree_lines,
    render_cli_line,
)
from agent_driver.adapters.cli_rich import (
    cli_run_live_lines,
    is_rich_available,
    render_cli_live_line,
    render_rich_event,
    render_rich_event_text,
    rich_run_live,
)
from agent_driver.adapters.sse import (
    AssistantTextCapture,
    parse_after_seq,
    parse_sse_data_payload,
    render_sse_line,
    sse_event_stream,
    to_sse_envelope,
)
from agent_driver.adapters.sanitize import (
    REDACTED,
    TRUNCATED,
    sanitize_projection_value,
    should_redact_key,
    should_truncate_raw_payload_key,
)
from agent_driver.adapters.warnings import project_warning_event

__all__ = [
    "cli_follow_lines",
    "cli_replay_lines",
    "cli_run_live_lines",
    "cli_run_lines",
    "cli_tail_lines",
    "cli_tree_lines",
    "AssistantTextCapture",
    "is_rich_available",
    "parse_after_seq",
    "parse_sse_data_payload",
    "project_warning_event",
    "REDACTED",
    "render_cli_live_line",
    "render_cli_line",
    "render_rich_event",
    "render_rich_event_text",
    "render_sse_line",
    "rich_run_live",
    "sanitize_projection_value",
    "should_redact_key",
    "should_truncate_raw_payload_key",
    "sse_event_stream",
    "to_sse_envelope",
    "TRUNCATED",
]
