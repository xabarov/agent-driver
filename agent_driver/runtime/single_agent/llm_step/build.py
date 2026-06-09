"""Helpers for constructing LLM requests in single-agent runtime."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from agent_driver.code_agent.prompt import render_code_agent_prompt
from agent_driver.code_agent.tool_surface import (
    build_callable_tool_surface,
    render_callable_tool_docs,
)
from agent_driver.context import trim_context
from agent_driver.context.token_pressure import (
    TokenPressureInput,
    estimate_token_pressure,
)
from agent_driver.contracts.context import ContextBudget
from agent_driver.contracts.enums import AgentProfile
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.contracts.tools import ToolManifest
from agent_driver.llm.contracts import LlmRequest
from agent_driver.runtime.single_agent.context_management.protocol_validate import (
    validate_and_repair_protocol_messages,
)


@dataclass(frozen=True, slots=True)
class LlmRequestBuildContext:
    """Inputs for building a single-agent LLM request."""

    run_input: AgentRunInput
    clarification: str | None = None
    tool_docs: str | None = None
    authorized_imports: tuple[str, ...] = ()
    registry: Any | None = None
    observations: tuple[dict[str, Any], ...] = ()
    planning_prompt: str | None = None
    digest_ids: tuple[str, ...] = ()
    artifact_ids: tuple[str, ...] = ()
    max_chars: int = 6000
    max_messages: int | None = 24
    max_observations: int | None = None
    context_window_estimate: int = 12000
    warning_threshold: int = 7500
    compact_threshold: int = 9000
    blocking_threshold: int = 10500
    output_token_reserve: int = 1500
    stream: bool = False
    system_instruction: str | None = None
    protocol_messages: tuple[ChatMessage, ...] | None = None
    tool_choice: str | dict[str, Any] | None = None
    response_format: dict[str, Any] | None = None
    request_allowed_tools: tuple[str, ...] | None = None
    enable_prompt_cache: bool = False


def _normalize_trimmed_messages(
    prompt_messages: list[dict[str, object]],
) -> list[ChatMessage]:
    """Convert trimmed prompt payloads into validated chat messages."""
    normalized: list[ChatMessage] = []
    for message in prompt_messages:
        role = str(message.get("role", "user"))
        content = str(message.get("content", ""))
        name = message.get("name")
        tool_call_id = message.get("tool_call_id")
        metadata = message.get("metadata")
        normalized.append(
            ChatMessage(
                role=role,
                content=content,
                name=str(name) if isinstance(name, str) and name.strip() else None,
                tool_call_id=(
                    str(tool_call_id)
                    if isinstance(tool_call_id, str) and tool_call_id.strip()
                    else None
                ),
                metadata=metadata if isinstance(metadata, dict) else {},
            )
        )
    return normalized


def _tool_schema_from_manifest(manifest: ToolManifest) -> dict[str, Any]:
    parameters = manifest.args_schema
    if not isinstance(parameters, dict):
        parameters = {
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        }
    else:
        parameters = _provider_compatible_json_schema(parameters)
    return {
        "type": "function",
        "function": {
            "name": manifest.name,
            "description": manifest.description,
            "parameters": parameters,
        },
    }


def _provider_compatible_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a provider-friendly copy of a tool JSON Schema.

    Some OpenAI/OpenRouter routes reject array schemas without an explicit
    ``items`` key. JSON Schema allows that omission, but provider function
    calling is stricter, so normalize before sending tool definitions.
    """
    normalized = deepcopy(schema)
    _ensure_array_items(normalized)
    return normalized


def _ensure_array_items(value: Any) -> None:
    if isinstance(value, dict):
        if value.get("type") == "array" and "items" not in value:
            value["items"] = {}
        for child in value.values():
            _ensure_array_items(child)
    elif isinstance(value, list):
        for child in value:
            _ensure_array_items(child)


