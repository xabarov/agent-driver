"""Research/source-verification helpers for tool-stage processing."""

from __future__ import annotations

from typing import Any

from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.runtime.metadata_state import (
    get_research_runtime_state,
    get_tool_loop_state,
)
from agent_driver.runtime.research_evidence import (
    RESEARCH_DEPTH_SOURCE_VERIFIED,
    SOURCE_VERIFIED_DOMAINS,
    SOURCE_VERIFIED_FETCHES,
    research_evidence_from_tool_results,
)
from agent_driver.runtime.research_session_contract import (
    FINAL_READINESS_ALLOWED,
    build_research_session_contract_from_context,
)
from agent_driver.runtime.single_agent.types import RunContext
from agent_driver.runtime.tools import ToolExecutionResult


def append_web_fetch_duplicate_guard(
    context: RunContext, result: ToolExecutionResult, messages: list[ChatMessage]
) -> None:
    """Discourage repeated web_fetch when prior fetch already returned usable text."""
    saw_fetch = any(
        envelope.call.tool_name == "web_fetch" for envelope in result.envelopes
    )
    if not saw_fetch:
        return
    prior_fetch_total = int(context.metadata.get("web_fetch_calls_total", 0))
    allowed_fetches_before_warning = required_research_fetch_count(context)
    if prior_fetch_total > max(1, allowed_fetches_before_warning):
        if context.metadata.get("web_fetch_duplicate_guard_sent") is True:
            return
        messages.append(
            ChatMessage(
                role=ChatRole.USER,
                content=(
                    "You already fetched at least one URL. Do not call web_fetch "
                    "again "
                    "unless the previous excerpt/metadata was clearly insufficient."
                ),
            )
        )
        context.metadata["web_fetch_duplicate_guard_sent"] = True


def append_web_fetch_verification_hint(
    context: RunContext, result: ToolExecutionResult, messages: list[ChatMessage]
) -> None:
    """Nudge model to verify web searches with fetched source text."""
    web_search_total = int(context.metadata.get("web_search_calls_total", 0))
    web_fetch_total = int(context.metadata.get("web_fetch_calls_total", 0))
    for envelope in result.envelopes:
        if envelope.call.tool_name == "web_search":
            web_search_total += 1
        elif envelope.call.tool_name == "web_fetch":
            web_fetch_total += 1
    context.metadata["web_search_calls_total"] = web_search_total
    context.metadata["web_fetch_calls_total"] = web_fetch_total
    if web_search_total < 1:
        return
    required_fetches = required_research_fetch_count(context)
    evidence = research_evidence_from_tool_results(
        get_tool_loop_state(context).tool_results()
    )
    if evidence.failed_fetches >= SOURCE_VERIFIED_FETCHES:
        get_research_runtime_state(context).set_fetch_fallback_required()
        messages.append(
            ChatMessage(
                role=ChatRole.USER,
                content=(
                    "Multiple web_fetch attempts failed. Stop retrying fetch for "
                    "this run; synthesize from available search-result metadata "
                    "and explicitly say that full pages could not be verified."
                ),
            )
        )
        return
    successful_fetches = max(web_fetch_total, evidence.successful_fetches)
    unique_domains = len(evidence.unique_domains)
    diversity_pending = (
        successful_fetches >= 1 and unique_domains < SOURCE_VERIFIED_DOMAINS
    )
    if (
        successful_fetches >= required_fetches
        and unique_domains >= SOURCE_VERIFIED_DOMAINS
    ):
        return
    if required_fetches > 1:
        hint_signature = f"{successful_fetches}:{unique_domains}"
        sent_for_count = context.metadata.get("web_fetch_verification_hint_sent_for")
        if sent_for_count == hint_signature:
            return
        remaining = max(required_fetches - successful_fetches, 0)
        if (
            successful_fetches >= required_fetches
            and unique_domains < SOURCE_VERIFIED_DOMAINS
        ):
            avoid_domains = ", ".join(evidence.unique_domains)
            fetch_instruction = (
                "fetch/open at least one additional concrete high-signal URL "
                "from a different domain"
            )
            if avoid_domains:
                fetch_instruction += (
                    f"; do not fetch/open URLs from these already fetched "
                    f"domain(s): {avoid_domains}"
                )
        else:
            fetch_instruction = (
                f"fetch/open at least {remaining} more concrete high-signal "
                "URL(s) with web_fetch"
            )
            if diversity_pending:
                avoid_domains = ", ".join(evidence.unique_domains)
                if avoid_domains:
                    fetch_instruction += (
                        f", preferably outside these already fetched domain(s): "
                        f"{avoid_domains}"
                    )
        messages.append(
            ChatMessage(
                role=ChatRole.USER,
                content=(
                    "This is source-verified research/report work. Search results "
                    f"are only candidates; {fetch_instruction} before final "
                    "synthesis, then cite the fetched sources."
                ),
            )
        )
        context.metadata["web_fetch_verification_hint_sent_for"] = hint_signature
        return
    if context.metadata.get("web_fetch_verification_hint_sent") is True:
        return
    messages.append(
        ChatMessage(
            role=ChatRole.USER,
            content=(
                "You already used web_search. Before concluding on "
                "external-world facts, open at least one returned URL "
                "with web_fetch and cite that URL."
            ),
        )
    )
    context.metadata["web_fetch_verification_hint_sent"] = True


