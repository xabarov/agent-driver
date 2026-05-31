"""Research/todo final-readiness contract for chat-style runs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agent_driver.contracts.context import PlanningState
from agent_driver.contracts.enums import PlanningTodoStatus
from agent_driver.runtime.metadata_state import (
    get_planning_runtime_state,
    get_tool_loop_state,
)
from agent_driver.runtime.research_evidence import (
    RESEARCH_DEPTH_LIGHT,
    RESEARCH_DEPTH_NONE,
    RESEARCH_DEPTH_SOURCE_VERIFIED,
    SOURCE_VERIFIED_DOMAINS,
    SOURCE_VERIFIED_FETCHES,
    WEB_FETCH_TOOL,
    ResearchEvidenceState,
    research_evidence_from_tool_results,
)

if TYPE_CHECKING:
    from agent_driver.runtime.single_agent.types import RunContext

FINAL_READINESS_ALLOWED = "allowed"
FINAL_READINESS_REPAIR_NEEDED = "repair_needed"
FINAL_READINESS_BLOCKED_BY_PROVIDER = "blocked_by_provider"

REPAIR_MISSING_RESEARCH_EVIDENCE = "missing_research_evidence"
REPAIR_MISSING_FETCHED_SOURCES = "missing_fetched_sources"
REPAIR_INSUFFICIENT_SOURCE_DIVERSITY = "insufficient_source_diversity"
REPAIR_FINAL_MISSING_SOURCE_LINKS = "final_missing_source_links"
REPAIR_UNFINISHED_TODOS = "unfinished_todos"


@dataclass(frozen=True)
class ResearchFinalReadiness:
    """Whether a research/todo turn may produce its final answer now."""

    status: str
    reasons: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        return self.status == FINAL_READINESS_ALLOWED


@dataclass(frozen=True)
class ResearchSessionContract:
    """Small computed contract for research evidence and visible todo progress."""

    requires_research: bool
    research_depth: str
    evidence: ResearchEvidenceState
    web_fetch_available: bool
    fetch_required: bool = False
    unfinished_todos: tuple[str, ...] = ()
    final_has_source_links: bool = False
    enforce_final_source_links: bool = True
    enforce_todos: bool = True
    fetch_fallback_required: bool = False

    @property
    def final_readiness(self) -> ResearchFinalReadiness:
        reasons: list[str] = []
        if self.enforce_todos and self.unfinished_todos:
            reasons.append(REPAIR_UNFINISHED_TODOS)
        if self.requires_research:
            reasons.extend(self._research_repair_reasons())
        if reasons:
            return ResearchFinalReadiness(
                status=FINAL_READINESS_REPAIR_NEEDED,
                reasons=tuple(dict.fromkeys(reasons)),
            )
        return ResearchFinalReadiness(status=FINAL_READINESS_ALLOWED)

    def _research_repair_reasons(self) -> list[str]:
        if self.research_depth == RESEARCH_DEPTH_NONE:
            return []
        if self.evidence.search_calls == 0 and self.evidence.fetch_calls == 0:
            return [REPAIR_MISSING_RESEARCH_EVIDENCE]
        if (
            self.fetch_required
            and self.web_fetch_available
            and self.evidence.successful_fetches < 1
        ):
            return [REPAIR_MISSING_FETCHED_SOURCES]
        if self.research_depth != RESEARCH_DEPTH_SOURCE_VERIFIED:
            return []
        if not self.web_fetch_available:
            return []
        if self.fetch_fallback_required:
            return []
        reasons: list[str] = []
        if self.evidence.successful_fetches < SOURCE_VERIFIED_FETCHES:
            reasons.append(REPAIR_MISSING_FETCHED_SOURCES)
        elif len(self.evidence.unique_domains) < SOURCE_VERIFIED_DOMAINS:
            reasons.append(REPAIR_INSUFFICIENT_SOURCE_DIVERSITY)
        elif self.enforce_final_source_links and not self.final_has_source_links:
            reasons.append(REPAIR_FINAL_MISSING_SOURCE_LINKS)
        return reasons

    def model_dump(self) -> dict[str, Any]:
        readiness = self.final_readiness
        return {
            "requires_research": self.requires_research,
            "research_depth": self.research_depth,
            "web_fetch_available": self.web_fetch_available,
            "fetch_required": self.fetch_required,
            "final_readiness": readiness.status,
            "repair_required_reasons": list(readiness.reasons),
            "fetch_fallback_required": self.fetch_fallback_required,
            "unfinished_todos": list(self.unfinished_todos),
            "final_has_source_links": self.final_has_source_links,
            "enforce_final_source_links": self.enforce_final_source_links,
            "enforce_todos": self.enforce_todos,
            "evidence": {
                "search_calls": self.evidence.search_calls,
                "fetch_calls": self.evidence.fetch_calls,
                "successful_fetches": self.evidence.successful_fetches,
                "failed_fetches": self.evidence.failed_fetches,
                "unique_domains": list(self.evidence.unique_domains),
            },
        }


def build_research_session_contract(
    *,
    task_contract: dict[str, Any] | None,
    tool_results: object,
    planning_state: object = None,
    assistant_text: str = "",
    web_fetch_available: bool = True,
    enforce_final_source_links: bool = True,
    enforce_todos: bool = True,
    allow_final_deliverable_todos: bool = False,
) -> ResearchSessionContract:
    """Build the final-readiness contract from current runtime state."""
    requires_research = (
        isinstance(task_contract, dict)
        and task_contract.get("requires_research") is True
    )
    research_depth = _research_depth_from_task_contract(task_contract)
    fetch_required = _fetch_required_from_task_contract(task_contract)
    evidence = research_evidence_from_tool_results(tool_results)
    fetch_fallback_required = (
        research_depth == RESEARCH_DEPTH_SOURCE_VERIFIED
        and web_fetch_available
        and evidence.failed_fetches >= SOURCE_VERIFIED_FETCHES
        and evidence.successful_fetches == 0
        and (evidence.search_calls > 0 or evidence.fetch_calls > 0)
    )
    final_answer_covers_research_process_todos = (
        requires_research
        and _meaningful_final_answer(assistant_text)
        and (not enforce_final_source_links or has_source_links(assistant_text))
        and _research_evidence_satisfied(
            research_depth=research_depth,
            evidence=evidence,
            web_fetch_available=web_fetch_available,
            fetch_required=fetch_required,
            fetch_fallback_required=fetch_fallback_required,
        )
    )
    return ResearchSessionContract(
        requires_research=requires_research,
        research_depth=research_depth,
        evidence=evidence,
        web_fetch_available=web_fetch_available,
        fetch_required=fetch_required,
        unfinished_todos=tuple(
            _unfinished_todo_labels(
                planning_state,
                assistant_text,
                allow_final_deliverable_todos=allow_final_deliverable_todos,
                allow_all_todos=final_answer_covers_research_process_todos,
            )
        ),
        final_has_source_links=has_source_links(assistant_text),
        enforce_final_source_links=enforce_final_source_links,
        enforce_todos=enforce_todos,
        fetch_fallback_required=fetch_fallback_required,
    )


def build_research_session_contract_from_context(
    context: RunContext,
    *,
    assistant_text: str = "",
    enforce_final_source_links: bool = True,
    enforce_todos: bool = True,
    allow_final_deliverable_todos: bool = False,
) -> ResearchSessionContract:
    """Build a research contract from a single-agent run context."""
    return build_research_session_contract(
        task_contract=_task_contract_from_context(context),
        tool_results=get_tool_loop_state(context).tool_results(),
        planning_state=get_planning_runtime_state(context).planning_state(),
        assistant_text=assistant_text,
        web_fetch_available=_tool_available(context, WEB_FETCH_TOOL),
        enforce_final_source_links=enforce_final_source_links,
        enforce_todos=enforce_todos,
        allow_final_deliverable_todos=allow_final_deliverable_todos,
    )


def has_source_links(text: str) -> bool:
    """Return True when final text includes at least one visible URL citation."""
    return bool(re.search(r"https?://|\[[^\]]+\]\(https?://", text or ""))


def unfinished_todo_labels(
    planning_state: object, *, assistant_text: str = ""
) -> list[str]:
    """Return visible todos that still require tool/model progress."""
    return _unfinished_todo_labels(planning_state, assistant_text)


def _research_depth_from_task_contract(task_contract: dict[str, Any] | None) -> str:
    if not isinstance(task_contract, dict):
        return RESEARCH_DEPTH_NONE
    depth = task_contract.get("research_depth")
    if depth in {
        RESEARCH_DEPTH_NONE,
        RESEARCH_DEPTH_LIGHT,
        RESEARCH_DEPTH_SOURCE_VERIFIED,
    }:
        return str(depth)
    return (
        RESEARCH_DEPTH_LIGHT
        if task_contract.get("requires_research") is True
        else RESEARCH_DEPTH_NONE
    )


def _fetch_required_from_task_contract(task_contract: dict[str, Any] | None) -> bool:
    return (
        isinstance(task_contract, dict) and task_contract.get("fetch_required") is True
    )


def _unfinished_todo_labels(
    planning_state: object,
    assistant_text: str = "",
    *,
    allow_final_deliverable_todos: bool = False,
    allow_all_todos: bool = False,
) -> list[str]:
    if not isinstance(planning_state, dict):
        return []
    state = PlanningState.model_validate(planning_state)
    labels: list[str] = []
    for item in state.todos:
        if item.status not in {
            PlanningTodoStatus.PENDING,
            PlanningTodoStatus.IN_PROGRESS,
        }:
            continue
        if allow_all_todos:
            continue
        if _final_answer_covers_todo(
            todo_id=item.todo_id,
            content=item.content,
            assistant_text=assistant_text,
        ):
            continue
        if allow_final_deliverable_todos and _is_final_deliverable_todo(
            todo_id=item.todo_id,
            content=item.content,
        ):
            continue
        labels.append(f"{item.todo_id}: {item.content}")
    return labels


_FINAL_DELIVERABLE_TODO_MARKERS = (
    "summary",
    "summar",
    "synthesis",
    "synthesize",
    "report",
    "output",
    "final",
    "answer",
    "итог",
    "свод",
    "обобщ",
    "вывод",
    "ответ",
    "отчет",
    "отчёт",
)


def _final_answer_covers_todo(
    *,
    todo_id: str,
    content: str,
    assistant_text: str,
) -> bool:
    """Treat a meaningful final answer as completing a final synthesis todo."""
    if not _meaningful_final_answer(assistant_text):
        return False
    return _is_final_deliverable_todo(todo_id=todo_id, content=content)


def _meaningful_final_answer(assistant_text: str) -> bool:
    return len((assistant_text or "").strip()) >= 200


def _is_final_deliverable_todo(*, todo_id: str, content: str) -> bool:
    haystack = f"{todo_id} {content}".lower()
    return any(marker in haystack for marker in _FINAL_DELIVERABLE_TODO_MARKERS)


def _research_evidence_satisfied(
    *,
    research_depth: str,
    evidence: ResearchEvidenceState,
    web_fetch_available: bool,
    fetch_required: bool,
    fetch_fallback_required: bool,
) -> bool:
    if research_depth == RESEARCH_DEPTH_NONE:
        return True
    if evidence.search_calls == 0 and evidence.fetch_calls == 0:
        return False
    if fetch_required and web_fetch_available and evidence.successful_fetches < 1:
        return False
    if research_depth != RESEARCH_DEPTH_SOURCE_VERIFIED:
        return True
    if not web_fetch_available or fetch_fallback_required:
        return True
    return evidence.source_verified(
        required_fetches=SOURCE_VERIFIED_FETCHES,
        required_domains=SOURCE_VERIFIED_DOMAINS,
    )


def _task_contract_from_context(context: RunContext) -> dict[str, Any] | None:
    metadata = context.run_input.tool_policy.metadata
    task_contract = metadata.get("task_contract")
    return task_contract if isinstance(task_contract, dict) else None


def _tool_available(context: RunContext, tool_name: str) -> bool:
    effective_tool_names = get_tool_loop_state(context).effective_tool_names()
    if effective_tool_names is not None:
        return tool_name in effective_tool_names
    policy = context.run_input.tool_policy
    denied = getattr(policy, "denied_tools", None) or []
    allowed = getattr(policy, "allowed_tools", None)
    return tool_name not in denied and (allowed is None or tool_name in allowed)


__all__ = [
    "FINAL_READINESS_ALLOWED",
    "FINAL_READINESS_BLOCKED_BY_PROVIDER",
    "FINAL_READINESS_REPAIR_NEEDED",
    "REPAIR_FINAL_MISSING_SOURCE_LINKS",
    "REPAIR_INSUFFICIENT_SOURCE_DIVERSITY",
    "REPAIR_MISSING_FETCHED_SOURCES",
    "REPAIR_MISSING_RESEARCH_EVIDENCE",
    "REPAIR_UNFINISHED_TODOS",
    "ResearchFinalReadiness",
    "ResearchSessionContract",
    "build_research_session_contract",
    "build_research_session_contract_from_context",
    "has_source_links",
    "unfinished_todo_labels",
]