def _request_tools_from_registry(
    registry: Any | None,
    *,
    allowed: tuple[str, ...] | None = None,
    denied: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Build the LLM-visible tool list, applying allow/deny filters.

    Filtering happens at request-build time (not just at policy-eval time)
    so the model NEVER SEES denied tools in its schema. The OpenClaude
    pattern: restricting the registry at the LLM boundary prevents the
    model from even trying to invoke a forbidden tool — which would
    otherwise consume an LLM round-trip + emit a denied trace.

    Filter semantics:
      * ``allowed=None``  → no allowlist gate; everything (minus denied) passes
      * ``allowed=()``    → empty allowlist → ZERO tools surface (use this
        when caller wants ``NO_TOOLS`` via the schema layer)
      * ``denied=None`` / ``()`` → no denylist gate
      * ``denied`` always overrides ``allowed`` (deny wins on collision)

    The ``tool_policy.evaluate_tool_policy`` runtime check still runs on
    every planned call; this filter is the FIRST line of defence, the
    evaluator is the SECOND. Both are needed because (a) some tools may
    bypass the schema (e.g. native function-calling smuggling) and (b) the
    runtime check sees data the schema layer doesn't (current call count,
    risk threshold).
    """
    names = effective_tool_names_from_registry(
        registry,
        allowed=allowed,
        denied=denied,
    )
    if not names:
        return []
    allowed_names = set(names)
    rows = getattr(registry, "list_registered", None)
    if not callable(rows):
        return []
    schemas: list[dict[str, Any]] = []
    for item in rows():
        if item.manifest.name in allowed_names:
            schemas.append(_tool_schema_from_manifest(item.manifest))
    return schemas


def effective_tool_names_from_registry(
    registry: Any | None,
    *,
    allowed: tuple[str, ...] | None = None,
    denied: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    """Return model-visible tool names after request-level allow/deny filters."""
    if registry is None:
        return tuple()
    rows = getattr(registry, "list_registered", None)
    if not callable(rows):
        return tuple()
    allowed_set = set(allowed) if allowed is not None else None
    denied_set = set(denied) if denied else set()
    names: list[str] = []
    for item in rows():
        name = item.manifest.name
        if name in denied_set:
            continue
        if allowed_set is not None and name not in allowed_set:
            continue
        names.append(name)
    return tuple(names)


def build_single_agent_llm_request(
    ctx: LlmRequestBuildContext,
) -> tuple[LlmRequest, dict[str, Any]]:
    """Build normalized non-streaming request for single-agent step loop."""
    run_input = ctx.run_input
    prompt = run_input.input or (
        run_input.messages[-1].content if run_input.messages else ""
    )
    if ctx.protocol_messages is not None:
        repaired = validate_and_repair_protocol_messages(
            ctx.protocol_messages,
            max_total_content_chars=max(ctx.max_chars * 3, ctx.max_chars),
        )
        prompt_messages = [
            message.model_dump(mode="json") for message in repaired.messages
        ]
    else:
        prompt_messages = (
            [msg.model_dump(mode="json") for msg in run_input.messages]
            if run_input.messages
            else [{"role": "user", "content": prompt}]
        )
    if ctx.protocol_messages is None:
        if ctx.clarification is not None and ctx.clarification.strip():
            prompt = f"{prompt}\n\nClarification: {ctx.clarification.strip()}"
        if ctx.planning_prompt and ctx.planning_prompt.strip():
            prompt = f"{prompt}\n\n{ctx.planning_prompt.strip()}"
    code_prompt_render = None
    if run_input.agent_profile == AgentProfile.CODE_AGENT:
        resolved_tool_docs = ctx.tool_docs
        if resolved_tool_docs is None and ctx.registry is not None:
            resolved_tool_docs = render_callable_tool_docs(
                build_callable_tool_surface(ctx.registry)
            )
        observation_lines: list[str] = []
        for row in ctx.observations:
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
        if ctx.planning_prompt and ctx.planning_prompt.strip():
            observation_lines.append(f"[planning] {ctx.planning_prompt.strip()}")
        code_prompt_render = render_code_agent_prompt(
            task=prompt,
            tool_docs=resolved_tool_docs or "",
            authorized_imports=ctx.authorized_imports,
            observations=observation_lines,
            clarification=ctx.clarification,
        )
        prompt = code_prompt_render.rendered_text
    if ctx.system_instruction and ctx.system_instruction.strip():
        has_system = any(
            str(item.get("role", "")) == "system" for item in prompt_messages
        )
        if not has_system:
            prompt_messages = [
                {"role": "system", "content": ctx.system_instruction.strip()}
            ] + prompt_messages
    if prompt_messages and ctx.protocol_messages is None:
        prompt_messages[-1]["content"] = prompt
    trimmed = trim_context(
        budget=ContextBudget(
            max_chars=ctx.max_chars,
            max_messages=ctx.max_messages,
            max_observations=ctx.max_observations,
        ),
        prompt_messages=prompt_messages,
        digest_ids=list(ctx.digest_ids),
        artifact_ids=list(ctx.artifact_ids),
        observation_rows=list(ctx.observations),
    )
    final_prompt_messages = list(trimmed.prompt_messages)
    post_trim_protocol_repairs: tuple[str, ...] = ()
    post_trim_protocol_warnings: tuple[str, ...] = ()
    if ctx.protocol_messages is not None:
        repaired_trimmed = validate_and_repair_protocol_messages(
            _normalize_trimmed_messages(final_prompt_messages),
            max_total_content_chars=max(ctx.max_chars * 3, ctx.max_chars),
        )
        post_trim_protocol_repairs = repaired_trimmed.repairs
        post_trim_protocol_warnings = repaired_trimmed.warnings
        final_prompt_messages = [
            message.model_dump(mode="json") for message in repaired_trimmed.messages
        ]
    request_metadata = dict(run_input.tool_policy.metadata)
    forced_model = request_metadata.pop("forced_model", None)
    # Mirror the runtime policy's allow/deny into the schema layer so the
    # model only sees tools it's permitted to call. ``None`` allowlist
    # keeps the legacy "show everything" default; explicit tuples flow
    # into the filter.
    policy_allowed = run_input.tool_policy.allowed_tools
    request_allowed = _intersect_allowed_tools(
        tuple(policy_allowed) if policy_allowed is not None else None,
        ctx.request_allowed_tools,
    )
    policy_denied = run_input.tool_policy.denied_tools
    # Caller-supplied response_format (Phase 1 / 0.1) flows through to
    # LlmRequest. Inner-loop never overrides it — schema enforcement
    # is a request-level decision, not a per-step retry.
    response_format = ctx.response_format
    if response_format is None:
        response_format = run_input.response_format
    request = LlmRequest(
        messages=_normalize_trimmed_messages(final_prompt_messages),
        model_role=run_input.model_role,
        model=forced_model if isinstance(forced_model, str) else None,
        stream=ctx.stream,
        tools=_request_tools_from_registry(
            ctx.registry,
            allowed=request_allowed,
            denied=tuple(policy_denied) if policy_denied else None,
        ),
        tool_choice=ctx.tool_choice,
        response_format=response_format,
        enable_prompt_cache=ctx.enable_prompt_cache,
        metadata=request_metadata,
    )
    trim_metadata = trimmed.model_dump(mode="json").get("metadata", {})
    if isinstance(trim_metadata, dict):
        if post_trim_protocol_repairs:
            trim_metadata["post_trim_protocol_repairs"] = list(
                post_trim_protocol_repairs
            )
        if post_trim_protocol_warnings:
            trim_metadata["post_trim_protocol_warnings"] = list(
                post_trim_protocol_warnings
            )
    retained_observations = (
        trim_metadata.get("retained_observations", [])
        if isinstance(trim_metadata, dict)
        else []
    )
    token_pressure = estimate_token_pressure(
        TokenPressureInput(
            prompt_messages=tuple(final_prompt_messages),
            observations=tuple(
                retained_observations if isinstance(retained_observations, list) else []
            ),
            retained_digest_ids=tuple(trimmed.retained_digest_ids),
            retained_artifact_ids=tuple(trimmed.retained_artifact_ids),
            context_window_estimate=ctx.context_window_estimate,
            warning_threshold=ctx.warning_threshold,
            compact_threshold=ctx.compact_threshold,
            blocking_threshold=ctx.blocking_threshold,
            output_token_reserve=ctx.output_token_reserve,
        )
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


def _intersect_allowed_tools(
    policy_allowed: tuple[str, ...] | None,
    request_allowed: tuple[str, ...] | None,
) -> tuple[str, ...] | None:
    """Return the effective request-level allowlist."""
    if request_allowed is None:
        return policy_allowed
    if policy_allowed is None:
        return request_allowed
    request_set = set(request_allowed)
    return tuple(name for name in policy_allowed if name in request_set)


__all__ = [
    "LlmRequestBuildContext",
    "_intersect_allowed_tools",
    "_provider_compatible_json_schema",
    "_request_tools_from_registry",
    "build_single_agent_llm_request",
]
