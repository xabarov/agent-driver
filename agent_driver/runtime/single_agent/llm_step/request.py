"""Request preparation helpers for the single-agent LLM-call step."""

from __future__ import annotations

from typing import Any, Protocol

from agent_driver.context import (
    microcompact_observations,
    render_planning_step_prompt,
    resolve_run_context_budget,
)
from agent_driver.contracts.context import ContextBudgetDefaults, PlanningStep
from agent_driver.contracts.enums import ChatRole, RuntimeEventType
from agent_driver.contracts.messages import ChatMessage
from agent_driver.context.token_estimation import (
    DEFAULT_CHARS_PER_TOKEN,
    clamp_chars_per_token,
)
from agent_driver.llm.context_windows import provider_model_hint
from agent_driver.runtime.metadata_state import (
    get_compaction_runtime_state,
    get_planning_runtime_state,
    get_tool_loop_state,
)
from agent_driver.runtime.single_agent.llm_step.build import (
    LlmRequestBuildContext,
    build_single_agent_llm_request,
)
from agent_driver.runtime.single_agent.llm_step.defer_primer import (
    surfaced_deferred_tool_names,
)
from agent_driver.runtime.single_agent.llm_step.prompt import (
    append_runtime_attachment_messages,
    effective_code_agent_imports,
    effective_request_tool_names,
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
from agent_driver.runtime.single_agent.llm_step.deep_research_gating_choice import (  # noqa: F401
    _deep_research_request_allowed_tools,
    _deep_research_strategy_tool_choice,
    _deep_research_context_active,
    _record_deep_research_active_profile,
    _deep_research_active_profile,
    _deep_research_medium_or_hard_active,
    _deep_research_initial_todo_only,
    _deep_research_write_strategy_tool_choice,
    _deep_research_initial_plan_seen,
    _deep_research_discovery_budget_reached,
    _deep_research_child_synthesis_pending,
    _deep_research_subagent_budget_remaining,
    _deep_research_tool_used,
    _deep_research_verified_fetch_count,
    _deep_research_parent_verify_fetch_budget_remaining,
    _deep_research_candidate_urls,
    _collect_urls_from_source_ledger,
    _collect_urls_from_text,
    _canonical_url,
    _deep_research_parent_search_fallback_required,
    _deep_research_fetch_attempt_count,
    _deep_research_tool_counts,
    _deep_research_parent_report_write_seen,
    _report_artifact_confirmed_if_possible,
    _deep_research_record_strategy_choice,
)


class LlmRequestPrepHost(Protocol):
    """Host surface required while preparing an LLM request."""

    _deps: RunnerDeps
    _config: RunnerConfig

    def _emit(self, event: EventSpec) -> None: ...


def _calibrated_chars_per_token(context: RunContext) -> float:
    """The run's calibrated chars-per-token (BUG-6), or the default before any
    provider usage has been observed. Clamped to the sane range."""
    raw = context.metadata.get("context_chars_per_token")
    if isinstance(raw, (int, float)) and raw > 0:
        return clamp_chars_per_token(float(raw))
    return DEFAULT_CHARS_PER_TOKEN


def _run_context_budget_defaults(
    host: LlmRequestPrepHost,
    *,
    trimming: object | None = None,
) -> ContextBudgetDefaults:
    """Project runner/model settings into the supported resolver contract."""
    settings = trimming or host._config
    return ContextBudgetDefaults(
        max_chars=int(getattr(settings, "trim_max_chars")),
        max_messages=getattr(settings, "trim_max_messages", None),
        max_observations=getattr(settings, "trim_max_observations", None),
        protect_recent_messages=getattr(settings, "trim_protect_recent_turns", None),
        preserve_recent_observations=getattr(
            settings, "microcompact_preserve_recent", None
        ),
        max_observation_preview_chars=getattr(
            settings, "microcompact_max_preview_chars", None
        ),
        context_window_estimate=int(getattr(settings, "context_window_estimate")),
        warning_threshold=int(getattr(settings, "token_warning_threshold")),
        compact_threshold=int(getattr(settings, "token_compact_threshold")),
        blocking_threshold=int(getattr(settings, "token_blocking_threshold")),
        output_token_reserve=int(getattr(settings, "output_token_reserve")),
        # BUG-5: the DEFAULT compaction char budget derives from the resolved model
        # window (usable input tokens × ~4 chars), not the static ptl_retry_max_chars
        # (4000) — otherwise "full" compaction summarises a sliver of history on a
        # large-context model. Never below the configured base. (max_chars above
        # stays the trimming budget; only compaction reads max_compaction_chars.)
        max_compaction_chars=max(
            int(getattr(host._config, "ptl_retry_max_chars", 4000)),
            max(
                1,
                int(getattr(settings, "context_window_estimate"))
                - int(getattr(settings, "output_token_reserve", 0)),
            )
            * 4,
        ),
        source=(
            "model_catalog"
            if getattr(settings, "context_window_source", "") == "model_catalog"
            else "runner_config"
        ),
    )


def microcompact_context_observations(
    host: LlmRequestPrepHost, context: RunContext
) -> list[dict[str, object]]:
    """Apply cheap observation microcompaction before request trimming."""
    compaction_state = get_compaction_runtime_state(context)
    observations = compaction_state.observations()
    context_budget = resolve_run_context_budget(
        context.run_input,
        _run_context_budget_defaults(host),
        chars_per_token=_calibrated_chars_per_token(context),
    )
    micro = microcompact_observations(
        [item for item in observations if isinstance(item, dict)],
        preserve_recent=(
            context_budget.preserve_recent_observations
            if context_budget.preserve_recent_observations is not None
            else len(observations)
        ),
        max_preview_chars=(
            context_budget.max_observation_preview_chars
            if context_budget.max_observation_preview_chars is not None
            else 2**31 - 1
        ),
    )
    compaction_state.set_microcompaction(
        observations=micro.observations,
        audit=micro.audit,
        bytes_saved=micro.bytes_saved,
        estimated_tokens_saved=micro.estimated_tokens_saved,
    )
    return micro.observations


_PRIMER_CONVERSATION_CHAR_CAP = 8000


def _primer_conversation_text(
    context: RunContext,
    protocol_messages: tuple[ChatMessage, ...] | None,
) -> str:
    """Concatenate recent conversation content for defer-primer relevance.

    Used only for keyword matching — never sent to the model — so a cheap
    char-capped join of the most recent messages is sufficient. Falls back to
    ``run_input.messages`` / ``run_input.input`` when no protocol transcript
    has been assembled yet (e.g. the very first step).
    """
    parts: list[str] = []
    if protocol_messages:
        parts = [msg.content for msg in protocol_messages if msg.content]
    elif context.run_input.messages:
        parts = [msg.content for msg in context.run_input.messages if msg.content]
    elif context.run_input.input:
        parts = [context.run_input.input]
    if not parts:
        return ""
    text = "\n".join(parts)
    # Keep the tail: the most recent turns carry the live intent.
    if len(text) > _PRIMER_CONVERSATION_CHAR_CAP:
        text = text[-_PRIMER_CONVERSATION_CHAR_CAP:]
    return text


def _surface_deferred_tools(
    host: LlmRequestPrepHost,
    context: RunContext,
    protocol_messages: tuple[ChatMessage, ...] | None,
) -> tuple[str, ...]:
    """Select deferred tools to surface this step via the configured primer.

    Returns an empty tuple when no primer is configured (the default), the
    registry exposes no manifests, or nothing is currently deferred — so the
    pure ``tool_search`` path is unchanged.
    """
    primer = getattr(host._config, "defer_primer", None)
    if primer is None:
        return ()
    registry = host._deps.tool_registry
    rows = getattr(registry, "list_registered", None)
    if not callable(rows):
        return ()
    deferred = tuple(item.manifest for item in rows() if item.manifest.is_deferred())
    if not deferred:
        return ()
    return surfaced_deferred_tool_names(
        deferred,
        _primer_conversation_text(context, protocol_messages),
        primer,
    )


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
    plan_refinement = get_planning_runtime_state(context).plan_refinement()
    refinement_revision_count = int(
        context.metadata.get("plan_refinement_revision_count", 0)
    )
    drafting_plan_refinement = (
        plan_refinement is not None and refinement_revision_count == 0
    )
    if host._config.include_planning_prompt and isinstance(planning_step_payload, dict):
        planning_prompt = render_planning_step_prompt(
            PlanningStep.model_validate(planning_step_payload)
        )
    protocol_messages = protocol_messages_from_metadata(context)
    protocol_messages = append_runtime_attachment_messages(
        context,
        protocol_messages,
        effective_tool_names=effective_request_tool_names(host, context),
    )
    protocol_messages = maybe_append_todo_reminder_to_protocol(
        context, protocol_messages
    )
    if (
        protocol_messages is not None
        and isinstance(clarification, str)
        and clarification.strip()
    ):
        clarification_prompt = f"Operator clarification:\n{clarification.strip()}"
        if plan_refinement is not None:
            if drafting_plan_refinement:
                clarification_prompt = (
                    "Draft the full revised approval plan itself now. Apply the "
                    "operator's exact feedback; do not describe what you intend to "
                    "change and do not call a tool in this drafting response.\n\n"
                    f"Operator feedback:\n{clarification.strip()}"
                )
            else:
                clarification_prompt = (
                    "Submit the full revised plan you just drafted by calling "
                    "exit_plan_mode_v2. Use the plan itself as content, not "
                    "commentary about the requested changes.\n\n"
                    f"Operator feedback:\n{clarification.strip()}"
                )
        protocol_messages = protocol_messages + (
            ChatMessage(
                role=ChatRole.USER,
                content=clarification_prompt,
            ),
        )
    # Inner-loop overrides (e.g. ``"none"`` to force a final answer after a
    # repeated handler error) take precedence; otherwise fall through to
    # the caller-supplied ``RunInput.tool_choice`` so the public seam can
    # force a specific tool. None on both sides preserves the legacy
    # ``"auto"`` default applied by the provider adapters.
    tool_loop_state = get_tool_loop_state(context)
    tool_choice = tool_loop_state.tool_choice_override()
    if drafting_plan_refinement:
        tool_choice = "none"
    elif tool_choice is None:
        if (
            context.run_input.app_metadata.get("chat_mode") is True
            and context.llm_step_count > 0
        ):
            tool_choice = None
        else:
            tool_choice = context.run_input.tool_choice
    request_allowed_tools = _combine_request_allowlists(
        _deep_research_request_allowed_tools(context),
        _skill_scope_request_allowed_tools(host, context),
    )
    if request_allowed_tools is not None:
        context.metadata["llm_request_allowed_tools"] = request_allowed_tools
    else:
        context.metadata.pop("llm_request_allowed_tools", None)
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
    # Epic 017: pressure thresholds scale to the model's REAL window automatically when
    # the host left the trimming defaults (explicit host tuning always wins). Without this,
    # the 12k defaults strangled retrieval-heavy prompts on 128k models.
    trimming = host._config.trimming.resolved_for_model(
        provider_model_hint(host._deps.provider)
    )
    if trimming.context_window_source in ("model_catalog", "unresolved_fallback"):
        context.metadata.setdefault(
            "context_window_resolved",
            {
                "window": trimming.context_window_estimate,
                "source": trimming.context_window_source,
            },
        )
    if (
        trimming.context_window_source == "unresolved_fallback"
        and not context.metadata.get("context_window_unresolved_warned")
    ):
        context.metadata["context_window_unresolved_warned"] = True
        emit_step_event(
            host,
            context,
            event_type=RuntimeEventType.WARNING,
            payload={
                "warning": (
                    "Model context window could not be resolved and no "
                    "context_window_estimate was set; assuming a "
                    f"{trimming.context_window_estimate}-token window. Set "
                    "context_window_estimate explicitly for this model "
                    "(especially small local models) to size compaction correctly."
                ),
                "signal_id": "context_window_unresolved_fallback",
                "severity": "warning",
                "assumed_window": trimming.context_window_estimate,
            },
        )
    context_budget = resolve_run_context_budget(
        context.run_input,
        _run_context_budget_defaults(host, trimming=trimming),
        chars_per_token=_calibrated_chars_per_token(context),
    )
    context.metadata["effective_context_budget"] = context_budget.model_dump(
        mode="json"
    )
    request_max_tokens = context.run_input.max_tokens
    max_tokens_source = "run_input.max_tokens"
    if request_max_tokens is None:
        max_tokens_source = "provider_default"
        if (
            context_budget.source.startswith("run_input.")
            and context_budget.output_tokens > 0
        ):
            request_max_tokens = context_budget.output_tokens
            max_tokens_source = f"{context_budget.source}.output_tokens"
    context.metadata["provider_max_tokens_source"] = max_tokens_source
    request, trim_payload = build_single_agent_llm_request(
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
            max_chars=context_budget.max_chars,
            max_messages=context_budget.max_messages,
            max_observations=context_budget.max_observations,
            protect_recent_turns=context_budget.protect_recent_messages,
            context_window_estimate=context_budget.context_window_estimate,
            chars_per_token=_calibrated_chars_per_token(context),
            warning_threshold=context_budget.warning_threshold,
            compact_threshold=context_budget.compact_threshold,
            blocking_threshold=context_budget.blocking_threshold,
            output_token_reserve=context_budget.output_token_reserve,
            request_max_tokens=request_max_tokens,
            stream=is_stream_enabled(context.run_input),
            system_instruction=system_instruction,
            protocol_messages=protocol_messages,
            tool_choice=(
                str(tool_choice)
                if isinstance(tool_choice, str)
                else (tool_choice if isinstance(tool_choice, dict) else None)
            ),
            max_tool_calls_per_step=(
                context.run_input.max_tool_calls_per_step
                if context.run_input.max_tool_calls_per_step is not None
                else host._config.default_max_tool_calls_per_step
            ),
            request_allowed_tools=request_allowed_tools,
            enable_prompt_cache=host._config.enable_prompt_cache,
            harness_profiles=host._config.harness_profiles,
            surface_deferred_tools=_surface_deferred_tools(
                host, context, protocol_messages
            ),
            tool_defer_mode=host._config.capabilities.tool_defer_mode,
            tool_defer_threshold_pct=host._config.capabilities.tool_defer_threshold_pct,
            model_role_map=host._config.capabilities.model_role_map,
            model_router=host._config.capabilities.model_router,
            step_index=context.llm_step_count,
            pre_resolved_model_role=context.metadata.get("llm_routed_role"),
        )
    )
    if get_planning_runtime_state(context).plan_refinement() is None:
        return request, trim_payload
    tools: list[dict[str, Any]] = []
    for tool in request.tools:
        next_tool = dict(tool)
        function = next_tool.get("function")
        if isinstance(function, dict) and function.get("name") == "exit_plan_mode_v2":
            next_function = dict(function)
            parameters = next_function.get("parameters")
            if isinstance(parameters, dict):
                next_parameters = dict(parameters)
                properties = next_parameters.get("properties")
                if isinstance(properties, dict):
                    next_properties = dict(properties)
                    next_properties.pop("plan", None)
                    next_parameters["properties"] = next_properties
                required = list(next_parameters.get("required") or [])
                for field in ("content", "requested_tools", "target_urls"):
                    if field not in required:
                        required.append(field)
                next_parameters["required"] = required
                next_function["parameters"] = next_parameters
                next_tool["function"] = next_function
        tools.append(next_tool)
    return request.model_copy(update={"tools": tools}), trim_payload


