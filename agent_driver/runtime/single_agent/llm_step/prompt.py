"""Prompt and runtime-attachment helpers for LLM-call step."""

from __future__ import annotations

from typing import Protocol

from agent_driver.contracts.enums import AgentProfile, ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.prompts import (
    python_tool_system_addendum,
    react_base_policy,
    react_chat_tool_policy,
    react_chat_tool_policy_fragment_names,
    todo_write_guidance,
)
from agent_driver.runtime.metadata_state import (
    get_planning_runtime_state,
    get_research_runtime_state,
)
from agent_driver.runtime.research_evidence import (
    RESEARCH_DEPTH_DEEP_PARALLEL,
    RESEARCH_DEPTH_SOURCE_VERIFIED,
)
from agent_driver.runtime.research_session_contract import (
    build_research_session_contract_from_context,
)
from agent_driver.runtime.single_agent.llm_step.build import (
    effective_tool_names_from_registry,
)
from agent_driver.runtime.single_agent.types import RunContext, RunnerConfig, RunnerDeps
from agent_driver.runtime.task_contract import render_task_contract_reminder
from agent_driver.skills import CURATED_RESEARCH_SKILL_NAMES, curated_skills_dir


class LlmPromptHost(Protocol):
    """Host surface required for prompt composition helpers."""

    _deps: RunnerDeps
    _config: RunnerConfig


def append_runtime_attachment_messages(
    context: RunContext,
    protocol_messages: tuple[ChatMessage, ...] | None,
) -> tuple[ChatMessage, ...] | None:
    """Append volatile model-facing reminders to protocol messages."""
    attachments = runtime_attachment_messages(context)
    if not attachments:
        return protocol_messages
    if protocol_messages is not None:
        return protocol_messages + attachments
    base_messages = tuple(context.run_input.messages)
    if not base_messages:
        content = str(context.run_input.input or "").strip()
        if content:
            base_messages = (ChatMessage(role=ChatRole.USER, content=content),)
    return base_messages + attachments if base_messages else attachments


def runtime_attachment_messages(context: RunContext) -> tuple[ChatMessage, ...]:
    """Return volatile model-facing chat reminders as request attachments."""
    if context.run_input.app_metadata.get("chat_mode") is not True:
        return tuple()
    lines = chat_mode_runtime_reminders(context)
    task_contract = context.run_input.tool_policy.metadata.get("task_contract")
    if isinstance(task_contract, dict):
        reminder = render_task_contract_reminder(task_contract)
        if reminder:
            lines.append(reminder)
    planning_hint = context.run_input.tool_policy.metadata.get("planning_hint")
    if isinstance(planning_hint, dict):
        level = str(planning_hint.get("level") or "")
        reason = str(planning_hint.get("reason") or "").strip()
        if level == "suggested":
            lines.append(
                "Planning hint: this request looks like non-trivial "
                "implementation work; prefer enter_plan_mode before execution. "
                f"Reason: {reason or 'adaptive planning suggested'}."
            )
        elif level == "required":
            lines.append(
                "Planning hint: approved planning is required before "
                "side-effecting execution. Enter plan mode and call "
                "exit_plan_mode_v2 with concrete plan content."
            )
    return tuple(
        ChatMessage(
            role=ChatRole.USER,
            content=line,
            metadata={"kind": "runtime_attachment"},
        )
        for line in lines
        if line.strip()
    )


def effective_code_agent_imports(host: LlmPromptHost) -> tuple[str, ...]:
    """Return effective authorized imports for code-agent prompt rendering."""
    imports = host._config.authorized_imports
    if imports:
        return imports
    if host._config.python_tool.enabled:
        from agent_driver.tools.builtin.python_imports import effective_python_imports

        return effective_python_imports(host._config.python_tool)
    return tuple()