def force_web_fetch_for_source_verified_research(context: RunContext) -> None:
    """Force the next repair turn to verify search candidates with web_fetch."""

    def _clear_fetch_force() -> None:
        if context.metadata.get("continuation_nudge_reason") in {
            "source_verified_fetch_required",
            "source_diversity_search_required",
            "source_diversity_fetch_required",
        }:
            context.metadata.pop("continuation_nudge_reason", None)
        context.metadata.pop("research_avoid_domains", None)
        context.metadata.pop("research_source_diversity_avoid_domains", None)

    if get_tool_loop_state(context).force_final_answer_enabled():
        _clear_fetch_force()
        return
    if not source_verified_research_pending(context):
        _clear_fetch_force()
        return
    evidence = research_evidence_from_tool_results(
        get_tool_loop_state(context).tool_results()
    )
    if evidence.search_calls < 1:
        _clear_fetch_force()
        return
    if source_diversity_repair_pending(evidence):
        domains = ", ".join(evidence.unique_domains)
        get_research_runtime_state(context).set_avoid_domains(
            list(evidence.unique_domains)
        )
        if last_research_tool_name(context) == "web_search":
            get_tool_loop_state(context).set_tool_choice_override(
                {
                    "type": "tool",
                    "name": "web_fetch",
                }
            )
            context.metadata["continuation_nudge_reason"] = (
                "source_diversity_fetch_required"
            )
        else:
            get_tool_loop_state(context).set_tool_choice_override(
                {
                    "type": "tool",
                    "name": "web_search",
                }
            )
            context.metadata["continuation_nudge_reason"] = (
                "source_diversity_search_required"
            )
        if domains:
            context.metadata["research_source_diversity_avoid_domains"] = domains
        return
    if evidence.failed_fetches >= SOURCE_VERIFIED_FETCHES:
        _clear_fetch_force()
        return
    get_tool_loop_state(context).set_tool_choice_override(
        {
            "type": "tool",
            "name": "web_fetch",
        }
    )
    context.metadata["continuation_nudge_reason"] = "source_verified_fetch_required"


