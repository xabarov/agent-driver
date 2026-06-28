"""Runtime enforcement for :class:`~agent_driver.contracts.node_contract.NodeContract`.

The schema lives in ``contracts/node_contract.py``; this module is the runtime
*behaviour* behind its three opt-in layers. Everything here is a no-op unless the
run's ``AgentRunInput.node_contract`` is active, so other consumers of the library
are unaffected.

* **Layer A** — ``unsatisfiable_tool_names`` diffs the declared ``allowed_tools`` /
  ``finalize_when_tools`` against the live registry so a policy↔registry mismatch
  surfaces as a structured warning instead of an empty result.
* **Layer B** — ``build_prelude`` (proactive, woven into the system prompt by
  :class:`NodeContractLifecycleHook`) plus the reactive finalize guards turn a
  zero-tool-call or missing-required-tool finalize into a recoverable reprompt and
  then a typed violation — never a silent generic reply.
* **Layer C** — ``declarative_finalize_satisfied`` and the ``on_tool_evidence`` hook
  let a run finalize directly from tool evidence with no extra LLM continuation;
  ``build_evidence_answer`` synthesises the terminal answer from the envelopes.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any

from agent_driver.runtime.lifecycle_hooks import BaseRunLifecycleHook
from agent_driver.runtime.metadata_state import get_tool_loop_state

if TYPE_CHECKING:
    from agent_driver.contracts.node_contract import NodeContract
    from agent_driver.contracts.runtime import AgentRunInput
    from agent_driver.llm.contracts import LlmRequest
    from agent_driver.runtime.single_agent.types import RunContext

# --- metadata keys (kept stable; mirrored into AgentRunOutput.metadata) --------
NODE_CONTRACT_PRELUDE_KEY = "node_contract_prelude"
TOOL_POLICY_WARNINGS_KEY = "tool_policy_warnings"
NODE_CONTRACT_VIOLATION_KEY = "node_contract_violation"
TOOL_USE_REPROMPT_COUNT_KEY = "tool_use_contract_reprompt_count"
EARLY_FINALIZE_ANSWER_KEY = "early_finalize_answer"
EARLY_FINALIZE_REASON_KEY = "node_contract_early_finalize_reason"

# Continuation reason tag used by ``_build_continuation_transition``.
_REPROMPT_REASON = "node_contract_tool_use"


def contract_of(run_input: "AgentRunInput") -> "NodeContract":
    """Return the run's node contract (always present; default is inert)."""
    return run_input.node_contract


def is_active(run_input: "AgentRunInput") -> bool:
    """Whether any node-contract layer is engaged for this run."""
    contract = run_input.node_contract
    return contract is not None and contract.is_active()


# --- Layer A: policy ↔ registry validation -------------------------------------
def unsatisfiable_tool_names(
    run_input: "AgentRunInput", callable_names: Iterable[str]
) -> list[str]:
    """Declared tool names (allowlist + finalize set) not callable in the registry.

    Returns ``[]`` unless ``require_callable_tools`` is set. Order-preserving and
    de-duplicated so the warning lists each missing name once.
    """
    contract = run_input.node_contract
    if not contract.require_callable_tools:
        return []
    callable_set = {str(name) for name in callable_names}
    declared: list[str] = []
    declared.extend(run_input.tool_policy.allowed_tools or [])
    declared.extend(contract.finalize_when_tools)
    declared.extend(contract.require_completed_tools)
    return [
        name
        for name in dict.fromkeys(str(item) for item in declared)
        if name and name not in callable_set
    ]


