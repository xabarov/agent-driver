"""Helpers for constructing LLM requests in single-agent runtime."""

from __future__ import annotations

from typing import Any

from agent_driver.code_agent.prompt import render_code_agent_prompt
from agent_driver.code_agent.tool_surface import (
    build_callable_tool_surface,
    render_callable_tool_docs,
)
from agent_driver.context import estimate_token_pressure, trim_context
from agent_driver.contracts.context import ContextBudget
from agent_driver.contracts.enums import AgentProfile
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
    tool_docs: str | None = None,
    authorized_imports: tuple[str, ...] = (),
    registry: Any | None = None,
    observations: list[dict[str, Any]] | None = None,
    planning_prompt: str | None = None,
    digest_ids: list[str] | None = None,
    artifact_ids: list[str] | None = None,
    max_chars: int = 6000,
    max_messages: int | None = 24,
    max_observations: int | None = None,
    context_window_estimate: int = 12000,
    warning_threshold: int = 7500,
    compact_threshold: int = 9000,
    blocking_threshold: int = 10500,
    output_token_reserve: int = 1500,
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
    if planning_prompt and planning_prompt.strip():
        prompt = f"{prompt}\n\n{planning_prompt.strip()}"
    code_prompt_render = None
    if run_input.agent_profile == AgentProfile.CODE_AGENT:
        resolved_tool_docs = tool_docs
        if resolved_tool_docs is None and registry is not None:
            resolved_tool_docs = render_callable_tool_docs(
                build_callable_tool_surface(registry)
            )
        observation_lines: list[str] = []
        if observations:
            for row in observations:
                preview = row.get("text_preview")
                if not isinstance(preview, str) or not preview.strip():
                    continue
                provenance = row.get("provenance")
                source = (
                    str(provenance.get("source", "observation"))
                    if isinstance(provenance, dict)
                    else "observation"
                )
                observation_lines.append(f"[{source}] {preview}")
        if planning_prompt and planning_prompt.strip():
            observation_lines.append(f"[planning] {planning_prompt.strip()}")
        code_prompt_render = render_code_agent_prompt(
            task=prompt,
            tool_docs=resolved_tool_docs or "",
            authorized_imports=authorized_imports,
            observations=observation_lines,
            clarification=clarification,
        )
        prompt = code_prompt_render.rendered_text
    if prompt_messages:
        prompt_messages[-1]["content"] = prompt
    trimmed = trim_context(
        budget=ContextBudget(
            max_chars=max_chars,
            max_messages=max_messages,
            max_observations=max_observations,
        ),
        prompt_messages=prompt_messages,
        digest_ids=digest_ids or [],
        artifact_ids=artifact_ids or [],
        observation_rows=observations or [],
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
    trim_metadata = trimmed.model_dump(mode="json").get("metadata", {})
    retained_observations = (
        trim_metadata.get("retained_observations", [])
        if isinstance(trim_metadata, dict)
        else []
    )
    token_pressure = estimate_token_pressure(
        prompt_messages=trimmed.prompt_messages,
        observations=(
            retained_observations if isinstance(retained_observations, list) else []
        ),
        retained_digest_ids=trimmed.retained_digest_ids,
        retained_artifact_ids=trimmed.retained_artifact_ids,
        context_window_estimate=context_window_estimate,
        warning_threshold=warning_threshold,
        compact_threshold=compact_threshold,
        blocking_threshold=blocking_threshold,
        output_token_reserve=output_token_reserve,
    )
    return request, {
        "trim_audit": [item.model_dump(mode="json") for item in trimmed.audit],
        "trim_metadata": trim_metadata,
        "retained_digest_ids": trimmed.retained_digest_ids,
        "retained_artifact_ids": trimmed.retained_artifact_ids,
        "token_pressure": token_pressure,
        "prompt_render": (
            code_prompt_render.model_dump(mode="json")
            if code_prompt_render is not None
            else None
        ),
    }


__all__ = ["build_single_agent_llm_request"]
