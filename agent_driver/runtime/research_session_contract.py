"""Research/todo final-readiness contract for chat-style runs."""


from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agent_driver.runtime.metadata_state import (
    get_planning_runtime_state,
    get_tool_loop_state,
)
from agent_driver.runtime.research_evidence import (
    RESEARCH_DEPTH_DEEP_PARALLEL,
    RESEARCH_DEPTH_NONE,
    SOURCE_VERIFIED_FETCHES,
    ResearchEvidenceState,
    ResearchSourceLedger,
    research_evidence_from_tool_results,
    research_source_ledger_from_tool_results,
    rollup_child_source_ledgers,
)

if TYPE_CHECKING:
    from agent_driver.runtime.single_agent.types import RunContext
from agent_driver.runtime.research_signals import (
    _READ_SOURCE_TOOLS,
    _SOURCE_VERIFIED_DEPTHS,
    _child_synthesis_pending,
    _evidence_floor,
    _fetch_required_from_task_contract,
    _hard_claims_state_from_context,
    _meaningful_final_answer,
    _parent_review_pending,
    _plan_created,
    _research_depth_from_task_contract,
    _research_evidence_satisfied,
    _task_contract_from_context,
    _tool_available,
    _tool_policy_allows,
    _tool_result_count,
    _unfinished_todo_labels,
    child_source_ledgers_from_context,
    deep_research_parent_review_next_tool,
    deep_research_parent_review_pending,
    has_source_links,
    parent_review_actions_seen,
    unfinished_todo_labels,
)


FINAL_READINESS_ALLOWED = "allowed"


FINAL_READINESS_REPAIR_NEEDED = "repair_needed"


FINAL_READINESS_BLOCKED_BY_PROVIDER = "blocked_by_provider"


REPAIR_MISSING_RESEARCH_EVIDENCE = "missing_research_evidence"


REPAIR_MISSING_FETCHED_SOURCES = "missing_fetched_sources"


REPAIR_INSUFFICIENT_SOURCE_DIVERSITY = "insufficient_source_diversity"


REPAIR_FINAL_MISSING_SOURCE_LINKS = "final_missing_source_links"


REPAIR_UNFINISHED_TODOS = "unfinished_todos"


REPAIR_CHILD_SYNTHESIS_PENDING = "child_synthesis_pending"


REPAIR_PARENT_REVIEW_PENDING = "parent_review_pending"


# Hard-profile claim audit (opt-in: only enforced when the task contract sets
# hard_options.enforce_claims_audit). The scaffold in research_artifacts auto
# -derives research/claims.jsonl from the source ledger; these gates make a
# hard run finish only once that audit carries verified support.
REPAIR_HARD_CLAIMS_UNVERIFIED = "hard_claims_unverified"


REPAIR_HARD_CLAIMS_UNSUPPORTED = "hard_claims_unsupported"


DEEP_RESEARCH_PHASE_PLAN = "plan"


DEEP_RESEARCH_PHASE_DISCOVER = "discover"


DEEP_RESEARCH_PHASE_VERIFY = "verify"


DEEP_RESEARCH_PHASE_WRITE = "write"


DEEP_RESEARCH_PHASE_REVIEW = "review"


DEEP_RESEARCH_PHASE_FINAL = "final"


_DEEP_RESEARCH_PHASE_TOOLS: dict[str, tuple[str, ...]] = {
    DEEP_RESEARCH_PHASE_PLAN: ("todo_write", "skill_tool", "skill_view"),
    DEEP_RESEARCH_PHASE_DISCOVER: (
        "agent_tool",
        "skill_tool",
        "skill_view",
        "web_search",
        *_READ_SOURCE_TOOLS,
        "glob_search",
        "grep_search",
        "read_file",
        "todo_write",
    ),
    DEEP_RESEARCH_PHASE_VERIFY: (
        "agent_tool",
        *_READ_SOURCE_TOOLS,
        "web_search",
        "read_file",
        "todo_write",
    ),
    DEEP_RESEARCH_PHASE_WRITE: (
        "file_write",
        "file_edit",
        "file_patch",
        "read_file",
        "artifact_list",
        "artifact_read",
        "artifact_preview",
        "todo_write",
    ),
    DEEP_RESEARCH_PHASE_REVIEW: (
        "artifact_list",
        "artifact_preview",
        "artifact_read",
        "read_file",
        "file_patch",
        "file_edit",
        *_READ_SOURCE_TOOLS,
        "todo_write",
    ),
    DEEP_RESEARCH_PHASE_FINAL: (),
}