def research_request_should_force_final(context: RunContext) -> bool:
    """Return whether a research task has enough tool evidence to synthesize."""
    task_contract = context.run_input.tool_policy.metadata.get("task_contract")
    if not isinstance(task_contract, dict):
        return False
    if task_contract.get("kind") != "research":
        return False
    if task_contract.get("requires_research") is not True:
        return False
    if task_contract.get("research_mode") == "deep":
        contract = build_research_session_contract_from_context(
            context,
            enforce_final_source_links=False,
            allow_final_deliverable_todos=True,
        )
        if contract.final_readiness.status != FINAL_READINESS_ALLOWED:
            return False
    tool_results = get_tool_loop_state(context).tool_results()
    if task_contract.get("research_depth") == RESEARCH_DEPTH_SOURCE_VERIFIED:
        if not tool_available(context, "web_fetch"):
            return any(
                isinstance(item, dict)
                and isinstance(item.get("call"), dict)
                and item["call"].get("tool_name") == "web_search"
                for item in tool_results
            )
        evidence = research_evidence_from_tool_results(tool_results)
        if evidence.failed_fetches >= SOURCE_VERIFIED_FETCHES and (
            evidence.successful_fetches == 0
            and (evidence.search_calls > 0 or evidence.fetch_calls > 0)
        ):
            get_research_runtime_state(context).set_fetch_fallback_required()
            return True
        return evidence.source_verified(
            required_fetches=SOURCE_VERIFIED_FETCHES,
            required_domains=SOURCE_VERIFIED_DOMAINS,
        )
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        call = item.get("call")
        if not isinstance(call, dict):
            continue
        if call.get("tool_name") in {"web_search", "web_fetch"}:
            return True
    return False


def source_verified_research_pending(context: RunContext) -> bool:
    """Return whether source-verified research still needs verified reads."""
    task_contract = task_contract_metadata(context)
    if not isinstance(task_contract, dict):
        return False
    if task_contract.get("research_depth") != RESEARCH_DEPTH_SOURCE_VERIFIED:
        return False
    if not tool_available(context, "web_fetch"):
        return False
    evidence = research_evidence_from_tool_results(
        get_tool_loop_state(context).tool_results()
    )
    if evidence.failed_fetches >= SOURCE_VERIFIED_FETCHES and (
        evidence.successful_fetches == 0
        and (evidence.search_calls > 0 or evidence.fetch_calls > 0)
    ):
        return False
    return not evidence.source_verified(
        required_fetches=SOURCE_VERIFIED_FETCHES,
        required_domains=SOURCE_VERIFIED_DOMAINS,
    )


def source_diversity_repair_pending(evidence: Any) -> bool:
    """Return whether fetched evidence lacks required domain diversity."""
    return (
        getattr(evidence, "successful_fetches", 0) >= SOURCE_VERIFIED_FETCHES
        and len(getattr(evidence, "unique_domains", ())) < SOURCE_VERIFIED_DOMAINS
    )


def last_research_tool_name(context: RunContext) -> str | None:
    """Return the latest web research tool name from stored tool results."""
    tool_results = get_tool_loop_state(context).tool_results()
    for item in reversed(tool_results):
        if not isinstance(item, dict):
            continue
        call = item.get("call")
        if not isinstance(call, dict):
            continue
        tool_name = str(call.get("tool_name") or "").strip()
        if tool_name in {"web_search", "web_fetch"}:
            return tool_name
    return None


def required_research_fetch_count(context: RunContext) -> int:
    """Return required verified fetch count for the current task contract."""
    task_contract = task_contract_metadata(context)
    if (
        isinstance(task_contract, dict)
        and task_contract.get("research_depth") == RESEARCH_DEPTH_SOURCE_VERIFIED
        and tool_available(context, "web_fetch")
    ):
        return SOURCE_VERIFIED_FETCHES
    return 1


def task_contract_metadata(context: RunContext) -> object:
    """Return task contract metadata from run input policy, if present."""
    run_input = getattr(context, "run_input", None)
    policy = getattr(run_input, "tool_policy", None)
    metadata = getattr(policy, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    return metadata.get("task_contract")


def tool_available(context: RunContext, tool_name: str) -> bool:
    """Return whether a tool is available under effective and policy surfaces."""
    effective_tool_names = get_tool_loop_state(context).effective_tool_names()
    if effective_tool_names is not None:
        return tool_name in effective_tool_names
    policy = context.run_input.tool_policy
    denied = getattr(policy, "denied_tools", None) or []
    allowed = getattr(policy, "allowed_tools", None)
    return tool_name not in denied and (allowed is None or tool_name in allowed)


__all__ = [
    "append_web_fetch_duplicate_guard",
    "append_web_fetch_verification_hint",
    "force_web_fetch_for_source_verified_research",
    "research_request_should_force_final",
    "source_verified_research_pending",
]
