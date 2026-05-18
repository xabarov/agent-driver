"""Helpers for constructing LLM requests in single-agent runtime."""

from __future__ import annotations

from typing import Any

from agent_driver.context import trim_context
from agent_driver.contracts.context import ContextBudget
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.llm.contracts import LlmRequest


def _normalize_trimmed_messages(
    prompt_messages: list[dict[str, object]],
) -> list[ChatMessage]:
    """Convert trimmed prompt payloads into validated chat messages."""
    normalized: list[ChatMessage] = []
    for message in prompt_messages:
        role = str(message.get("role", "user"))
        content = str(message.get("content", ""))
        normalized.append(ChatMessage(role=role, content=content))
    return normalized


def build_single_agent_llm_request(  # pylint: disable=too-many-arguments,too-many-locals
    *,
    run_input: AgentRunInput,
    clarification: str | None,
    observations: list[dict[str, Any]] | None = None,
    digest_ids: list[str] | None = None,
    artifact_ids: list[str] | None = None,
    max_chars: int = 6000,
    max_messages: int | None = 24,
) -> tuple[LlmRequest, dict[str, Any]]:
    """Build normalized non-streaming request for single-agent step loop."""
    prompt = run_input.input or (
        run_input.messages[-1].content if run_input.messages else ""
    )
    prompt_messages = (
        [msg.model_dump(mode="json") for msg in run_input.messages]
        if run_input.messages
        else [{"role": "user", "content": prompt}]
    )
    if clarification is not None and clarification.strip():
        prompt = f"{prompt}\n\nClarification: {clarification.strip()}"
    if observations:
        observation_lines: list[str] = []
        for row in observations:
            preview = row.get("text_preview")
            if isinstance(preview, str) and preview.strip():
                source = str(row.get("provenance", {}).get("source", "observation"))
                observation_lines.append(f"[{source}] {preview}")
        if observation_lines:
            prompt = f"{prompt}\n\nObservations:\n" + "\n".join(observation_lines)
    if prompt_messages:
        prompt_messages[-1]["content"] = prompt
    trimmed = trim_context(
        budget=ContextBudget(max_chars=max_chars, max_messages=max_messages),
        prompt_messages=prompt_messages,
        digest_ids=digest_ids or [],
        artifact_ids=artifact_ids or [],
    )
    request_metadata = dict(run_input.tool_policy.metadata)
    forced_model = request_metadata.pop("forced_model", None)
    request = LlmRequest(
        messages=_normalize_trimmed_messages(trimmed.prompt_messages),
        model_role=run_input.model_role,
        model=forced_model if isinstance(forced_model, str) else None,
        stream=False,
        metadata=request_metadata,
    )
    return request, {
        "trim_audit": [item.model_dump(mode="json") for item in trimmed.audit],
        "trim_metadata": trimmed.metadata,
        "retained_digest_ids": trimmed.retained_digest_ids,
        "retained_artifact_ids": trimmed.retained_artifact_ids,
    }


__all__ = ["build_single_agent_llm_request"]
