"""Deep-research signal vocabulary + context/gating predicates.

Phase/readiness/tool-set constants plus the leaf predicate functions reading
signals from a run context / task contract / tool state (including parent-review
gating). Split out of ``research_session_contract`` (god-module split,
behaviour-neutral); the contract dataclasses and builders there depend on THESE,
never the reverse.
"""


from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from agent_driver.contracts.context import PlanningState
from agent_driver.contracts.enums import PlanningTodoStatus
from agent_driver.runtime.deep_research_gating import (
    deep_research_tool_result_succeeded,
)
from agent_driver.runtime.metadata_state import (
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
    research_evidence_from_tool_results,
)

if TYPE_CHECKING:
    from agent_driver.runtime.single_agent.types import RunContext


_READ_SOURCE_TOOLS = ("web_fetch", "source_read", "pdf_read", "browser_read")


def _hard_claims_state_from_context(context: RunContext) -> dict[str, Any]:
    """Derive the hard-profile claim-audit gate inputs from run context.

    Enforcement is opt-in: ``hard_options.enforce_claims_audit`` must be set on
    the task contract, so default hard-profile behaviour is unchanged. The
    verified/unsupported counts come from the claims-matrix metadata recorded by
    ``persist_deep_research_claims_matrix``.
    """
    task_contract = _task_contract_from_context(context)
    hard_profile = (
        isinstance(task_contract, dict)
        and task_contract.get("research_profile") == "hard"
    )
    hard_options = (
        task_contract.get("hard_options") if isinstance(task_contract, dict) else None
    )
    enforce = bool(
        isinstance(hard_options, dict) and hard_options.get("enforce_claims_audit")
    )
    artifacts = context.metadata.get("deep_research_artifacts")
    verified = unsupported = 0
    if isinstance(artifacts, dict):
        verified = _coerce_count(artifacts.get("claims_verified_count"))
        unsupported = _coerce_count(artifacts.get("claims_unsupported_count"))
    return {
        "hard_profile": hard_profile,
        "enforce_hard_claims": enforce,
        "claims_verified_count": verified,
        "claims_unsupported_count": unsupported,
    }


def _coerce_count(value: object) -> int:
    try:
        return max(0, int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


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


def _parent_review_steps_done(tool_results: object) -> dict[str, bool]:
    """Cap-aware completion for each parent review step.

    A step is done when its scoped action succeeded OR the model has burned the
    per-step attempt cap on it (denied wrong-path calls included). Shared by the
    pending gate and the next-tool picker so they never disagree (which would
    leave the gate open while the picker has nothing left to force).
    """
    actions = parent_review_actions_seen(tool_results)
    return {
        "read_file": actions["read_file"]
        or _parent_tool_attempt_count(tool_results, _PARENT_READ_TOOLS)
        >= _PARENT_REVIEW_ATTEMPT_CAP,
        "artifact_preview": actions["artifact_preview"]
        or _parent_tool_attempt_count(tool_results, _PARENT_PREVIEW_TOOLS)
        >= _PARENT_REVIEW_ATTEMPT_CAP,
        "file_patch": actions["file_patch"]
        or _parent_tool_attempt_count(tool_results, _PARENT_PATCH_TOOLS)
        >= _PARENT_REVIEW_ATTEMPT_CAP,
    }


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
    steps = _parent_review_steps_done(tool_results)
    review_done = (
        steps["read_file"] and steps["artifact_preview"] and steps["file_patch"]
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
    # Loop-breaker per step: a forced tool_choice can be answered with a denied
    # call (wrong/absolute path), which never flips the corresponding "seen"
    # flag. _parent_review_steps_done treats a step as done after the per-step
    # attempt cap so the run advances instead of spinning to cancellation.
    steps = _parent_review_steps_done(tool_results)
    if not steps["read_file"] and _tool_policy_allows(context, "read_file"):
        return "read_file"
    if not steps["artifact_preview"] and _tool_policy_allows(
        context, "artifact_preview"
    ):
        return "artifact_preview"
    if not steps["file_patch"]:
        for tool_name in ("file_patch", "file_edit"):
            if _tool_policy_allows(context, tool_name):
                return tool_name
    parent_evidence = research_evidence_from_tool_results(tool_results)
    if parent_evidence.fetch_calls < 1 and _tool_policy_allows(context, "web_fetch"):
        return "web_fetch"
    return None


def _tool_result_count(context: RunContext, tool_name: str) -> int:
    count = 0
    for item in get_tool_loop_state(context).tool_results():
        if not isinstance(item, dict):
            continue
        call = item.get("call")
        if isinstance(call, dict) and call.get("tool_name") == tool_name:
            count += 1
    return count


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
    if "workspace_cwd" in context.metadata or isinstance(
        context.metadata.get("deep_research_artifacts"), dict
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