# --- Layer B: proactive prelude ------------------------------------------------
def build_prelude(
    run_input: "AgentRunInput", callable_names: Sequence[str]
) -> str | None:
    """Build the system-prompt addendum that makes tools + target unmissable.

    ``None`` when ``require_tool_use`` is off or no callable tool would surface —
    there is nothing honest to promise the model in that case.
    """
    contract = run_input.node_contract
    if not contract.require_tool_use:
        return None
    names = [str(name) for name in callable_names if str(name)]
    allowed = run_input.tool_policy.allowed_tools
    if allowed:
        allowed_set = {str(name) for name in allowed}
        filtered = [name for name in names if name in allowed_set]
        names = filtered or [str(name) for name in allowed]
    names = list(dict.fromkeys(names))
    if not names:
        return None
    tool_list = ", ".join(names)
    lines = [
        "## Tool-use contract (workflow node)",
        (
            "You are running as a tool-using workflow node with working tools "
            f"available right now: {tool_list}."
        ),
        (
            "Call the appropriate tool(s) to complete the task. Do NOT claim you "
            "lack tools or that you can only provide instructions, and do NOT ask "
            "which target to use — act on the information already given."
        ),
    ]
    if contract.target:
        lines.append(
            f"The target is `{contract.target}`. Use it directly; never ask for it."
        )
    if contract.task_hint:
        lines.append(f"Task: {contract.task_hint}.")
    if contract.require_completed_tools:
        required = ", ".join(str(name) for name in contract.require_completed_tools)
        lines.append(
            "Do not finalize until these required tool(s) have completed successfully: "
            f"{required}."
        )
    return "\n".join(lines)


def inject_system_prelude(request: "LlmRequest", prelude: str) -> "LlmRequest":
    """Return a copy of ``request`` with ``prelude`` woven into the system message."""
    messages = [message.model_copy(deep=True) for message in request.messages]
    for message in messages:
        if str(getattr(message, "role", "")) == "system":
            existing = message.content or ""
            if prelude in existing:
                return request
            message.content = (
                f"{existing.rstrip()}\n\n{prelude}" if existing else prelude
            )
            return request.model_copy(update={"messages": messages})
    # No system message present — prepend one.
    from agent_driver.contracts.enums import ChatRole
    from agent_driver.contracts.messages import ChatMessage

    messages.insert(0, ChatMessage(role=ChatRole.SYSTEM, content=prelude))
    return request.model_copy(update={"messages": messages})


# --- Layer B: reactive reprompt + escalation -----------------------------------
def _is_disallowed_management_denial(item: dict) -> bool:
    """Whether a tool result is a denied out-of-allowlist management call."""
    structured = item.get("structured_output")
    return (
        isinstance(structured, dict)
        and structured.get("error_kind") == "disallowed_management_tool"
    )


def meaningful_tool_call_count(context: "RunContext") -> int:
    """Tool calls that count as real tool-use progress.

    Excludes denied out-of-allowlist *management* calls (``todo_write`` …): a
    model that only emits a disallowed ``todo_write`` has made no progress on the
    node's assigned executable tools, so that denial alone must not satisfy
    ``require_tool_use`` or unlock finalization. A genuine attempt at an allowed
    tool (even if it errors) still counts, preserving prior behaviour.
    """
    return sum(
        1
        for item in get_tool_loop_state(context).tool_results()
        if not _is_disallowed_management_denial(item)
    )


def tool_use_violation_pending(context: "RunContext") -> bool:
    """``require_tool_use`` is on and no meaningful tool call has been made yet."""
    contract = context.run_input.node_contract
    if not contract.require_tool_use:
        return False
    return meaningful_tool_call_count(context) == 0


def reprompt_budget_remaining(context: "RunContext") -> bool:
    """Whether another tool-use reprompt is allowed under ``max_tool_use_reprompts``."""
    contract = context.run_input.node_contract
    used = int(context.metadata.get(TOOL_USE_REPROMPT_COUNT_KEY, 0))
    return used < contract.max_tool_use_reprompts


def build_tool_use_reprompt(run_input: "AgentRunInput") -> str:
    """Nudge text re-stating the contract when the model produced no tool call."""
    contract = run_input.node_contract
    lines = [
        "You have not called any tool, but this node requires tool use and the "
        "tools are available to you now. Call the appropriate tool to perform the "
        "task — do not answer in prose and do not say tools are unavailable.",
    ]
    if contract.target:
        lines.append(f"Target: `{contract.target}` (already known — do not ask).")
    if contract.task_hint:
        lines.append(f"Task: {contract.task_hint}.")
    return " ".join(lines)


