"""Stable SDK trace summary and support-bundle helpers."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.runtime import AgentRunOutput
from agent_driver.contracts.validation import ensure_json_serializable
from agent_driver.observability.run_trace import summarize_run_trace


class TraceSummary(ContractModel):
    """Stable SDK-facing summary of one run trace."""

    run_id: str
    verdict: str
    terminal_event: str | None = None
    llm_calls: int = 0
    tool_calls: int = 0
    tool_names: list[str] = Field(default_factory=list)
    failures: dict[str, bool] = Field(default_factory=dict)
    context_pressure: dict[str, Any] = Field(default_factory=dict)
    provider_rejected: bool = False
    notes: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("failures", "context_pressure", "raw")
    @classmethod
    def validate_json_dict(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Keep summary payloads JSON-compatible."""
        return ensure_json_serializable(value, field_name="trace summary")


def summarize_output(output: AgentRunOutput) -> TraceSummary:
    """Build a stable ``TraceSummary`` from an SDK run output."""
    raw = summarize_run_trace(
        run_id=output.run_id,
        events=[_summary_event(event) for event in output.events],
        assistant_text=output.answer,
    )
    return TraceSummary(
        run_id=str(raw.get("run_id") or output.run_id),
        verdict=str(raw.get("verdict") or "unknown"),
        terminal_event=_optional_str(raw.get("terminal_event")),
        llm_calls=int(raw.get("llm_calls") or 0),
        tool_calls=int(raw.get("tool_calls") or 0),
        tool_names=[
            item for item in raw.get("tool_names", []) if isinstance(item, str)
        ],
        failures={
            str(key): bool(value)
            for key, value in (raw.get("failures") or {}).items()
        },
        context_pressure=_dict_or_empty(raw.get("context_pressure")),
        provider_rejected=bool(raw.get("provider_rejected")),
        notes=[item for item in raw.get("notes", []) if isinstance(item, str)],
        raw=raw,
    )


def support_bundle(output: AgentRunOutput) -> dict[str, Any]:
    """Return a redacted support bundle recipe for SDK users."""
    summary = summarize_output(output)
    return {
        "run_id": output.run_id,
        "attempt_id": output.attempt_id,
        "status": output.status.value,
        "terminal_reason": (
            output.terminal_reason.value if output.terminal_reason else None
        ),
        "trace_summary": summary.model_dump(mode="json"),
        "context": output.context.model_dump(mode="json"),
        "usage": output.usage.model_dump(mode="json") if output.usage else None,
        "tool_trace": [
            item.model_dump(mode="json", exclude={"args"}) for item in output.tool_trace
        ],
        "warnings": [item.model_dump(mode="json") for item in output.warnings],
        "metadata_keys": sorted(output.metadata),
    }


def _summary_event(event: Any) -> dict[str, object]:
    if hasattr(event, "model_dump"):
        payload = event.model_dump(mode="json")
    elif isinstance(event, dict):
        payload = dict(event)
    else:
        return {}
    return {
        "event": str(payload.get("type") or payload.get("event") or ""),
        "data": _dict_or_empty(payload.get("payload") or payload.get("data")),
        "seq": payload.get("seq"),
    }


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


__all__ = ["TraceSummary", "summarize_output", "support_bundle"]