def effective_request_tool_names(
    host: LlmPromptHost, context: RunContext
) -> tuple[str, ...]:
    """Return effective tool names after request policy filtering."""
    policy = context.run_input.tool_policy
    request_allowed = context.metadata.get("llm_request_allowed_tools")
    allowed_override: tuple[str, ...] | None = None
    if isinstance(request_allowed, tuple) and all(
        isinstance(item, str) for item in request_allowed
    ):
        allowed_override = request_allowed
    elif isinstance(request_allowed, list) and all(
        isinstance(item, str) for item in request_allowed
    ):
        allowed_override = tuple(request_allowed)
    policy_allowed = (
        tuple(policy.allowed_tools) if policy.allowed_tools is not None else None
    )
    if allowed_override is not None and policy_allowed is not None:
        allowed_set = set(allowed_override)
        policy_allowed = tuple(name for name in policy_allowed if name in allowed_set)
    elif allowed_override is not None:
        policy_allowed = allowed_override
    return effective_tool_names_from_registry(
        host._deps.tool_registry,
        allowed=policy_allowed,
        denied=tuple(policy.denied_tools) if policy.denied_tools else None,
    )


def react_system_instruction(host: LlmPromptHost, context: RunContext) -> str | None:
    """Build ReAct system instruction for the current effective tool surface."""
    if context.run_input.agent_profile != AgentProfile.REACT_TEXT:
        return None
    lines = [react_base_policy()]
    if context.run_input.app_metadata.get("chat_mode") is True:
        from agent_driver.tools.builtin.python_imports import scientific_imports_enabled

        effective_tool_names = effective_request_tool_names(host, context)
        prompt_fragments = react_chat_tool_policy_fragment_names(effective_tool_names)
        remember_prompt_surface(
            context,
            effective_tool_names=effective_tool_names,
            prompt_fragments=prompt_fragments,
        )
        lines.append(
            react_chat_tool_policy(
                include_scientific_python=scientific_imports_enabled(
                    host._config.python_tool
                ),
                available_tool_names=effective_tool_names,
            )
        )
    else:
        effective_tool_names = effective_request_tool_names(host, context)
        remember_prompt_surface(
            context,
            effective_tool_names=effective_tool_names,
            prompt_fragments=tuple(),
        )
    python_addendum = python_tool_addendum_if_present(host, effective_tool_names)
    if python_addendum:
        lines.append(python_addendum)
    todo_guidance = todo_write_guidance_if_present(effective_tool_names)
    if todo_guidance:
        lines.append(todo_guidance)
    workspace_cwd = context.run_input.app_metadata.get("workspace_cwd")
    if isinstance(workspace_cwd, str) and workspace_cwd.strip():
        lines.append(f"Workspace cwd: {workspace_cwd.strip()}")
    return "\n".join(lines)


def python_tool_addendum_if_present(
    host: LlmPromptHost, effective_tool_names: tuple[str, ...]
) -> str | None:
    """Return Python tool policy addendum when python is visible."""
    if not host._config.python_tool.enabled:
        return None
    if "python" not in effective_tool_names:
        return None
    return python_tool_system_addendum(host._config.python_tool)


def todo_write_guidance_if_present(
    effective_tool_names: tuple[str, ...],
) -> str | None:
    """Return todo_write guidance when the tool is visible."""
    if "todo_write" not in effective_tool_names:
        return None
    return todo_write_guidance()


def remember_prompt_surface(
    context: RunContext,
    *,
    effective_tool_names: tuple[str, ...],
    prompt_fragments: tuple[str, ...],
) -> None:
    """Persist effective prompt/tool surface for trace diagnostics."""
    metadata = getattr(context, "metadata", None)
    if not isinstance(metadata, dict):
        return
    metadata["effective_tool_names"] = effective_tool_names
    metadata["prompt_fragments"] = prompt_fragments


