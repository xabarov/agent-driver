"""Context-pressure helpers for LLM-call step."""

from __future__ import annotations

from typing import Any, Protocol

from agent_driver.contracts.enums import ChatRole, RuntimeEventType
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmRequest
from agent_driver.runtime.metadata_state import get_compaction_runtime_state
from agent_driver.runtime.single_agent.step_events import emit_step_event
from agent_driver.runtime.single_agent.types import EventSpec, RunContext


class ContextPressureHost(Protocol):
    """Host surface required for context-pressure warning events."""

    def _emit(self, event: EventSpec) -> None: ...


_CONTEXT_PRESSURE_NUDGES: dict[str, str] = {
    "early_warning": (
        "Runtime context-pressure guidance: context use is no longer low. "
        "Keep a compact running summary of durable findings, preserve concrete "
        "source/file references, and avoid rereading bulky material unless it is "
        "needed for the next step."
    ),
    "delegate_or_summarize": (
        "Runtime context-pressure guidance: context use is in the delegation or "
        "summarization band. Summarize read-heavy findings now, preserve source "
        "refs and artifact ids, delegate separable read-heavy work when the "
        "agent_tool is available, and move toward synthesis instead of expanding "
        "the search surface."
    ),
    "compact_recommended": (
        "Runtime context-pressure guidance: compaction is recommended. Preserve "
        "only task-critical facts, source refs, open decisions and todo state; "
        "then move to synthesis or a compact next action."
    ),
    "blocking": (
        "Runtime context-pressure guidance: context is at the emergency guard. "
        "Do not start broad new reading. Preserve source refs and produce the "
        "smallest viable synthesis or next step."
    ),
}


def request_with_context_pressure_nudge(request: Any, state: str) -> Any:
    """Inject a model-facing context pressure reminder for the current request."""
    nudge = _CONTEXT_PRESSURE_NUDGES.get(state)
    if not nudge or not isinstance(request, LlmRequest):
        return request
    messages = list(request.messages)
    if messages and messages[0].role == ChatRole.SYSTEM:
        messages[0] = messages[0].model_copy(
            update={"content": f"{messages[0].content}\n\n{nudge}"}
        )
    else:
        messages.insert(0, ChatMessage(role=ChatRole.SYSTEM, content=nudge))
    return request.model_copy(update={"messages": messages})


def token_pressure_state(token_pressure: object) -> str:
    """Return normalized context-pressure state from a snapshot payload."""
    if not isinstance(token_pressure, dict):
        return "ok"
    return str(token_pressure.get("state", "ok"))


_TOKEN_PRESSURE_SIGNAL_IDS: dict[str, str] = {
    "early_warning": "context_early_warning",
    "delegate_or_summarize": "context_delegate_or_summarize",
    "warning": "context_above_soft_threshold",
    "compact_recommended": "context_compact_recommended",
    "blocking": "context_blocking_threshold",
}

_TOKEN_PRESSURE_SEVERITIES: dict[str, str] = {
    "early_warning": "info",
    "delegate_or_summarize": "warning",
    "warning": "warning",
    "compact_recommended": "warning",
    "blocking": "critical",
}

_TOKEN_PRESSURE_RECOMMENDATIONS: dict[str, str] = {
    "early_warning": "summarize_findings",
    "delegate_or_summarize": "delegate_or_summarize",
    "warning": "summarize_findings",
    "compact_recommended": "compact_recommended",
    "blocking": "blocking",
}


def emit_token_pressure_warning(
    host: ContextPressureHost, context: RunContext
) -> None:
    """Emit a stream/runtime warning when context-pressure state changes."""
    compaction_state = get_compaction_runtime_state(context)
    token_pressure = compaction_state.token_pressure()
    if not token_pressure:
        return
    state = str(token_pressure.get("state", "ok"))
    previous_state = compaction_state.previous_token_pressure_state()
    if previous_state == state:
        return
    compaction_state.set_previous_token_pressure_state(state)
    if state not in _TOKEN_PRESSURE_SIGNAL_IDS:
        return
    used_tokens_raw = token_pressure.get("used_tokens_estimate")
    window_raw = token_pressure.get("context_window_estimate")
    used_tokens = (
        int(used_tokens_raw) if isinstance(used_tokens_raw, (int, float)) else 0
    )
    window = (
        int(window_raw) if isinstance(window_raw, (int, float)) and window_raw else 0
    )
    context_usage_ratio = token_pressure.get("context_usage_ratio")
    if not isinstance(context_usage_ratio, (int, float)):
        context_usage_ratio = round(used_tokens / window, 4) if window > 0 else None
    payload: dict[str, Any] = {
        "kind": "token_pressure",
        "signal_id": _TOKEN_PRESSURE_SIGNAL_IDS[state],
        "severity": _TOKEN_PRESSURE_SEVERITIES[state],
        "state": state,
        "used_tokens_estimate": token_pressure.get("used_tokens_estimate"),
        "remaining_tokens_estimate": token_pressure.get("remaining_tokens_estimate"),
        "context_window_estimate": token_pressure.get("context_window_estimate"),
        "output_token_reserve": token_pressure.get("output_token_reserve"),
        "warning_threshold": token_pressure.get("warning_threshold"),
        "compact_threshold": token_pressure.get("compact_threshold"),
        "blocking_threshold": token_pressure.get("blocking_threshold"),
        "context_usage_ratio": context_usage_ratio,
        "usage_ratio": context_usage_ratio,
        "recommendation": _TOKEN_PRESSURE_RECOMMENDATIONS[state],
    }
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.WARNING,
        payload=payload,
    )


__all__ = [
    "emit_token_pressure_warning",
    "request_with_context_pressure_nudge",
    "token_pressure_state",
]
