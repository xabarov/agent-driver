"""Python-tool execution signals for run-trace summaries."""

from __future__ import annotations

from typing import Any

from agent_driver.observability.run_trace.tools import tool_payloads as _tool_payloads

from ._common import _PYTHON_TOOL


def _python_summary(
    events: list[dict[str, object]],
    *,
    tool_names: list[str],
    user_prompt: str | None,
    assistant_text: str,
    terminal_event: str | None,
    continuation_reason: str | None,
) -> dict[str, Any]:
    payloads = _tool_payloads(events, _PYTHON_TOOL)
    completed_payloads = [
        payload
        for payload in payloads
        if str(payload.get("status") or "").lower()
        in {"completed", "done", "success", "ok"}
    ]
    result_texts = [
        str(payload.get("result_summary") or payload.get("result") or "")
        for payload in payloads
    ]
    combined_results = "\n".join(result_texts).lower()
    python_tool_used = _PYTHON_TOOL in tool_names
    final_after_python = (
        python_tool_used
        and terminal_event == "run_completed"
        and continuation_reason != "continuation_signal"
        and len(assistant_text.strip()) >= 3
    )
    return {
        "python_tool_available": python_tool_used,
        "python_tool_used": python_tool_used,
        "python_calls": tool_names.count(_PYTHON_TOOL),
        "python_policy_errors": sum(
            1
            for text in result_texts
            if "python policy:" in text.lower() or "unauthorized import" in text.lower()
        ),
        "python_timeouts": sum(1 for text in result_texts if "timeout" in text.lower()),
        "python_expected": False,
        "missed_python_for_calculation": False,
        "python_result_observed": bool(
            completed_payloads
            or any(text.strip() for text in result_texts)
            or "final_answer" in combined_results
        ),
        "final_after_python": final_after_python,
        "final_mentions_python_error": any(
            marker in assistant_text.lower()
            for marker in ("python policy", "unauthorized import", "sandbox")
        ),
    }