def stamp_no_tool_use_violation(context: "RunContext") -> dict:
    """Record a typed ``no_tool_use`` violation on metadata (never a silent answer)."""
    violation = {
        "kind": "no_tool_use",
        "detail": (
            "node contract requires tool use but the run finalized with zero tool "
            "calls after exhausting reprompts"
        ),
        "reprompts": int(context.metadata.get(TOOL_USE_REPROMPT_COUNT_KEY, 0)),
        "max_reprompts": context.run_input.node_contract.max_tool_use_reprompts,
    }
    context.metadata[NODE_CONTRACT_VIOLATION_KEY] = violation
    return violation


def missing_required_tools(context: "RunContext") -> list[str]:
    """Required terminal tools that have not produced a successful envelope."""
    contract = context.run_input.node_contract
    if not contract.require_completed_tools:
        return []
    succeeded = successful_tool_names(context)
    return [
        name
        for name in dict.fromkeys(str(item) for item in contract.require_completed_tools)
        if name and name not in succeeded
    ]


def required_tools_violation_pending(context: "RunContext") -> bool:
    """Whether finalization is blocked by missing required completed tools."""
    return bool(missing_required_tools(context))


def build_required_tools_reprompt(context: "RunContext") -> str:
    """Nudge text requiring the missing terminal tool(s) before finalization."""
    missing = missing_required_tools(context)
    missing_text = ", ".join(missing)
    lines = [
        "This node cannot finalize yet because required tool(s) have not completed "
        f"successfully: {missing_text}. Call the missing required tool(s) now. "
        "Do not answer in prose until the required tool evidence exists.",
    ]
    contract = context.run_input.node_contract
    if contract.target:
        lines.append(f"Target: `{contract.target}` (already known — do not ask).")
    if contract.task_hint:
        lines.append(f"Task: {contract.task_hint}.")
    return " ".join(lines)


def stamp_required_tools_violation(context: "RunContext") -> dict:
    """Record a typed missing-required-tools violation on metadata."""
    missing = missing_required_tools(context)
    violation = {
        "kind": "missing_required_tools",
        "detail": (
            "node contract requires specific completed tools but the run finalized "
            "without successful evidence from all required tools after exhausting reprompts"
        ),
        "missing_tools": missing,
        "reprompts": int(context.metadata.get(TOOL_USE_REPROMPT_COUNT_KEY, 0)),
        "max_reprompts": context.run_input.node_contract.max_tool_use_reprompts,
    }
    context.metadata[NODE_CONTRACT_VIOLATION_KEY] = violation
    return violation


# --- Layer C: early finalize from tool evidence --------------------------------
def successful_tool_names(context: "RunContext") -> set[str]:
    """Tool names that produced a non-error, non-denied envelope this run."""
    names: set[str] = set()
    for item in get_tool_loop_state(context).tool_results():
        call = item.get("call")
        if not isinstance(call, dict):
            continue
        name = call.get("tool_name")
        if not isinstance(name, str) or not name:
            continue
        if item.get("error"):
            continue
        decision = str(item.get("decision") or "").lower()
        if decision in {"deny", "interrupt"}:
            continue
        names.add(name)
    return names


def declarative_finalize_satisfied(context: "RunContext") -> bool:
    """Whether every ``finalize_when_tools`` entry has a successful envelope."""
    contract = context.run_input.node_contract
    if not contract.finalize_when_tools:
        return False
    succeeded = successful_tool_names(context)
    return all(name in succeeded for name in contract.finalize_when_tools)


def executed_tools_summary(context: "RunContext") -> list[dict]:
    """Machine-readable per-call summary of executed tools for downstream consumers."""
    rows: list[dict] = []
    for item in get_tool_loop_state(context).tool_results():
        call = item.get("call")
        if not isinstance(call, dict):
            continue
        error = item.get("error") if isinstance(item.get("error"), dict) else None
        structured = item.get("structured_output")
        rows.append(
            {
                "tool_name": call.get("tool_name"),
                "tool_call_id": call.get("tool_call_id"),
                "status": "failed" if error else "completed",
                "summary": item.get("summary"),
                "structured_output": (
                    structured if isinstance(structured, dict) else None
                ),
                "error_code": error.get("code") if error else None,
            }
        )
    return rows


def _clean_answer_fragment(value: Any, *, max_chars: int = 180) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = " ".join(text.split())
    if len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."
    return text