def chat_mode_runtime_reminders(context: RunContext) -> list[str]:
    """Return compact mode reminders for the chat ReAct prompt."""
    reminders: list[str] = []
    planning_payload = context.metadata.get("planning_state")
    deliverable_requested = current_turn_requests_deliverable(context)
    deliverable_policy = context.run_input.tool_policy.metadata.get(
        "deliverable_request"
    )
    if (
        isinstance(deliverable_policy, dict)
        and deliverable_policy.get("enabled") is True
    ):
        reminders.append(
            "Runtime reminder: deliverable_request_active. The user asked for the "
            "answer/draft now; use tools only if needed for missing facts, then "
            "produce the final deliverable. Do not ask for plan approval or another "
            "clarification unless a safety requirement blocks progress."
        )
    python_policy = context.run_input.tool_policy.metadata.get(
        "python_reliability_request"
    )
    policy_allowed = context.run_input.tool_policy.allowed_tools
    policy_denied = context.run_input.tool_policy.denied_tools or []
    python_allowed_by_policy = "python" not in policy_denied and (
        policy_allowed is None or "python" in policy_allowed
    )
    if (
        isinstance(python_policy, dict)
        and python_policy.get("enabled") is True
        and python_allowed_by_policy
    ):
        reminders.append(
            "Runtime reminder: python_reliability_request. The current user turn "
            "asks for exact calculation/counting; call the python tool before the "
            "final answer, then answer naturally from the execution result."
        )
    task_contract = context.run_input.tool_policy.metadata.get("task_contract")
    if (
        isinstance(task_contract, dict)
        and task_contract.get("research_depth") == RESEARCH_DEPTH_DEEP_PARALLEL
    ):
        deep_contract = build_research_session_contract_from_context(
            context,
            enforce_final_source_links=False,
            allow_final_deliverable_todos=True,
        ).model_dump()["deep_research"]
        if isinstance(deep_contract, dict):
            phase = str(deep_contract.get("phase") or "").strip()
            tools = deep_contract.get("next_allowed_tools")
            tool_text = (
                ", ".join(str(item) for item in tools)
                if isinstance(tools, list)
                else ""
            )
            reminders.append(
                "Runtime reminder: deep_research_phase_contract. "
                f"Current phase={phase or 'unknown'}. "
                f"Prefer next tool(s): {tool_text or 'none; produce final handoff'}. "
                "Move one phase at a time: plan -> discover -> verify -> write -> "
                "review -> final. Do not expand search after verify/write unless "
                "the source ledger shows a concrete coverage gap."
            )
        reminders.append(
            "Runtime reminder: deep_research_artifact_mode. Durable research "
            "output belongs in research/report.md inside the workspace. Keep chat "
            "messages concise progress/final handoff. For long drafts, call "
            "file_write to create research/report.md, then use read_file plus "
            "file_edit or file_patch for targeted revisions instead of rewriting "
            "the full report in chat. Use artifact_list or artifact_preview to "
            "inspect workspace artifacts before final handoff."
        )
        max_subagents = task_contract.get("max_subagent_requests")
        if isinstance(max_subagents, int):
            reminders.append(
                "Runtime reminder: deep_research_subagent_contract. "
                f"Use at most {max_subagents} child research tasks in this run. "
                "Children are for compact source notes only; the parent run owns "
                "research/report.md and research/sources.jsonl and must synthesize "
                "child findings into those artifacts."
            )
        child_synthesis = context.metadata.get("deep_research_child_synthesis")
        if isinstance(child_synthesis, dict) and child_synthesis.get("pending") is True:
            summary = str(child_synthesis.get("summary") or "").strip()
            summary_fragment = (
                f" Child notes preview: {summary[:1200]}" if summary else ""
            )
            reminders.append(
                "Runtime reminder: deep_research_child_synthesis_pending. "
                "A child research group has joined and returned compact source "
                "notes. Do not answer with long prose and do not spawn another "
                "child wave. The next parent step should write or patch "
                "research/sources.jsonl and research/report.md using file_write, "
                "file_patch, file_edit, read_file, or artifact_preview. "
                "Use the embedded child notes preview in this message; do not "
                "try to read child transcript or skill files by absolute path. "
                "Only the parent-run artifact write counts as medium Deep "
                f"Research synthesis.{summary_fragment}"
            )
    if (
        isinstance(task_contract, dict)
        and task_contract.get("research_depth")
        in {RESEARCH_DEPTH_SOURCE_VERIFIED, RESEARCH_DEPTH_DEEP_PARALLEL}
        and "web_fetch" not in policy_denied
        and (policy_allowed is None or "web_fetch" in policy_allowed)
    ):
        reminders.append(
            "Runtime reminder: source_verified_report. For report/deep-research "
            "work, search results are candidates, not evidence. Use web_fetch on "
            "multiple relevant URLs before final synthesis when URLs are available. "
            "When you synthesize the final answer from fetched web evidence, include "
            "Markdown links to the concrete fetched/source URLs you relied on."
        )
        if tool_available_by_policy(
            name="skill_view",
            allowed=policy_allowed,
            denied=policy_denied,
        ):
            reminders.append(research_skill_suggestion_message())
    if get_research_runtime_state(context).fetch_fallback_required():
        reminders.append(
            "Runtime reminder: research_fetch_fallback. Multiple page fetches "
            "failed; do not retry the same fetch loop. If hard-profile source "
            "tools are available, try source_read, then pdf_read for PDF URLs, "
            "then browser_read as a read-only fallback. If no source-read path "
            "works, answer from available search metadata and explicitly state "
            "that full pages could not be verified."
        )
    if get_planning_runtime_state(context).approved_plan():
        reminders.append(
            "Runtime reminder: planning_mode_exit. An approval plan has already "
            "been accepted for this run; continue execution instead of creating "
            "another approval plan."
        )
    if not isinstance(planning_payload, dict):
        return reminders
    todos = planning_payload.get("todos")
    planning_metadata = planning_payload.get("metadata")
    planning_mode = (
        planning_metadata.get("planning_mode")
        if isinstance(planning_metadata, dict)
        else None
    )
    if planning_mode == "plan":
        reminders.append(
            "Runtime reminder: planning_mode_active. Stay read-only, inspect and "
            "reason, ask only blocking questions, then call exit_plan_mode_v2 with "
            "a concrete approval-ready plan before side-effecting execution."
        )
    if isinstance(todos, list) and todos:
        if deliverable_requested:
            reminders.append(
                "Runtime reminder: deliverable_request_active_with_plan. A session "
                "checklist exists, but the current turn asks for the deliverable. "
                "Use existing context and produce the requested final answer in "
                "this turn; update todos only if needed, and do not restart "
                "planning, ask another clarification, or ask for plan approval."
            )
        else:
            reminders.append(
                "Runtime reminder: planning_mode_sparse. Session todos are active: "
                "follow existing todos, update statuses with todo_write "
                "(merge=true) as each step completes, keep moving to the next "
                "unfinished todo, and do not give a final answer until the "
                "requested plan is complete. Do not restate the full plan "
                "checklist in chat."
            )
    return reminders


