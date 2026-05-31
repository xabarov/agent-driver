"""Request preparation helpers for the single-agent LLM-call step."""

from __future__ import annotations

from typing import Any, Protocol

from agent_driver.context import (
    microcompact_observations,
    render_planning_step_prompt,
)
from agent_driver.contracts.context import PlanningStep
from agent_driver.contracts.enums import ChatRole, RuntimeEventType
from agent_driver.contracts.messages import ChatMessage
from agent_driver.runtime.metadata_state import (
    get_compaction_runtime_state,
    get_planning_runtime_state,
    get_tool_loop_state,
)
from agent_driver.runtime.single_agent.llm import (
    LlmRequestBuildContext,
    build_single_agent_llm_request,
)
from agent_driver.runtime.single_agent.llm_step_prompt import (
    append_runtime_attachment_messages,
    effective_code_agent_imports,
    react_system_instruction,
)
from agent_driver.runtime.single_agent.llm_step_stream_recovery import (
    force_final_answer_message,
)
from agent_driver.runtime.single_agent.step_events import emit_step_event
from agent_driver.runtime.single_agent.streaming import is_stream_enabled
from agent_driver.runtime.single_agent.todo_reminders import (
    maybe_append_todo_reminder_to_protocol,
)
from agent_driver.runtime.single_agent.types import (
    EventSpec,
    RunContext,
    RunnerConfig,
    RunnerDeps,
)


class LlmRequestPrepHost(Protocol):
    """Host surface required while preparing an LLM request."""

    _deps: RunnerDeps
    _config: RunnerConfig

    def _emit(self, event: EventSpec) -> None: ...


def microcompact_context_observations(
    host: LlmRequestPrepHost, context: RunContext
) -> list[dict[str, object]]:
    """Apply cheap observation microcompaction before request trimming."""
    compaction_state = get_compaction_runtime_state(context)
    observations = compaction_state.observations()
    micro = microcompact_observations(
        [item for item in observations if isinstance(item, dict)],
        preserve_recent=host._config.microcompact_preserve_recent,
        max_preview_chars=host._config.microcompact_max_preview_chars,
    )
    compaction_state.set_microcompaction(
        observations=micro.observations,
        audit=micro.audit,
        bytes_saved=micro.bytes_saved,
        estimated_tokens_saved=micro.estimated_tokens_saved,
    )
    return micro.observations


def build_trimmed_request(
    host: LlmRequestPrepHost,
    context: RunContext,
    observations: list[dict[str, object]],
    clarification: object,
) -> tuple[Any, dict[str, object]]:
    """Build the provider request and return the trim audit payload."""
    compaction_state = get_compaction_runtime_state(context)
    digest_refs = compaction_state.digest_refs()
    artifact_refs = compaction_state.artifact_refs()
    planning_prompt = None
    planning_step_payload = get_planning_runtime_state(context).planning_step()
    if host._config.include_planning_prompt and isinstance(planning_step_payload, dict):
        planning_prompt = render_planning_step_prompt(
            PlanningStep.model_validate(planning_step_payload)
        )
    protocol_messages = protocol_messages_from_metadata(context)
    protocol_messages = append_runtime_attachment_messages(
        context,
        protocol_messages,
    )
    protocol_messages = maybe_append_todo_reminder_to_protocol(
        context, protocol_messages
    )
    # Inner-loop overrides (e.g. ``"none"`` to force a final answer after a
    # repeated handler error) take precedence; otherwise fall through to
    # the caller-supplied ``RunInput.tool_choice`` so the public seam can
    # force a specific tool. None on both sides preserves the legacy
    # ``"auto"`` default applied by the provider adapters.
    tool_loop_state = get_tool_loop_state(context)
    tool_choice = tool_loop_state.tool_choice_override()
    if tool_choice is None:
        if (
            context.run_input.app_metadata.get("chat_mode") is True
            and context.llm_step_count > 0
        ):
            tool_choice = None
        else:
            tool_choice = context.run_input.tool_choice
    system_instruction = react_system_instruction(host, context)
    if (
        tool_loop_state.force_final_answer_enabled()
        and protocol_messages is not None
        and protocol_messages
    ):
        protocol_messages = protocol_messages + (
            ChatMessage(
                role=ChatRole.USER,
                content=force_final_answer_message(context),
            ),
        )
    return build_single_agent_llm_request(
        LlmRequestBuildContext(
            run_input=context.run_input,
            clarification=clarification if isinstance(clarification, str) else None,
            tool_docs=(
                context.metadata["code_tool_docs"]
                if isinstance(context.metadata.get("code_tool_docs"), str)
                else None
            ),
            authorized_imports=effective_code_agent_imports(host),
            registry=host._deps.tool_registry,
            observations=(
                tuple()
                if protocol_messages is not None
                else tuple(item for item in observations if isinstance(item, dict))
            ),
            planning_prompt=planning_prompt,
            digest_ids=tuple(
                str(item.get("digest_id"))
                for item in digest_refs
                if isinstance(item, dict) and item.get("digest_id")
            ),
            artifact_ids=tuple(
                str(item.get("artifact_id"))
                for item in artifact_refs
                if isinstance(item, dict) and item.get("artifact_id")
            ),
            max_chars=host._config.trim_max_chars,
            max_messages=host._config.trim_max_messages,
            max_observations=host._config.trim_max_observations,
            context_window_estimate=host._config.context_window_estimate,
            warning_threshold=host._config.token_warning_threshold,
            compact_threshold=host._config.token_compact_threshold,
            blocking_threshold=host._config.token_blocking_threshold,
            output_token_reserve=host._config.output_token_reserve,
            stream=is_stream_enabled(context.run_input),
            system_instruction=system_instruction,
            protocol_messages=protocol_messages,
            tool_choice=(
                str(tool_choice)
                if isinstance(tool_choice, str)
                else (tool_choice if isinstance(tool_choice, dict) else None)
            ),
        )
    )


def protocol_messages_from_metadata(
    context: RunContext,
) -> tuple[ChatMessage, ...] | None:
    """Deserialize protocol messages captured in runtime metadata."""
    payload = context.metadata.get("protocol_messages")
    if not isinstance(payload, list):
        return None
    rows: list[ChatMessage] = []
    for item in payload:
        if isinstance(item, dict):
            rows.append(ChatMessage.model_validate(item))
    return tuple(rows) if rows else None


def emit_protocol_debug(
    host: LlmRequestPrepHost, context: RunContext, request: Any
) -> None:
    """Emit protocol debug summary for chat/demo troubleshooting."""
    if context.run_input.app_metadata.get("debug_tool_protocol") is not True:
        return
    messages = request.messages if isinstance(request.messages, list) else []
    roles = [message.role.value for message in messages]
    tool_names: list[str] = []
    for tool in request.tools:
        function_payload = tool.get("function")
        if isinstance(function_payload, dict):
            name = function_payload.get("name")
            if isinstance(name, str) and name.strip():
                tool_names.append(name)
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.WARNING,
        payload={
            "kind": "tool_protocol_debug",
            "message_count": len(messages),
            "roles": roles,
            "tool_names": tool_names,
            "tool_choice": request.tool_choice,
        },
    )


__all__ = [
    "build_trimmed_request",
    "emit_protocol_debug",
    "microcompact_context_observations",
    "protocol_messages_from_metadata",
]