def _compact_mapping(mapping: dict[str, Any], *, keys: Sequence[str]) -> str:
    parts: list[str] = []
    for key in keys:
        value = mapping.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            text = ", ".join(
                _clean_answer_fragment(item, max_chars=80)
                for item in value[:4]
                if _clean_answer_fragment(item, max_chars=80)
            )
        elif isinstance(value, bool):
            text = str(value)
        else:
            text = _clean_answer_fragment(value, max_chars=120)
        if text:
            parts.append(f"{key}={text}")
        if len(parts) >= 6:
            break
    return ", ".join(parts)


def _compact_structured_finding(finding: Any) -> str:
    if isinstance(finding, dict):
        ftype = _clean_answer_fragment(
            finding.get("type") or finding.get("kind") or finding.get("name"),
            max_chars=60,
        )
        details = _compact_mapping(
            finding,
            keys=(
                "vulnerability_id",
                "template_id",
                "severity",
                "title",
                "package",
                "installed_version",
                "fixed_version",
                "parameter",
                "url",
                "target",
                "host",
                "asset",
                "bucket",
                "exists",
                "provider",
                "public_read",
                "public_write",
                "public_full_control",
                "objects_enumerated",
                "service",
                "status",
                "value",
            ),
        )
        if ftype and details:
            return f"{ftype}: {details}"
        return ftype or details
    return _clean_answer_fragment(finding)


def _compact_structured_output(structured: Any) -> list[str]:
    if not isinstance(structured, dict):
        return []
    parts: list[str] = []
    for key in ("summary", "result_summary", "observation"):
        text = _clean_answer_fragment(structured.get(key), max_chars=240)
        if text:
            parts.append(text)
            break
    findings = structured.get("findings")
    if isinstance(findings, list) and findings:
        compact = [
            item
            for item in (_compact_structured_finding(finding) for finding in findings[:5])
            if item
        ]
        if compact:
            parts.append(f"findings: {'; '.join(compact)}")
    targets = structured.get("targets")
    if isinstance(targets, list) and targets:
        compact_targets = []
        for target in targets[:5]:
            if isinstance(target, dict):
                text = _clean_answer_fragment(
                    target.get("host") or target.get("target") or target.get("value")
                )
            else:
                text = _clean_answer_fragment(target)
            if text:
                compact_targets.append(text)
        if compact_targets:
            parts.append(f"targets: {', '.join(compact_targets)}")
    metrics = structured.get("metrics")
    if isinstance(metrics, dict):
        metric_text = _compact_mapping(
            metrics,
            keys=(
                "findings",
                "targets",
                "issues",
                "count",
                "vulnerable_params",
                "tags_count",
                "hit_count",
                "document_count",
                "buckets_reported",
                "buckets_existing",
                "public_read_buckets",
                "public_write_buckets",
                "public_full_control_buckets",
            ),
        )
        if metric_text:
            parts.append(f"metrics: {metric_text}")
    results = structured.get("results")
    if isinstance(results, list) and results and not any(
        part.startswith("findings:") for part in parts
    ):
        compact_results = [
            text
            for text in (_clean_answer_fragment(item, max_chars=90) for item in results[:5])
            if text
        ]
        if compact_results:
            parts.append(f"results: {', '.join(compact_results)}")
    return parts[:4]


def build_evidence_answer(context: "RunContext") -> str:
    """Synthesise a terminal answer from tool evidence (no model turn needed)."""
    rows = [
        row for row in executed_tools_summary(context) if row["status"] == "completed"
    ]
    if not rows:
        return "Tool execution completed; see the structured tool summary."
    parts: list[str] = ["Tool evidence summary:"]
    for row in rows:
        structured_parts = _compact_structured_output(row.get("structured_output"))
        if structured_parts:
            parts.append(f"- {row['tool_name']}: " + "; ".join(structured_parts))
            continue
        summary = row.get("summary")
        if isinstance(summary, str) and summary.strip():
            parts.append(f"- {row['tool_name']}: {summary.strip()}")
        else:
            parts.append(f"- {row['tool_name']}: completed")
    return "\n".join(parts)