def tool_available_by_policy(
    *, name: str, allowed: list[str] | tuple[str, ...] | None, denied: list[str]
) -> bool:
    """Return whether a named tool is available under request policy."""
    return name not in denied and (allowed is None or name in allowed)


def research_skill_suggestion_message() -> str:
    """Return runtime reminder for curated research skills."""
    names = ", ".join(CURATED_RESEARCH_SKILL_NAMES)
    return (
        "Runtime reminder: curated_research_skills_available. For "
        "source_verified_report work, relevant bundled skills may help: "
        f"{names}. Discover them with skill_tool using base_dir="
        f"{str(curated_skills_dir())!r} and trusted_roots="
        f"{[str(curated_skills_dir())]!r}, then call skill_view with the same "
        "trusted_roots for a selected skill before relying on it. Do not "
        "auto-load hidden instructions or treat skill listings as evidence."
    )


def current_turn_requests_deliverable(context: RunContext) -> bool:
    """Return whether current user text asks for final deliverable now."""
    current_input = str(context.run_input.input or "").lower()
    return any(
        marker in current_input
        for marker in (
            "write",
            "draft",
            "final",
            "deliverable",
            "напиши",
            "черновик",
            "итог",
            "финал",
            "не план",
        )
    )


__all__ = [
    "append_runtime_attachment_messages",
    "effective_code_agent_imports",
    "react_system_instruction",
    "runtime_attachment_messages",
]
