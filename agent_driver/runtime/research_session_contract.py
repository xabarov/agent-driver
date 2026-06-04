"""Research/todo final-readiness contract for chat-style runs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agent_driver.contracts.context import PlanningState
from agent_driver.contracts.enums import PlanningTodoStatus
from agent_driver.runtime.deep_research_gating import (
    deep_research_tool_result_succeeded,
)
from agent_driver.runtime.metadata_state import (
    get_planning_runtime_state,
    get_tool_loop_state,
)
from agent_driver.runtime.research_evidence import (
    DEEP_PARALLEL_DOMAINS,
    DEEP_PARALLEL_FETCHES,
    RESEARCH_DEPTH_DEEP_PARALLEL,
    RESEARCH_DEPTH_LIGHT,
    RESEARCH_DEPTH_NONE,
    RESEARCH_DEPTH_SOURCE_VERIFIED,
    SOURCE_VERIFIED_DOMAINS,
    SOURCE_VERIFIED_FETCHES,
    ResearchEvidenceState,
    ResearchSourceLedger,
    research_evidence_from_tool_results,
    research_source_ledger_from_tool_results,
    rollup_child_source_ledgers,
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
REPAIR_CHILD_SYNTHESIS_PENDING = "child_synthesis_pending"
REPAIR_PARENT_REVIEW_PENDING = "parent_review_pending"

DEEP_RESEARCH_PHASE_PLAN = "plan"
DEEP_RESEARCH_PHASE_DISCOVER = "discover"
DEEP_RESEARCH_PHASE_VERIFY = "verify"
DEEP_RESEARCH_PHASE_WRITE = "write"
DEEP_RESEARCH_PHASE_REVIEW = "review"
DEEP_RESEARCH_PHASE_FINAL = "final"
_READ_SOURCE_TOOLS = ("web_fetch", "source_read", "pdf_read", "browser_read")

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
    if depth in _SUPPORTED_RESEARCH_DEPTHS:
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


def _children_joined(child_source_ledgers: object) -> bool:
    return isinstance(child_source_ledgers, list) and any(
        isinstance(ledger, dict) for ledger in child_source_ledgers
    )


def _result_targets_report(call: dict[str, Any]) -> bool:
    args = call.get("args")
    if not isinstance(args, dict):
        return False
    path = str(args.get("path") or args.get("file_path") or "").strip()
    return path == "research/report.md" or path.endswith("/research/report.md")


# Artifacts whose read counts as the parent's "inspect" step. The report is the
# headline, but reading the source ledger or claims matrix is an equally
# legitimate review action — and crucially the model often reads the ledger
# instead of the report. Scoping the read gate to *any* research artifact (while
# the patch gate still requires the report itself) avoids an infinite forced
# read_file loop when the model keeps re-reading sources.jsonl.
_RESEARCH_ARTIFACT_BASENAMES = (
    "research/report.md",
    "research/sources.jsonl",
    "research/claims.jsonl",
)


def _result_targets_research_artifact(call: dict[str, Any]) -> bool:
    args = call.get("args")
    if not isinstance(args, dict):
        return False
    path = str(args.get("path") or args.get("file_path") or "").strip()
    return any(
        path == base or path.endswith("/" + base)
        for base in _RESEARCH_ARTIFACT_BASENAMES
    )


# Hard cap on forced parent attempts per review step before it is treated as
# satisfied. The forced tool_choice only pins the tool *name*, not its args, so
# the model can burn turns calling read_file / artifact_preview with absolute or
# wrong paths that the workspace sandbox denies. Without a cap those denials spin
# the run until it hits the iteration/token budget and is cancelled.
_PARENT_REVIEW_ATTEMPT_CAP = 3


def _parent_tool_attempt_count(tool_results: object, names: frozenset[str]) -> int:
    """Count parent tool attempts for ``names`` (any path, any status)."""
    if not isinstance(tool_results, list):
        return 0
    count = 0
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        call = item.get("call")
        if isinstance(call, dict) and call.get("tool_name") in names:
            count += 1
    return count


_PARENT_READ_TOOLS = frozenset({"read_file", "artifact_read"})
_PARENT_PREVIEW_TOOLS = frozenset({"artifact_preview"})
_PARENT_PATCH_TOOLS = frozenset({"file_patch", "file_edit"})


def parent_review_actions_seen(tool_results: object) -> dict[str, bool]:
    """Return which parent-owned review actions have succeeded this run.

    The Deep Research parent must do its own verify+review pass on the report
    after child notes are folded in — a child-only run plus the auto-written
    draft stub is not a substitute for the parent reading, previewing, and
    patching its own report. ``read_file``/``file_patch`` are scoped to the
    report path so unrelated file reads/edits do not satisfy the gate.
    """
    seen = {"read_file": False, "artifact_preview": False, "file_patch": False}
    if not isinstance(tool_results, list):
        return seen
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        if not deep_research_tool_result_succeeded(item):
            continue
        call = item.get("call")
        if not isinstance(call, dict):
            continue
        tool_name = call.get("tool_name")
        if tool_name in {"read_file", "artifact_read"} and (
            _result_targets_research_artifact(call)
        ):
            seen["read_file"] = True
        elif tool_name == "artifact_preview":
            seen["artifact_preview"] = True
        elif tool_name in {"file_patch", "file_edit"} and _result_targets_report(call):
            seen["file_patch"] = True
    return seen


def _parent_review_pending(
    *,
    requires_research: bool,
    research_depth: str,
    web_fetch_available: bool,
    parent_evidence: ResearchEvidenceState,
    tool_results: object,
    child_source_ledgers: object,
) -> bool:
    """True when a delegating hard-profile parent still owes a verify+review pass.

    Only applies to deep-parallel (hard) runs where children have joined: once a
    child fetches and the draft stub is auto-written, the run would otherwise
    finalize without the parent ever reading, previewing, patching its report, or
    opening a single source itself. The gate clears once the parent has done all
    three review actions and at least one verify-fetch of its own.
    """
    if not requires_research:
        return False
    if research_depth != RESEARCH_DEPTH_DEEP_PARALLEL:
        return False
    if not _children_joined(child_source_ledgers):
        return False
    actions = parent_review_actions_seen(tool_results)
    review_done = (
        actions["read_file"] and actions["artifact_preview"] and actions["file_patch"]
    )
    # Count a fetch *attempt* (not only a success) as the verify step: a blocked
    # or paywalled fetch still shows the parent tried to verify a source, and
    # gating on success would deadlock the run on inaccessible sources.
    verify_done = (not web_fetch_available) or parent_evidence.fetch_calls >= 1
    return not (review_done and verify_done)


def deep_research_parent_review_pending(context: RunContext) -> bool:
    """Context predicate: does the delegating parent still owe a verify+review pass?

    Used by the request builder so it does not strip the tool surface / force
    ``tool_choice="none"`` the moment the auto-written draft creates both
    artifacts — otherwise the parent can never read, preview, patch, or verify
    its own report.
    """
    task_contract = _task_contract_from_context(context)
    requires_research = (
        isinstance(task_contract, dict)
        and task_contract.get("requires_research") is True
    )
    # Cheap guard before touching loop state so minimal/non-research contexts
    # (and unit-test stubs) short-circuit without requiring full RunContext.
    if not requires_research:
        return False
    tool_results = get_tool_loop_state(context).tool_results()
    return _parent_review_pending(
        requires_research=requires_research,
        research_depth=_research_depth_from_task_contract(task_contract),
        web_fetch_available=any(
            _tool_available(context, tool_name) for tool_name in _READ_SOURCE_TOOLS
        ),
        parent_evidence=research_evidence_from_tool_results(tool_results),
        tool_results=tool_results,
        child_source_ledgers=child_source_ledgers_from_context(context),
    )


def deep_research_parent_review_next_tool(context: RunContext) -> str | None:
    """Pick the next parent-owned verify/review tool to force (request builder).

    Order: the review trio read_file -> artifact_preview -> file_patch first
    (the model reliably executes forced file ops), then a single verify-fetch
    last. Availability is checked at the *policy* level, not the effective set,
    because this surface is exactly what re-opens those tools. Returns ``None``
    when the parent has nothing left to do (or no tool is permitted).
    """
    tool_results = get_tool_loop_state(context).tool_results()
    actions = parent_review_actions_seen(tool_results)
    # Loop-breaker per step: a forced tool_choice can be answered with a denied
    # call (wrong/absolute path), which never flips the corresponding "seen"
    # flag. After _PARENT_REVIEW_ATTEMPT_CAP attempts at a step, treat it as done
    # and advance instead of spinning the run to cancellation.
    read_step_done = actions["read_file"] or (
        _parent_tool_attempt_count(tool_results, _PARENT_READ_TOOLS)
        >= _PARENT_REVIEW_ATTEMPT_CAP
    )
    if not read_step_done and _tool_policy_allows(context, "read_file"):
        return "read_file"
    preview_step_done = actions["artifact_preview"] or (
        _parent_tool_attempt_count(tool_results, _PARENT_PREVIEW_TOOLS)
        >= _PARENT_REVIEW_ATTEMPT_CAP
    )
    if not preview_step_done and _tool_policy_allows(context, "artifact_preview"):
        return "artifact_preview"
    patch_step_done = actions["file_patch"] or (
        _parent_tool_attempt_count(tool_results, _PARENT_PATCH_TOOLS)
        >= _PARENT_REVIEW_ATTEMPT_CAP
    )
    if not patch_step_done:
        for tool_name in ("file_patch", "file_edit"):
            if _tool_policy_allows(context, tool_name):
                return tool_name
    parent_evidence = research_evidence_from_tool_results(tool_results)
    if parent_evidence.fetch_calls < 1 and _tool_policy_allows(context, "web_fetch"):
        return "web_fetch"
    return None


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
    return None


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
    if research_depth not in _SOURCE_VERIFIED_DEPTHS:
        return True
    if not web_fetch_available or fetch_fallback_required:
        return True
    required_fetches, required_domains = _evidence_floor(research_depth)
    return evidence.source_verified(
        required_fetches=required_fetches,
        required_domains=required_domains,
    )


def _evidence_floor(research_depth: str) -> tuple[int, int]:
    """Return (required_fetches, required_domains) for a research depth.

    Deep-parallel (hard) parents clear a higher discovery floor than a single
    source-verified child, because the parent rolls up its children's reads plus
    its own verify-fetches.
    """
    if research_depth == RESEARCH_DEPTH_DEEP_PARALLEL:
        return DEEP_PARALLEL_FETCHES, DEEP_PARALLEL_DOMAINS
    return SOURCE_VERIFIED_FETCHES, SOURCE_VERIFIED_DOMAINS


_SOURCE_VERIFIED_DEPTHS = {RESEARCH_DEPTH_SOURCE_VERIFIED, RESEARCH_DEPTH_DEEP_PARALLEL}
_SUPPORTED_RESEARCH_DEPTHS = {
    RESEARCH_DEPTH_NONE,
    RESEARCH_DEPTH_LIGHT,
    RESEARCH_DEPTH_SOURCE_VERIFIED,
    RESEARCH_DEPTH_DEEP_PARALLEL,
}


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


def _plan_created(planning_state: object) -> bool:
    if not isinstance(planning_state, dict):
        return False
    todos = planning_state.get("todos")
    return isinstance(todos, list) and bool(todos)


def _task_contract_from_context(context: RunContext) -> dict[str, Any] | None:
    run_input = getattr(context, "run_input", None)
    tool_policy = getattr(run_input, "tool_policy", None)
    metadata = getattr(tool_policy, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    task_contract = metadata.get("task_contract")
    return task_contract if isinstance(task_contract, dict) else None


def child_source_ledgers_from_context(context: RunContext) -> list[dict[str, Any]]:
    """Return the joined children's source ledgers for parent roll-up."""
    payload = context.metadata.get("deep_research_child_synthesis")
    if not isinstance(payload, dict):
        return []
    children = payload.get("children")
    if not isinstance(children, list):
        return []
    ledgers: list[dict[str, Any]] = []
    for child in children:
        if not isinstance(child, dict):
            continue
        ledger = child.get("source_ledger")
        if isinstance(ledger, dict):
            ledgers.append(ledger)
    return ledgers