def set_early_finalize(context: "RunContext", *, answer: str, reason: str) -> None:
    """Stash the early-finalize answer + reason so the finalize step prefers them."""
    context.metadata[EARLY_FINALIZE_ANSWER_KEY] = answer
    context.metadata[EARLY_FINALIZE_REASON_KEY] = reason


def early_finalize_answer(context: "RunContext") -> str | None:
    """Return the stashed early-finalize answer, if any."""
    value = context.metadata.get(EARLY_FINALIZE_ANSWER_KEY)
    return value if isinstance(value, str) and value.strip() else None


# --- machine-readable output summary -------------------------------------------
def output_summary(context: "RunContext") -> dict | None:
    """Compact ``node_contract`` block for ``AgentRunOutput.metadata`` (or ``None``).

    Emitted whenever the contract is active OR a node-contract signal was recorded
    (warning / violation / early finalize), so a host always learns the outcome.
    """
    run_input = context.run_input
    warnings = context.metadata.get(TOOL_POLICY_WARNINGS_KEY) or []
    violation = context.metadata.get(NODE_CONTRACT_VIOLATION_KEY)
    early_reason = context.metadata.get(EARLY_FINALIZE_REASON_KEY)
    if (
        not is_active(run_input)
        and not warnings
        and violation is None
        and not early_reason
    ):
        return None
    contract = run_input.node_contract
    return {
        "active": is_active(run_input),
        "require_tool_use": contract.require_tool_use,
        "require_callable_tools": contract.require_callable_tools,
        "require_completed_tools": list(contract.require_completed_tools),
        "finalize_when_tools": list(contract.finalize_when_tools),
        "tool_calls": context.tool_calls,
        "executed_tools": executed_tools_summary(context),
        "tool_policy_warnings": list(warnings) if isinstance(warnings, list) else [],
        "violation": violation if isinstance(violation, dict) else None,
        "early_finalize_reason": (
            early_reason if isinstance(early_reason, str) else None
        ),
        "reprompts": int(context.metadata.get(TOOL_USE_REPROMPT_COUNT_KEY, 0)),
    }


# --- built-in lifecycle hook (proactive prelude injection) ---------------------
class NodeContractLifecycleHook(BaseRunLifecycleHook):
    """Built-in hook that injects the Layer-B prelude into the system prompt.

    Always registered but inert unless ``require_tool_use`` is set and a prelude
    was prepared at run start. Reading tool names off the run-start registry keeps
    the promise honest: the prelude only names tools that will actually surface.
    """

    name = "node_contract"

    async def before_llm_request(
        self, context: "RunContext", request: "LlmRequest"
    ) -> "LlmRequest | None":
        """Weave the prepared tool-use prelude into the request's system message."""
        contract = context.run_input.node_contract
        if not contract.require_tool_use:
            return None
        prelude = context.metadata.get(NODE_CONTRACT_PRELUDE_KEY)
        if not isinstance(prelude, str) or not prelude.strip():
            return None
        if not request.tools:
            # No tools surfaced this turn — promising them would be a lie. Layer A
            # already recorded the policy↔registry mismatch.
            return None
        return inject_system_prelude(request, prelude)


__all__ = [
    "EARLY_FINALIZE_ANSWER_KEY",
    "EARLY_FINALIZE_REASON_KEY",
    "NODE_CONTRACT_PRELUDE_KEY",
    "NODE_CONTRACT_VIOLATION_KEY",
    "NodeContractLifecycleHook",
    "TOOL_POLICY_WARNINGS_KEY",
    "TOOL_USE_REPROMPT_COUNT_KEY",
    "build_evidence_answer",
    "build_prelude",
    "build_required_tools_reprompt",
    "build_tool_use_reprompt",
    "contract_of",
    "declarative_finalize_satisfied",
    "early_finalize_answer",
    "executed_tools_summary",
    "inject_system_prelude",
    "is_active",
    "output_summary",
    "reprompt_budget_remaining",
    "set_early_finalize",
    "stamp_no_tool_use_violation",
    "stamp_required_tools_violation",
    "successful_tool_names",
    "tool_use_violation_pending",
    "required_tools_violation_pending",
    "unsatisfiable_tool_names",
]