def _combine_request_allowlists(
    first: tuple[str, ...] | None,
    second: tuple[str, ...] | None,
) -> tuple[str, ...] | None:
    """Combine two runtime tool-allowlist narrowings by intersection.

    ``None`` means "no narrowing from this source". When both narrow, the effective
    surface is their intersection (a tool must be allowed by every active narrowing). An
    empty result means the two narrowings do not overlap — the surface is fully locked
    down, which is the deterministic (host-configured) outcome of that combination.
    """
    if first is None:
        return second
    if second is None:
        return first
    return tuple(sorted(set(first) & set(second)))


def _skill_scope_request_allowed_tools(
    host: "LlmRequestPrepHost", context: RunContext
) -> tuple[str, ...] | None:
    """Narrow the tool surface to a pinned skill's ``allowed_tools`` (Skills S6).

    A host pins a run to a skill via ``tool_policy.metadata["skill_scope"] = "<name>"``.
    The named skill is resolved from the configured ``skills_catalog_sources`` and the
    model's visible tools are limited to that skill's declared set — plus the skill-load
    tools themselves, so the model can still open the scoped skill. ``None`` (no narrowing)
    when no scope is set, no sources are configured, or the skill declares no tools.
    """
    policy = context.run_input.tool_policy
    scope = policy.metadata.get("skill_scope")
    if not isinstance(scope, str) or not scope.strip():
        return None
    sources = getattr(host._config, "skills_catalog_sources", ())
    if not sources:
        return None
    from agent_driver.skills import resolve_skill_allowed_tools  # noqa: PLC0415

    tools = resolve_skill_allowed_tools(
        tuple(sources),
        scope,
        trusted_roots=tuple(
            getattr(host._config, "skills_catalog_trusted_roots", ()) or ()
        ),
    )
    if not tools:
        return None
    # Keep the skill-load tools reachable so the model can still open the scoped skill.
    return tuple(sorted(set(tools) | {"skill_view", "skill_tool"}))


def _provider_safe_tool_choice(
    context: RunContext, tool_choice: object | None
) -> object | None:
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
