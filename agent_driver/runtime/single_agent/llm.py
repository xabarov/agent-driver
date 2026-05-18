"""Helpers for constructing LLM requests in single-agent runtime."""

from __future__ import annotations

from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.llm.contracts import LlmRequest


def build_single_agent_llm_request(
    *,
    run_input: AgentRunInput,
    clarification: str | None,
) -> LlmRequest:
    """Build normalized non-streaming request for single-agent step loop."""
    prompt = run_input.input or (
        run_input.messages[-1].content if run_input.messages else ""
    )
    if clarification is not None and clarification.strip():
        prompt = f"{prompt}\n\nClarification: {clarification.strip()}"
    request_metadata = dict(run_input.tool_policy.metadata)
    forced_model = request_metadata.pop("forced_model", None)
    return LlmRequest(
        messages=[ChatMessage(role="user", content=prompt)],
        model_role=run_input.model_role,
        model=forced_model if isinstance(forced_model, str) else None,
        stream=False,
        metadata=request_metadata,
    )


__all__ = ["build_single_agent_llm_request"]