@dataclass(frozen=True)
class ResearchFinalReadiness:
    """Whether a research/todo turn may produce its final answer now."""

    status: str
    reasons: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        return self.status == FINAL_READINESS_ALLOWED


@dataclass(frozen=True)
class DeepResearchControllerState:
    """Derived state machine view for artifact-first Deep Research control."""

    phase: str
    readiness: str
    report_artifact_exists: bool
    source_ledger_artifact_exists: bool
    child_synthesis_pending: bool
    report_required: bool
    source_ledger_required: bool
    final_handoff_ready: bool
    next_allowed_tools: tuple[str, ...]

    def model_dump(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "readiness": self.readiness,
            "report_artifact_exists": self.report_artifact_exists,
            "source_ledger_artifact_exists": self.source_ledger_artifact_exists,
            "child_synthesis_pending": self.child_synthesis_pending,
            "report_required": self.report_required,
            "source_ledger_required": self.source_ledger_required,
            "final_handoff_ready": self.final_handoff_ready,
            "next_allowed_tools": list(self.next_allowed_tools),
        }


@dataclass(frozen=True)
class ResearchSessionContract:
    """Small computed contract for research evidence and visible todo progress."""

    requires_research: bool
    research_depth: str
    evidence: ResearchEvidenceState
    source_ledger: ResearchSourceLedger
    web_fetch_available: bool
    fetch_required: bool = False
    unfinished_todos: tuple[str, ...] = ()
    final_has_source_links: bool = False
    enforce_final_source_links: bool = True
    enforce_todos: bool = True
    fetch_fallback_required: bool = False
    report_artifact_exists: bool = False
    source_ledger_artifact_exists: bool = False
    plan_created: bool = False
    child_synthesis_pending: bool = False
    parent_review_pending: bool = False
    hard_profile: bool = False
    enforce_hard_claims: bool = False
    claims_verified_count: int = 0
    claims_unsupported_count: int = 0

    @property
    def final_readiness(self) -> ResearchFinalReadiness:
        reasons: list[str] = []
        if self.child_synthesis_pending:
            reasons.append(REPAIR_CHILD_SYNTHESIS_PENDING)
        if self.parent_review_pending:
            reasons.append(REPAIR_PARENT_REVIEW_PENDING)
        if self.enforce_todos and self.unfinished_todos:
            reasons.append(REPAIR_UNFINISHED_TODOS)
        if self.requires_research:
            reasons.extend(self._research_repair_reasons())
        if self.hard_profile and self.enforce_hard_claims:
            reasons.extend(self._hard_claims_repair_reasons())
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
        if self.research_depth not in _SOURCE_VERIFIED_DEPTHS:
            return []
        if not self.web_fetch_available:
            return []
        if self.fetch_fallback_required:
            return []
        reasons: list[str] = []
        required_fetches, required_domains = _evidence_floor(self.research_depth)
        if self.evidence.successful_fetches < required_fetches:
            reasons.append(REPAIR_MISSING_FETCHED_SOURCES)
        elif len(self.evidence.unique_domains) < required_domains:
            reasons.append(REPAIR_INSUFFICIENT_SOURCE_DIVERSITY)
        elif self.enforce_final_source_links and not self.final_has_source_links:
            reasons.append(REPAIR_FINAL_MISSING_SOURCE_LINKS)
        return reasons

    def _hard_claims_repair_reasons(self) -> list[str]:
        reasons: list[str] = []
        # No verified claim row means the claims audit is missing, empty, or
        # carries only inaccessible/unsupported rows — the hard run has no
        # verified evidence to stand on yet.
        if self.claims_verified_count <= 0:
            reasons.append(REPAIR_HARD_CLAIMS_UNVERIFIED)
        if self.claims_unsupported_count > 0:
            reasons.append(REPAIR_HARD_CLAIMS_UNSUPPORTED)
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
            "source_ledger": self.source_ledger.model_dump(),
            "deep_research": _deep_research_contract_payload(self),
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
    report_artifact_exists: bool = False,
    source_ledger_artifact_exists: bool = False,
    child_synthesis_pending: bool = False,
    child_source_ledgers: object = None,
    hard_profile: bool = False,
    enforce_hard_claims: bool = False,
    claims_verified_count: int = 0,
    claims_unsupported_count: int = 0,
) -> ResearchSessionContract:
    """Build the final-readiness contract from current runtime state."""
    requires_research = (
        isinstance(task_contract, dict)
        and task_contract.get("requires_research") is True
    )
    research_depth = _research_depth_from_task_contract(task_contract)
    fetch_required = _fetch_required_from_task_contract(task_contract)
    # Parent-only evidence (before child roll-up) — used to require the parent to
    # do at least one verify-fetch of its own once children have joined.
    parent_evidence = research_evidence_from_tool_results(tool_results)
    source_ledger = research_source_ledger_from_tool_results(
        tool_results,
        assistant_text=assistant_text,
    )
    # Roll child researchers' verified reads up into the parent ledger/evidence.
    # A delegating parent often fetches nothing itself; without this the parent
    # contract reports "missing research evidence" even though its children read
    # real pages. Children only ever add evidence on top of the parent's own.
    source_ledger, evidence = rollup_child_source_ledgers(
        source_ledger, parent_evidence, child_source_ledgers
    )
    parent_review_pending = _parent_review_pending(
        requires_research=requires_research,
        research_depth=research_depth,
        web_fetch_available=web_fetch_available,
        parent_evidence=parent_evidence,
        tool_results=tool_results,
        child_source_ledgers=child_source_ledgers,
    )
    plan_created = _plan_created(planning_state)
    fetch_fallback_required = (
        research_depth in _SOURCE_VERIFIED_DEPTHS
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
        source_ledger=source_ledger,
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
        report_artifact_exists=report_artifact_exists,
        source_ledger_artifact_exists=source_ledger_artifact_exists,
        plan_created=plan_created,
        child_synthesis_pending=child_synthesis_pending,
        parent_review_pending=parent_review_pending,
        hard_profile=hard_profile,
        enforce_hard_claims=enforce_hard_claims,
        claims_verified_count=claims_verified_count,
        claims_unsupported_count=claims_unsupported_count,
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
    from agent_driver.runtime.research_artifacts import (
        deep_research_report_artifact_exists,
        deep_research_source_ledger_artifact_exists,
    )

    return build_research_session_contract(
        task_contract=_task_contract_from_context(context),
        tool_results=get_tool_loop_state(context).tool_results(),
        planning_state=get_planning_runtime_state(context).planning_state(),
        assistant_text=assistant_text,
        web_fetch_available=any(
            _tool_available(context, tool_name) for tool_name in _READ_SOURCE_TOOLS
        ),
        enforce_final_source_links=enforce_final_source_links,
        enforce_todos=enforce_todos,
        allow_final_deliverable_todos=allow_final_deliverable_todos,
        report_artifact_exists=deep_research_report_artifact_exists(context),
        source_ledger_artifact_exists=deep_research_source_ledger_artifact_exists(
            context
        ),
        child_synthesis_pending=_child_synthesis_pending(context),
        child_source_ledgers=child_source_ledgers_from_context(context),
        **_hard_claims_state_from_context(context),
    )


# Bound the parent's own verify-fetches so a stuck/blocked domain cannot loop
# the run forever while chasing the diversity floor. Beyond this many parent
# fetch attempts the run finalizes with whatever coverage it has. The diversity
# repair nudge steers each attempt at an untried domain, so a handful of tries
# is enough to clear the floor when an accessible source exists.
_PARENT_VERIFY_FETCH_ATTEMPT_CAP = 6


def deep_research_post_artifact_next_tool(context: RunContext) -> str | None:
    """Next tool to force once both research artifacts exist but the run is not
    yet final-ready.

    Generalises the parent verify+review pass to also cover the discovery floor:
    after the review trio + first verify-fetch clear ``parent_review_pending``,
    a deep-parallel parent may still be short of the rolled-up fetch/domain
    minimums. In that case force another parent verify-fetch (bounded by
    ``_PARENT_VERIFY_FETCH_ATTEMPT_CAP`` so a blocked domain cannot spin). Returns
    ``None`` when the contract is final-ready or when no tool can close the gap
    (remaining reasons are handled by the repair nudge text).
    """
    review_tool = deep_research_parent_review_next_tool(context)
    if review_tool is not None:
        return review_tool
    contract = build_research_session_contract_from_context(context)
    readiness = contract.final_readiness
    if readiness.status == FINAL_READINESS_ALLOWED:
        return None
    if (
        REPAIR_MISSING_FETCHED_SOURCES in readiness.reasons
        or REPAIR_INSUFFICIENT_SOURCE_DIVERSITY in readiness.reasons
    ):
        tool_results = get_tool_loop_state(context).tool_results()
        parent_evidence = research_evidence_from_tool_results(tool_results)
        if parent_evidence.fetch_calls < _PARENT_VERIFY_FETCH_ATTEMPT_CAP and (
            _tool_policy_allows(context, "web_fetch")
        ):
            return "web_fetch"
        if contract.report_artifact_exists and contract.source_ledger_artifact_exists:
            return None
    if (
        REPAIR_UNFINISHED_TODOS in readiness.reasons
        and _tool_policy_allows(context, "todo_write")
    ):
        if _tool_result_count(context, "todo_write") <= 1:
            return "todo_write"
        final_contract = build_research_session_contract_from_context(
            context,
            enforce_final_source_links=False,
            enforce_todos=False,
            allow_final_deliverable_todos=True,
        )
        if final_contract.final_readiness.status == FINAL_READINESS_ALLOWED:
            return None
        return "todo_write"
    return None


def _deep_research_contract_payload(
    contract: ResearchSessionContract,
) -> dict[str, Any] | None:
    if contract.research_depth != RESEARCH_DEPTH_DEEP_PARALLEL:
        return None
    phase = _deep_research_phase(contract)
    allowed_tools = _DEEP_RESEARCH_PHASE_TOOLS[phase]
    controller_state = _deep_research_controller_state(
        contract,
        phase=phase,
        allowed_tools=allowed_tools,
    )
    return {
        "mode": RESEARCH_DEPTH_DEEP_PARALLEL,
        "phase": phase,
        "next_allowed_tools": list(allowed_tools),
        "phase_contract": "soft_guidance",
        "progress_event_types": [
            "research_progress",
            "source_ledger_updated",
            "citation_coverage_updated",
        ],
        "source_ledger_enabled": True,
        "context_pressure_recommendations": [
            "write_incremental_findings_to_workspace_before_blocking_pressure",
            "parent_keeps_final_synthesis_and_citation_coverage",
        ],
        "workspace_strategy": "use_research_report_md_and_source_ledger_for_long_outputs",
        "final_citation_coverage": {
            "verified_read_count": len(contract.source_ledger.verified_reads),
            "assistant_link_count": len(contract.source_ledger.assistant_links),
            "final_has_source_links": contract.final_has_source_links,
        },
        "report_artifact_exists": contract.report_artifact_exists,
        "source_ledger_artifact_exists": contract.source_ledger_artifact_exists,
        "plan_created": contract.plan_created,
        "child_synthesis_pending": contract.child_synthesis_pending,
        "parent_review_pending": contract.parent_review_pending,
        "final_readiness_authority": "ResearchSessionContract",
        "controller_state": controller_state.model_dump(),
    }


def _deep_research_controller_state(
    contract: ResearchSessionContract,
    *,
    phase: str,
    allowed_tools: tuple[str, ...],
) -> DeepResearchControllerState:
    final_handoff_ready = (
        phase == DEEP_RESEARCH_PHASE_FINAL
        and contract.report_artifact_exists
        and contract.source_ledger_artifact_exists
    )
    return DeepResearchControllerState(
        phase=phase,
        readiness=contract.final_readiness.status,
        report_artifact_exists=contract.report_artifact_exists,
        source_ledger_artifact_exists=contract.source_ledger_artifact_exists,
        child_synthesis_pending=contract.child_synthesis_pending,
        report_required=not contract.report_artifact_exists,
        source_ledger_required=(
            contract.report_artifact_exists
            and not contract.source_ledger_artifact_exists
        ),
        final_handoff_ready=final_handoff_ready,
        next_allowed_tools=allowed_tools,
    )


def _deep_research_phase(contract: ResearchSessionContract) -> str:
    if contract.child_synthesis_pending and not contract.report_artifact_exists:
        return DEEP_RESEARCH_PHASE_WRITE
    if contract.child_synthesis_pending and contract.report_artifact_exists:
        return DEEP_RESEARCH_PHASE_REVIEW
    if not contract.plan_created and contract.evidence.search_calls == 0:
        return DEEP_RESEARCH_PHASE_PLAN
    if contract.evidence.search_calls == 0:
        return DEEP_RESEARCH_PHASE_DISCOVER
    if (
        contract.web_fetch_available
        and not contract.fetch_fallback_required
        and contract.evidence.successful_fetches
        < _evidence_floor(contract.research_depth)[0]
    ):
        return DEEP_RESEARCH_PHASE_VERIFY
    if not contract.report_artifact_exists:
        return DEEP_RESEARCH_PHASE_WRITE
    if not contract.source_ledger_artifact_exists:
        return DEEP_RESEARCH_PHASE_REVIEW
    if contract.final_readiness.status != FINAL_READINESS_ALLOWED:
        return DEEP_RESEARCH_PHASE_REVIEW
    return DEEP_RESEARCH_PHASE_FINAL


__all__ = [
    "FINAL_READINESS_ALLOWED",
    "FINAL_READINESS_BLOCKED_BY_PROVIDER",
    "FINAL_READINESS_REPAIR_NEEDED",
    "DEEP_RESEARCH_PHASE_DISCOVER",
    "DEEP_RESEARCH_PHASE_FINAL",
    "DEEP_RESEARCH_PHASE_PLAN",
    "DEEP_RESEARCH_PHASE_REVIEW",
    "DEEP_RESEARCH_PHASE_VERIFY",
    "DEEP_RESEARCH_PHASE_WRITE",
    "REPAIR_FINAL_MISSING_SOURCE_LINKS",
    "REPAIR_CHILD_SYNTHESIS_PENDING",
    "REPAIR_HARD_CLAIMS_UNSUPPORTED",
    "REPAIR_HARD_CLAIMS_UNVERIFIED",
    "REPAIR_INSUFFICIENT_SOURCE_DIVERSITY",
    "REPAIR_MISSING_FETCHED_SOURCES",
    "REPAIR_MISSING_RESEARCH_EVIDENCE",
    "REPAIR_PARENT_REVIEW_PENDING",
    "REPAIR_UNFINISHED_TODOS",
    "DeepResearchControllerState",
    "ResearchFinalReadiness",
    "ResearchSessionContract",
    "build_research_session_contract",
    "build_research_session_contract_from_context",
    "child_source_ledgers_from_context",
    "deep_research_parent_review_next_tool",
    "deep_research_parent_review_pending",
    "deep_research_post_artifact_next_tool",
    "has_source_links",
    "parent_review_actions_seen",
    "unfinished_todo_labels",
]
