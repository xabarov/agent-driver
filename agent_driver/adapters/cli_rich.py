"""Rich-oriented CLI rendering with plain-text fallback."""

from __future__ import annotations

from collections.abc import AsyncIterator
import json
from typing import Any

from agent_driver.adapters.cli import render_cli_line
from agent_driver.contracts.stream import RunStreamEvent

try:  # pragma: no cover - optional dependency branch
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text

    _RICH_AVAILABLE = True
    _RICH_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - optional dependency branch
    Console = Any  # type: ignore[assignment,misc]
    Panel = Any  # type: ignore[assignment,misc]
    Text = Any  # type: ignore[assignment,misc]
    _RICH_AVAILABLE = False
    _RICH_IMPORT_ERROR = exc


_LIFECYCLE_EVENTS = {
    "run_started",
    "run_completed",
    "run_failed",
    "run_cancelled",
    "run_paused",
    "run_resumed",
}
_LLM_EVENTS = {"llm_call_started", "llm_call_completed"}
_TOOL_EVENTS = {"tool_call_started", "tool_call_completed"}


def is_rich_available() -> bool:
    """Return True when optional rich dependency is importable."""
    return _RICH_AVAILABLE


def _truncate_payload(data: dict[str, Any], *, max_chars: int) -> str:
    text = json.dumps(data, ensure_ascii=True, sort_keys=True)
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}..."


def render_rich_event_text(
    event: RunStreamEvent,
    *,
    token_state: dict[str, str] | None = None,
    max_payload_chars: int = 160,
) -> str:
    """Render one event to readable bounded plain text."""
    payload_text = _truncate_payload(event.data, max_chars=max_payload_chars)
    name = event.event
    if name in _LIFECYCLE_EVENTS:
        return f"[{event.seq:04d}] RUN {name}: {payload_text}"
    if name in _LLM_EVENTS:
        provider = event.data.get("provider")
        model = event.data.get("model")
        suffix = f" provider={provider} model={model}" if provider or model else ""
        return f"[{event.seq:04d}] LLM {name}:{suffix} payload={payload_text}"
    if name == "token_delta":
        delta = str(event.data.get("delta_text") or "")
        state = token_state if token_state is not None else {}
        accumulated = f"{state.get('tokens', '')}{delta}"
        state["tokens"] = accumulated
        preview = accumulated[-80:].replace("\n", "\\n")
        return (
            f"[{event.seq:04d}] TOKEN +{len(delta)} chars "
            f"total={len(accumulated)} preview='{preview}'"
        )
    if name in _TOOL_EVENTS:
        tool_name = event.data.get("tool_name", "?")
        status = event.data.get("status")
        status_part = f" status={status}" if status else ""
        return (
            f"[{event.seq:04d}] TOOL {name} tool={tool_name}{status_part} "
            f"payload={payload_text}"
        )
    if name in {"interrupt_requested", "run_paused"}:
        reason = event.data.get("reason", "unknown")
        return f"[{event.seq:04d}] INTERRUPT reason={reason} payload={payload_text}"
    if name == "warning":
        kind = event.data.get("kind", "warning")
        return f"[{event.seq:04d}] WARNING kind={kind} payload={payload_text}"
    return f"[{event.seq:04d}] EVENT {name}: {payload_text}"


def _style_for_event(name: str) -> str:
    if name in {"run_completed", "tool_call_completed"}:
        return "green"
    if name in {"run_failed", "run_cancelled", "interrupt_requested"}:
        return "bold red"
    if name in {"warning", "run_paused"}:
        return "yellow"
    if name in {"token_delta"}:
        return "cyan"
    return "white"


def render_rich_event(
    event: RunStreamEvent,
    *,
    token_state: dict[str, str] | None = None,
    max_payload_chars: int = 160,
) -> Any:
    """Render one event into rich renderable object."""
    if not _RICH_AVAILABLE:
        raise RuntimeError(
            "Rich renderer is unavailable. Install optional dependency: agent-driver[cli]."
        ) from _RICH_IMPORT_ERROR
    text = Text(
        render_rich_event_text(
            event, token_state=token_state, max_payload_chars=max_payload_chars
        ),
        style=_style_for_event(event.event),
    )
    if event.event in _LIFECYCLE_EVENTS or event.event in {"interrupt_requested", "warning"}:
        return Panel(text, title=event.event, border_style=_style_for_event(event.event))
    return text


def render_cli_live_line(
    event: RunStreamEvent,
    *,
    prefer_rich: bool = True,
    token_state: dict[str, str] | None = None,
    max_payload_chars: int = 160,
) -> str:
    """Render one line for live terminal output with optional rich UX."""
    if not prefer_rich:
        return render_cli_line(event)
    if not _RICH_AVAILABLE:
        return render_cli_line(event)
    console = Console(
        record=True,
        force_terminal=False,
        color_system="standard",
        width=120,
    )
    console.print(
        render_rich_event(
            event, token_state=token_state, max_payload_chars=max_payload_chars
        )
    )
    text = console.export_text(styles=False).strip()
    return text or render_cli_line(event)


async def cli_run_live_lines(
    stream: AsyncIterator[RunStreamEvent],
    *,
    prefer_rich: bool = True,
    max_payload_chars: int = 160,
) -> AsyncIterator[str]:
    """Yield live CLI lines with rich rendering when available."""
    token_state: dict[str, str] = {}
    async for event in stream:
        yield render_cli_live_line(
            event,
            prefer_rich=prefer_rich,
            token_state=token_state,
            max_payload_chars=max_payload_chars,
        )


async def rich_run_live(
    stream: AsyncIterator[RunStreamEvent],
    *,
    console: Any | None = None,
    max_payload_chars: int = 160,
) -> None:
    """Render stream directly to rich console."""
    if not _RICH_AVAILABLE:
        raise RuntimeError(
            "Rich renderer is unavailable. Install optional dependency: agent-driver[cli]."
        ) from _RICH_IMPORT_ERROR
    target = console or Console()
    token_state: dict[str, str] = {}
    async for event in stream:
        target.print(
            render_rich_event(
                event,
                token_state=token_state,
                max_payload_chars=max_payload_chars,
            )
        )


__all__ = [
    "cli_run_live_lines",
    "is_rich_available",
    "render_cli_live_line",
    "render_rich_event",
    "render_rich_event_text",
    "rich_run_live",
]