def _child_synthesis_pending(context: RunContext) -> bool:
    payload = context.metadata.get("deep_research_child_synthesis")
    if not isinstance(payload, dict) or payload.get("pending") is not True:
        return False
    return not _deep_research_parent_report_write_seen(context)


def _deep_research_parent_report_write_seen(context: RunContext) -> bool:
    for item in get_tool_loop_state(context).tool_results():
        if not isinstance(item, dict):
            continue
        if not deep_research_tool_result_succeeded(item):
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
            return _report_artifact_confirmed_if_possible(context)
    return False


def _report_artifact_confirmed_if_possible(context: RunContext) -> bool:
    if (
        "workspace_cwd" in context.metadata
        or isinstance(context.metadata.get("deep_research_artifacts"), dict)
    ):
        from agent_driver.runtime.research_artifacts import (
            deep_research_report_artifact_exists,
        )

        return deep_research_report_artifact_exists(context)
    return True


def _tool_available(context: RunContext, tool_name: str) -> bool:
    effective_tool_names = get_tool_loop_state(context).effective_tool_names()
    if effective_tool_names is not None:
        return tool_name in effective_tool_names
    run_input = getattr(context, "run_input", None)
    policy = getattr(run_input, "tool_policy", None)
    if policy is None:
        return False
    denied = getattr(policy, "denied_tools", None) or []
    allowed = getattr(policy, "allowed_tools", None)
    return tool_name not in denied and (allowed is None or tool_name in allowed)


def _tool_policy_allows(context: RunContext, tool_name: str) -> bool:
    """Static-policy availability (ignores the per-request effective set)."""
    run_input = getattr(context, "run_input", None)
    policy = getattr(run_input, "tool_policy", None)
    if policy is None:
        return False
    denied = getattr(policy, "denied_tools", None) or []
    allowed = getattr(policy, "allowed_tools", None)
    return tool_name not in denied and (allowed is None or tool_name in allowed)


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
