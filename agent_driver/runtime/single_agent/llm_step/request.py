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
from agent_driver.runtime.research_artifacts import deep_research_report_artifact_exists
from agent_driver.runtime.single_agent.llm_step.build import (
    LlmRequestBuildContext,
    build_single_agent_llm_request,
)
from agent_driver.runtime.single_agent.llm_step.prompt import (
    append_runtime_attachment_messages,
    effective_code_agent_imports,
    react_system_instruction,
)
from agent_driver.runtime.single_agent.llm_step.stream_recovery import (
    force_final_answer_message,
)
from agent_driver.runtime.single_agent.lifecycle.events import emit_step_event
from agent_driver.runtime.single_agent.llm_step.streaming import is_stream_enabled
from agent_driver.runtime.single_agent.context_management.todo_reminders import (
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
    tool_choice = _provider_safe_tool_choice(
        context,
        _deep_research_strategy_tool_choice(context, tool_choice),
    )
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


def _deep_research_strategy_tool_choice(
    context: RunContext, tool_choice: object | None
) -> object | None:
    """Force high-level Deep Research profile strategy when prompts drift."""
    if tool_choice is not None:
        return tool_choice
    task_contract = context.run_input.tool_policy.metadata.get("task_contract")
    if not isinstance(task_contract, dict):
        return None
    if task_contract.get("research_mode") != "deep" and task_contract.get(
        "research_depth"
    ) != "deep_parallel_research":
        return None
    profile = str(task_contract.get("research_profile") or "medium").strip().lower()
    if profile == "light":
        return None
    if _deep_research_child_synthesis_pending(context):
        if _deep_research_subagent_budget_remaining(context):
            return _deep_research_record_strategy_choice(
                context,
                tool_name="agent_tool",
                reason="child_synthesis_pending_with_remaining_subagent_budget",
            )
        return _deep_research_write_strategy_tool_choice(context, force=True)
    if _deep_research_tool_used(context, "agent_tool"):
        return _deep_research_write_strategy_tool_choice(context)
    if not _deep_research_initial_plan_seen(context):
        return None
    max_subagents = task_contract.get("max_subagent_requests")
    if isinstance(max_subagents, int) and max_subagents <= 0:
        return _deep_research_write_strategy_tool_choice(context)
    if not _deep_research_tool_available(context, "agent_tool"):
        return _deep_research_write_strategy_tool_choice(context)
    return _deep_research_record_strategy_choice(
        context,
        tool_name="agent_tool",
        reason="medium_hard_requires_bounded_subagents",
    )


def _deep_research_write_strategy_tool_choice(
    context: RunContext,
    *,
    force: bool = False,
) -> object | None:
    if _deep_research_parent_report_write_seen(context):
        return None
    if deep_research_report_artifact_exists(context) and _deep_research_tool_available(
        context, "file_patch"
    ):
        return _deep_research_record_strategy_choice(
            context,
            tool_name="file_patch",
            reason=(
                "child_synthesis_pending_budget_exhausted"
                if force
                else "deep_research_discovery_budget_reached"
            ),
            path="research/report.md",
        )
    if not _deep_research_tool_available(context, "file_write"):
        return None
    if not force and not _deep_research_discovery_budget_reached(context):
        return None
    return _deep_research_record_strategy_choice(
        context,
        tool_name="file_write",
        reason=(
            "child_synthesis_pending_budget_exhausted"
            if force
            else "deep_research_discovery_budget_reached"
        ),
        path="research/report.md",
    )


def _provider_safe_tool_choice(context: RunContext, tool_choice: object | None) -> object | None:
    """Avoid repeating provider-rejected named forced tool choices."""
    if context.metadata.get("forced_tool_choice_retry") != (
        "removed_after_provider_rejection"
    ):
        return tool_choice
    if not _forced_named_tool_choice(tool_choice):
        return tool_choice
    context.metadata["forced_tool_choice_disabled"] = (
        "provider_rejected_named_tool_choice"
    )
    return None


def _forced_named_tool_choice(tool_choice: object | None) -> str | None:
    if not isinstance(tool_choice, dict):
        return None
    if tool_choice.get("type") != "tool":
        return None
    name = tool_choice.get("name")
    return name if isinstance(name, str) and name.strip() else None


def _deep_research_initial_plan_seen(context: RunContext) -> bool:
    planning_state = context.metadata.get("planning_state")
    if isinstance(planning_state, dict):
        todos = planning_state.get("todos")
        if isinstance(todos, list) and todos:
            return True
    return _deep_research_tool_used(context, "todo_write")


def _deep_research_discovery_budget_reached(context: RunContext) -> bool:
    counts = _deep_research_tool_counts(context)
    if counts.get("web_fetch", 0) >= 2:
        return True
    if counts.get("web_search", 0) >= 6:
        return True
    artifacts = context.metadata.get("deep_research_artifacts")
    if isinstance(artifacts, dict) and artifacts.get("source_ledger_exists") is True:
        return True
    return False


def _deep_research_child_synthesis_pending(context: RunContext) -> bool:
    handoff = context.metadata.get("deep_research_child_synthesis")
    return (
        isinstance(handoff, dict)
        and handoff.get("pending") is True
        and not _deep_research_parent_report_write_seen(context)
    )


def _deep_research_subagent_budget_remaining(context: RunContext) -> bool:
    return _deep_research_planned_or_started_subagent_count(
        context
    ) < _deep_research_max_subagent_requests(context)


def _deep_research_planned_or_started_subagent_count(context: RunContext) -> int:
    count = 0
    planned = context.metadata.get("planned_subagent_group")
    if isinstance(planned, dict) and isinstance(planned.get("tasks"), list):
        count += len([item for item in planned["tasks"] if isinstance(item, dict)])
    runs = context.metadata.get("subagent_runs")
    if isinstance(runs, list):
        count += len([item for item in runs if isinstance(item, dict)])
    return count


def _deep_research_max_subagent_requests(context: RunContext) -> int:
    task_contract = context.run_input.tool_policy.metadata.get("task_contract")
    if isinstance(task_contract, dict):
        raw = task_contract.get("max_subagent_requests")
        if isinstance(raw, int) and not isinstance(raw, bool):
            return max(0, raw)
        profile = str(task_contract.get("research_profile") or "").strip().lower()
    if profile == "light":
        return 0
    if profile == "hard":
        return 4
    return 1


def _deep_research_tool_used(context: RunContext, tool_name: str) -> bool:
    return _deep_research_tool_counts(context).get(tool_name, 0) > 0


def _deep_research_tool_counts(context: RunContext) -> dict[str, int]:
    counts: dict[str, int] = {}
    results = context.metadata.get("tool_results")
    if not isinstance(results, list):
        return counts
    for item in results:
        if not isinstance(item, dict):
            continue
        call = item.get("call")
        if not isinstance(call, dict):
            continue
        name = call.get("tool_name")
        if not isinstance(name, str) or not name:
            continue
        counts[name] = counts.get(name, 0) + 1
    return counts


def _deep_research_parent_report_write_seen(context: RunContext) -> bool:
    for item in get_tool_loop_state(context).tool_results():
        if not isinstance(item, dict):
            continue
        call = item.get("call")
        if not isinstance(call, dict):
            continue
        if call.get("tool_name") not in {"file_write", "file_patch", "file_edit"}:
            continue
        args = call.get("args")
        if not isinstance(args, dict):
            continue
        path = str(args.get("path") or args.get("file_path") or "").strip()
        if path == "research/report.md" or path.endswith("/research/report.md"):
            return True
    return False


def _deep_research_tool_available(context: RunContext, tool_name: str) -> bool:
    policy = context.run_input.tool_policy
    if tool_name in set(policy.denied_tools or []):
        return False
    allowed = policy.allowed_tools
    return allowed is None or tool_name in set(allowed)


def _deep_research_record_strategy_choice(
    context: RunContext,
    *,
    tool_name: str,
    reason: str,
    path: str | None = None,
) -> dict[str, str]:
    choice = {"type": "tool", "name": tool_name}
    payload = {"tool": tool_name, "reason": reason}
    if path is not None:
        payload["path"] = path
    context.metadata["deep_research_strategy_tool_choice"] = payload
    return choice


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
