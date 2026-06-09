"""Deep-research gating, repair and continuation policy.

Extracted from the generic single-agent step loop so ``lifecycle/steps.py``
stays a lean driver. These helpers decide research contract repair,
tool-choice forcing, parent-synthesis/handoff gating and continuation
transitions; the step mixin calls only :func:`_maybe_build_continuation_transition`
and :func:`_tool_gate_for_context`.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from agent_driver.runtime.deep_research_gating import (
    deep_research_context_enabled,
    deep_research_contract_expected,
    deep_research_max_subagent_requests,
    deep_research_planned_or_started_subagent_count,
    deep_research_profile,
    deep_research_tool_available,
    deep_research_tool_result_succeeded,
    is_research_report_path,
    normalize_artifact_path,
)
from agent_driver.runtime.metadata_state import (
    get_research_runtime_state,
    get_tool_loop_state,
)
from agent_driver.runtime.research_artifacts import (
    captured_draft_protocol_text,
    deep_research_artifact_repair_hint,
    deep_research_report_artifact_exists,
    deep_research_source_ledger_artifact_exists,
    maybe_capture_deep_research_draft,
)
from agent_driver.runtime.research_evidence import (
    SOURCE_VERIFIED_DOMAINS,
    SOURCE_VERIFIED_FETCHES,
    research_evidence_from_tool_results,
    research_source_ledger_from_tool_results,
    rollup_child_source_ledgers,
)
from agent_driver.runtime.research_session_contract import (
    FINAL_READINESS_ALLOWED,
    REPAIR_CHILD_SYNTHESIS_PENDING,
    REPAIR_FINAL_MISSING_SOURCE_LINKS,
    REPAIR_INSUFFICIENT_SOURCE_DIVERSITY,
    REPAIR_MISSING_FETCHED_SOURCES,
    REPAIR_MISSING_RESEARCH_EVIDENCE,
    REPAIR_PARENT_REVIEW_PENDING,
    REPAIR_UNFINISHED_TODOS,
    build_research_session_contract_from_context,
    child_source_ledgers_from_context,
    deep_research_post_artifact_next_tool,
    parent_review_actions_seen,
)
from agent_driver.runtime.single_agent.lifecycle.continuation import (
    analyze_continuation_intent,
)
from agent_driver.runtime.single_agent.types import RunContext, RuntimeStepResult
from agent_driver.runtime.tool_gate import (
    ToolGate,
    ToolGateAllow,
    ToolGateContext,
    ToolGateDeny,
)

_PARENT_SYNTHESIS_CREATE_TOOLS = frozenset({"file_write", "todo_write", "web_fetch"})
_PARENT_SYNTHESIS_UPDATE_TOOLS = frozenset(
    {
        "artifact_preview",
        "artifact_read",
        "file_edit",
        "file_patch",
        "file_write",
        "read_file",
        "todo_write",
    }
)
_DEEP_RESEARCH_PRE_SUBAGENT_BLOCKED_TOOLS = frozenset(
    {
        "artifact_list",
        "artifact_preview",
        "artifact_read",
        "file_edit",
        "file_patch",
        "file_write",
        "glob_search",
        "grep_search",
        "read_file",
        "web_fetch",
        "web_search",
    }
)
_URL_RE = re.compile(r"https?://[^\s\]\)>,;]+")
_PARENT_SYNTHESIS_MAX_VERIFY_FETCHES = 3


def _maybe_build_continuation_transition(
    context: RunContext,
) -> RuntimeStepResult | None:
    """Continue when final text itself says there is a next step."""
    if context.llm_response is None:
        return None
    text = context.llm_response.message.content or ""
    contract = build_research_session_contract_from_context(
        context,
        assistant_text=text,
    )
    research_state = get_research_runtime_state(context)
    research_state.set_contract_payload(contract.model_dump())
    readiness = contract.final_readiness
    if readiness.status != FINAL_READINESS_ALLOWED:
        protocol_text = text
        capture_payload = maybe_capture_deep_research_draft(context, text)
        if capture_payload is not None:
            protocol_text = captured_draft_protocol_text(capture_payload)
        reason_signature = ",".join(readiness.reasons)
        repair_count = research_state.contract_repair_nudge_count()
        previous_signature = research_state.contract_repair_reason_signature()
        # The hard profile gets a larger repair budget and tolerates repeated
        # same-reason nudges: "fetch real sources" legitimately repeats while the
        # model escalates from search to fetch. Other profiles keep the tighter
        # cap so they cannot spin on an unsatisfiable repair reason.
        profile = deep_research_profile(context, default="")
        max_repairs = 4 if profile == "hard" else 2
        strict_repeat = profile != "hard"
        if repair_count >= max_repairs or (
            strict_repeat
            and repair_count >= 1
            and previous_signature == reason_signature
        ):
            research_state.set_repair_exhausted(list(readiness.reasons))
            return None
        from agent_driver.runtime.single_agent.lifecycle.continuation import (
            ContinuationIntent,
        )

        intent = ContinuationIntent(True, "contract_repair_required")
        nudge = _research_contract_repair_nudge(context, readiness.reasons)
        get_tool_loop_state(context).clear_force_final_answer()
        _force_research_repair_tool_choice(context, readiness.reasons)
        research_state.set_contract_repair_reason_signature(reason_signature)
        return _build_continuation_transition(
            context,
            text=protocol_text,
            nudge=nudge,
            reason=intent.reason,
            count_key="contract_repair_nudge_count",
        )
    count = int(context.metadata.get("continuation_nudge_count", 0))
    if count >= 2:
        return None
    intent = analyze_continuation_intent(text)
    if not intent.should_continue:
        return None
    nudge = (
        "Continue with the task. If you were about to proceed to the next "
        "step, do it now instead of only reporting progress. Reply in the "
        "user's language."
    )
    if intent.reason == "text_form_tool_call":
        nudge = (
            "The previous assistant message printed a tool call as text. Do not "
            "print JSON or <tool_call> blocks. If a tool is needed, call it using "
            "native function/tool-calling now; otherwise answer the user directly "
            "in the user's language."
        )
    return _build_continuation_transition(
        context,
        text=text,
        nudge=nudge,
        reason=intent.reason,
        count_key="continuation_nudge_count",
    )


def _build_continuation_transition(
    context: RunContext,
    *,
    text: str,
    nudge: str,
    reason: str,
    count_key: str,
) -> RuntimeStepResult:
    from agent_driver.contracts.enums import ChatRole
    from agent_driver.contracts.messages import ChatMessage

    protocol = context.metadata.get("protocol_messages")
    messages: list[dict[str, object]] = []
    if isinstance(protocol, list):
        messages = [item for item in protocol if isinstance(item, dict)]
    else:
        messages = [
            message.model_dump(mode="json") for message in context.run_input.messages
        ]
        if not messages:
            messages = [
                {"role": ChatRole.USER.value, "content": context.run_input.input or ""}
            ]
    messages.append(
        ChatMessage(role=ChatRole.ASSISTANT, content=text).model_dump(mode="json")
    )
    messages.append(
        ChatMessage(
            role=ChatRole.USER,
            content=nudge,
        ).model_dump(mode="json")
    )
    context.metadata["protocol_messages"] = messages
    context.metadata[count_key] = int(context.metadata.get(count_key, 0)) + 1
    context.metadata["continuation_nudge_reason"] = reason
    return RuntimeStepResult(next_step="llm_call")


def _force_research_repair_tool_choice(
    context: RunContext, reasons: tuple[str, ...]
) -> None:
    """Force the concrete research tool when a model tries to finish too early."""
    if _deep_research_initial_subagent_recovery_required(context):
        if _tool_available_for_repair(context, "agent_tool"):
            get_tool_loop_state(context).set_tool_choice_override(
                {"type": "tool", "name": "agent_tool"}
            )
            context.metadata["deep_research_initial_subagent_recovery"] = {
                "tool": "agent_tool",
                "reason": "contract_repair_before_initial_subagent",
            }
            return
    if REPAIR_CHILD_SYNTHESIS_PENDING in reasons:
        if not _deep_research_parent_report_write_seen(context):
            tool_name = "file_write"
            if deep_research_report_artifact_exists(
                context
            ) and _tool_available_for_repair(context, "file_patch"):
                tool_name = "file_patch"
            if _tool_available_for_repair(context, tool_name):
                get_tool_loop_state(context).set_tool_choice_override(
                    {"type": "tool", "name": tool_name}
                )
                context.metadata["deep_research_parent_synthesis_required"] = {
                    "tool": tool_name,
                    "path": "research/report.md",
                }
                return
        for tool_name in ("artifact_preview", "read_file", "file_patch", "file_edit"):
            if _tool_available_for_repair(context, tool_name):
                get_tool_loop_state(context).set_tool_choice_override(
                    {"type": "tool", "name": tool_name}
                )
                context.metadata["deep_research_parent_synthesis_required"] = {
                    "tool": tool_name,
                    "path": "research/report.md",
                }
                return
    if REPAIR_PARENT_REVIEW_PENDING in reasons:
        tool_name = _deep_research_parent_review_next_tool(context)
        if tool_name is not None:
            get_tool_loop_state(context).set_tool_choice_override(
                {"type": "tool", "name": tool_name}
            )
            context.metadata["deep_research_parent_review_required"] = {
                "tool": tool_name,
                "path": "research/report.md",
            }
            return
    if REPAIR_MISSING_RESEARCH_EVIDENCE in reasons:
        # Delegated search-spam: a child collected search candidates but never
        # opened a page. More searching will not help — force the parent to fetch
        # one of the candidate URLs instead of running another web_search.
        if _deep_research_child_searched_without_fetch(context) and (
            _tool_available_for_repair(context, "web_fetch")
        ):
            get_tool_loop_state(context).set_tool_choice_override(
                {"type": "tool", "name": "web_fetch"}
            )
            context.metadata["continuation_nudge_reason"] = (
                "child_search_without_fetch_repair"
            )
            return
        if _tool_available_for_repair(context, "web_search"):
            get_tool_loop_state(context).set_tool_choice_override(
                {"type": "tool", "name": "web_search"}
            )
            return
    if REPAIR_INSUFFICIENT_SOURCE_DIVERSITY in reasons:
        if _tool_available_for_repair(context, "web_search"):
            get_tool_loop_state(context).set_tool_choice_override(
                {"type": "tool", "name": "web_search"}
            )
            return
    if REPAIR_MISSING_FETCHED_SOURCES in reasons:
        if _tool_available_for_repair(context, "web_fetch"):
            get_tool_loop_state(context).set_tool_choice_override(
                {"type": "tool", "name": "web_fetch"}
            )
            return
    if REPAIR_UNFINISHED_TODOS in reasons:
        if deep_research_report_artifact_exists(context) and _tool_available_for_repair(
            context, "todo_write"
        ):
            get_tool_loop_state(context).set_tool_choice_override(
                {"type": "tool", "name": "todo_write"}
            )
            return
        if _research_evidence_ready_for_final_repair(context):
            get_tool_loop_state(context).force_final_answer(
                reason="contract_repair_final_answer"
            )
            return
        if _tool_available_for_repair(context, "todo_write"):
            get_tool_loop_state(context).set_tool_choice_override(
                {"type": "tool", "name": "todo_write"}
            )


def _deep_research_parent_review_next_tool(context: RunContext) -> str | None:
    """Pick the next parent-owned verify/review tool to force.

    Order: a verify-fetch first (gives the parent its own cited source + domain),
    then the review trio read_file -> artifact_preview -> file_patch. Each forced
    choice only seeds the next turn; the model is free to do the rest in one go.
    """
    tool_results = get_tool_loop_state(context).tool_results()
    parent_evidence = research_evidence_from_tool_results(tool_results)
    if parent_evidence.successful_fetches < 1 and _tool_available_for_repair(
        context, "web_fetch"
    ):
        return "web_fetch"
    actions = parent_review_actions_seen(tool_results)
    if not actions["read_file"] and _tool_available_for_repair(context, "read_file"):
        return "read_file"
    if not actions["artifact_preview"] and _tool_available_for_repair(
        context, "artifact_preview"
    ):
        return "artifact_preview"
    if not actions["file_patch"]:
        for tool_name in ("file_patch", "file_edit"):
            if _tool_available_for_repair(context, tool_name):
                return tool_name
    return None


def _deep_research_initial_subagent_recovery_required(context: RunContext) -> bool:
    if not _deep_research_requires_initial_subagent_gate(context):
        return False
    recovery = context.metadata.get("deep_research_initial_subagent_recovery")
    return isinstance(recovery, dict)


def _tool_available_for_repair(context: RunContext, tool_name: str) -> bool:
    return deep_research_tool_available(context, tool_name)


def _tool_gate_for_context(context: RunContext) -> ToolGate | None:
    """Wrap caller gate with Deep Research parent-synthesis enforcement."""
    existing_gate = context.tool_gate
    terminal_handoff_ready = _deep_research_terminal_handoff_ready(context)
    artifact_repair_required = _deep_research_artifact_repair_required(context)
    if (
        not _deep_research_child_synthesis_pending_without_report(context)
        and not _deep_research_requires_initial_subagent_gate(context)
        and not terminal_handoff_ready
        and not artifact_repair_required
    ):
        return existing_gate

    async def _gate(gate_context: ToolGateContext):
        if _deep_research_terminal_handoff_ready(context):
            payload = {
                "blocked_tool": gate_context.tool_name,
                "allowed_tools": [],
                "reason": "artifacts_ready_for_final_handoff",
            }
            context.metadata["deep_research_terminal_handoff_gate"] = payload
            return ToolGateDeny(
                reason=(
                    "deep_research_terminal_handoff_gate denied "
                    f"{gate_context.tool_name!r}: research/report.md and "
                    "research/sources.jsonl already exist. Finish with a concise "
                    "artifact handoff instead of calling another tool."
                )
            )
        if _deep_research_artifact_repair_required(context):
            if _deep_research_artifact_repair_tool_allowed(
                context,
                gate_context.tool_name,
                gate_context.args,
            ):
                if existing_gate is not None:
                    return await existing_gate(gate_context)
                return ToolGateAllow(reason="deep_research_artifact_repair_gate")
            payload = {
                "blocked_tool": gate_context.tool_name,
                "allowed_tools": ["file_write"],
                "reason": "report_or_ledger_missing",
                "required_path": _deep_research_required_artifact_repair_path(context),
            }
            context.metadata["deep_research_artifact_repair_gate"] = payload
            return ToolGateDeny(
                reason=(
                    "deep_research_artifact_repair_gate denied "
                    f"{gate_context.tool_name!r}: Deep Research has exactly one "
                    "required artifact. Write the missing research/report.md or "
                    "research/sources.jsonl before any other tool."
                )
            )
        if _deep_research_child_synthesis_pending_without_report(context):
            if _deep_research_parent_synthesis_tool_allowed(
                context,
                gate_context.tool_name,
                gate_context.args,
            ):
                if existing_gate is not None:
                    return await existing_gate(gate_context)
                return ToolGateAllow(reason="deep_research_parent_synthesis_gate")
            payload = {
                "blocked_tool": gate_context.tool_name,
                "allowed_tools": sorted(
                    _deep_research_parent_synthesis_allowed_tools(context)
                ),
                "reason": "child_synthesis_pending",
            }
            context.metadata["deep_research_parent_synthesis_gate"] = payload
            return ToolGateDeny(
                reason=(
                    "deep_research_parent_synthesis_gate denied "
                    f"{gate_context.tool_name!r}: joined child research notes "
                    "are pending parent synthesis. Launch the remaining bounded "
                    "agent_tool child if the child budget is not exhausted; "
                    "otherwise create or update research/report.md and "
                    "research/sources.jsonl before continuing discovery."
                )
            )
        if _deep_research_requires_initial_subagent_gate(context):
            blocked_tools = _DEEP_RESEARCH_PRE_SUBAGENT_BLOCKED_TOOLS
            if isinstance(
                context.metadata.get("deep_research_initial_subagent_recovery"),
                dict,
            ):
                blocked_tools = blocked_tools | frozenset({"skill_tool", "skill_view"})
            if gate_context.tool_name in blocked_tools:
                payload = {
                    "blocked_tool": gate_context.tool_name,
                    "allowed_tools": [
                        "agent_tool",
                        "skill_tool",
                        "skill_view",
                        "todo_write",
                    ],
                    "reason": "medium_hard_requires_bounded_subagents",
                }
                context.metadata["deep_research_initial_subagent_gate"] = payload
                return ToolGateDeny(
                    reason=(
                        "deep_research_initial_subagent_gate denied "
                        f"{gate_context.tool_name!r}: medium/hard Deep Research "
                        "must first delegate bounded source discovery with "
                        "agent_tool before direct web or write tools."
                    )
                )
        if existing_gate is not None:
            return await existing_gate(gate_context)
        return ToolGateAllow(reason="deep_research_parent_synthesis_gate")

    return _gate


def _deep_research_requires_initial_subagent_gate(context: RunContext) -> bool:
    task_contract = context.run_input.tool_policy.metadata.get("task_contract")
    if isinstance(task_contract, dict):
        deep_expected = deep_research_contract_expected(task_contract)
    else:
        deep_expected = deep_research_context_enabled(context)
    if not deep_expected:
        if not _deep_research_initial_todo_only_without_child(context):
            return False
    profile = deep_research_profile(context, default="")
    if profile not in {
        "medium",
        "hard",
    } and not _deep_research_initial_todo_only_without_child(context):
        return False
    if deep_research_max_subagent_requests(context) <= 0:
        return False
    if deep_research_planned_or_started_subagent_count(context) > 0:
        return False
    return _tool_available_for_repair(context, "agent_tool")


def _deep_research_initial_todo_only_without_child(context: RunContext) -> bool:
    counts: dict[str, int] = {}
    rows = list(get_tool_loop_state(context).tool_results())
    metadata_rows = context.metadata.get("tool_results")
    if isinstance(metadata_rows, list):
        rows.extend(item for item in metadata_rows if isinstance(item, dict))
    for item in rows:
        if not isinstance(item, dict):
            continue
        call = item.get("call")
        if not isinstance(call, dict):
            continue
        tool_name = str(call.get("tool_name") or "").strip()
        if tool_name:
            counts[tool_name] = counts.get(tool_name, 0) + 1
    if counts.get("todo_write", 0) <= 0:
        return False
    if counts.get("agent_tool", 0) > 0:
        return False
    return _tool_available_for_repair(context, "agent_tool")


def _deep_research_terminal_handoff_ready(context: RunContext) -> bool:
    if (
        deep_research_context_enabled(context)
        and deep_research_report_artifact_exists(context)
        and deep_research_source_ledger_artifact_exists(context)
    ):
        # The artifacts exist, but the run is only ready for terminal handoff once
        # there is no remaining tool-driven repair work: the parent's verify+review
        # pass (read_file / artifact_preview / file_patch + a verify-fetch) AND the
        # rolled-up discovery floor (fetched sources / distinct domains). While any
        # of those need a tool, the gate must let it through instead of clamping to
        # a final handoff.
        return deep_research_post_artifact_next_tool(context) is None
    return False


def _deep_research_artifact_repair_required(context: RunContext) -> bool:
    if not deep_research_context_enabled(context):
        return False
    report_exists = deep_research_report_artifact_exists(context)
    ledger_exists = deep_research_source_ledger_artifact_exists(context)
    return report_exists != ledger_exists


def _deep_research_artifact_repair_tool_allowed(
    context: RunContext,
    tool_name: str,
    args: object | None,
) -> bool:
    if tool_name != "file_write":
        return False
    required_path = _deep_research_required_artifact_repair_path(context)
    if required_path is None:
        return False
    path = normalize_artifact_path(
        _dict_value(args, "path") or _dict_value(args, "file_path")
    )
    return path == required_path


def _deep_research_required_artifact_repair_path(context: RunContext) -> str | None:
    report_exists = deep_research_report_artifact_exists(context)
    ledger_exists = deep_research_source_ledger_artifact_exists(context)
    if report_exists and not ledger_exists:
        return "research/sources.jsonl"
    if ledger_exists and not report_exists:
        return "research/report.md"
    return None


def _deep_research_child_synthesis_pending_without_report(context: RunContext) -> bool:
    handoff = context.metadata.get("deep_research_child_synthesis")
    return (
        isinstance(handoff, dict)
        and handoff.get("pending") is True
        and not _deep_research_parent_report_write_seen(context)
    )


def _deep_research_child_searched_without_fetch(context: RunContext) -> bool:
    """Return True when joined children gathered candidates but never fetched.

    This is the search-spam failure mode: a subagent runs many web_search calls
    but opens zero pages, so the parent ledger has no verified reads. The repair
    path should force a fetch rather than yet another search.
    """
    handoff = context.metadata.get("deep_research_child_synthesis")
    if not isinstance(handoff, dict):
        return False
    children = handoff.get("children")
    if not isinstance(children, list):
        return False
    searched = 0
    fetched = 0
    for child in children:
        if not isinstance(child, dict):
            continue
        ledger = child.get("source_ledger")
        if not isinstance(ledger, dict):
            continue
        candidates = ledger.get("search_candidates")
        if isinstance(candidates, list):
            searched += len(candidates)
        for section in ("verified_reads", "blocked_reads", "failed_reads"):
            rows = ledger.get(section)
            if isinstance(rows, list):
                fetched += len(rows)
    return searched > 0 and fetched == 0


def _deep_research_parent_synthesis_tool_allowed(
    context: RunContext,
    tool_name: str,
    args: object | None = None,
) -> bool:
    if tool_name == "web_fetch":
        return _deep_research_parent_verify_fetch_allowed(context, args)
    return tool_name in _deep_research_parent_synthesis_allowed_tools(context)


def _deep_research_parent_synthesis_allowed_tools(
    context: RunContext,
) -> frozenset[str]:
    allowed = set(
        _PARENT_SYNTHESIS_UPDATE_TOOLS
        if deep_research_report_artifact_exists(context)
        else _PARENT_SYNTHESIS_CREATE_TOOLS
    )
    return frozenset(allowed)


def _deep_research_parent_verify_fetch_allowed(
    context: RunContext,
    args: object | None,
) -> bool:
    if "web_fetch" not in _deep_research_parent_synthesis_allowed_tools(context):
        return False
    if (
        _deep_research_parent_fetch_count(context)
        >= _PARENT_SYNTHESIS_MAX_VERIFY_FETCHES
    ):
        return False
    url = _canonical_url(_dict_value(args, "url"))
    if url is None:
        return False
    return url in (
        _deep_research_child_candidate_urls(context)
        | _deep_research_parent_search_candidate_urls(context)
    )


def _deep_research_parent_fetch_count(context: RunContext) -> int:
    count = 0
    for item in get_tool_loop_state(context).tool_results():
        if not isinstance(item, dict):
            continue
        call = item.get("call")
        if isinstance(call, dict) and call.get("tool_name") == "web_fetch":
            count += 1
    return count


def _deep_research_parent_search_count(context: RunContext) -> int:
    count = 0
    for item in get_tool_loop_state(context).tool_results():
        if not isinstance(item, dict):
            continue
        call = item.get("call")
        if isinstance(call, dict) and call.get("tool_name") == "web_search":
            count += 1
    return count


def _deep_research_child_candidate_urls(context: RunContext) -> frozenset[str]:
    urls: set[str] = set()
    handoff = context.metadata.get("deep_research_child_synthesis")
    if not isinstance(handoff, dict):
        return frozenset()
    _collect_urls_from_text(urls, str(handoff.get("summary") or ""))
    children = handoff.get("children")
    if isinstance(children, list):
        for child in children:
            if not isinstance(child, dict):
                continue
            _collect_urls_from_text(urls, str(child.get("summary") or ""))
            source_ledger = child.get("source_ledger")
            if isinstance(source_ledger, dict):
                _collect_urls_from_source_ledger(urls, source_ledger)
    source_ledger = handoff.get("source_ledger")
    if isinstance(source_ledger, dict):
        _collect_urls_from_source_ledger(urls, source_ledger)
    return frozenset(urls)


def _deep_research_parent_search_candidate_urls(context: RunContext) -> frozenset[str]:
    urls: set[str] = set()
    for item in get_tool_loop_state(context).tool_results():
        if not isinstance(item, dict):
            continue
        call = item.get("call")
        if not isinstance(call, dict) or call.get("tool_name") != "web_search":
            continue
        structured = item.get("structured_output")
        if not isinstance(structured, dict):
            continue
        results = structured.get("results")
        if not isinstance(results, list):
            continue
        for result in results:
            if not isinstance(result, dict):
                continue
            canonical = _canonical_url(result.get("url"))
            if canonical is not None:
                urls.add(canonical)
    return frozenset(urls)


def _collect_urls_from_source_ledger(
    urls: set[str], source_ledger: dict[str, object]
) -> None:
    for section in (
        "search_candidates",
        "verified_reads",
        "blocked_reads",
        "failed_reads",
    ):
        rows = source_ledger.get(section)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            canonical = _canonical_url(row.get("url") or row.get("canonical_url"))
            if canonical is not None:
                urls.add(canonical)


def _collect_urls_from_text(urls: set[str], text: str) -> None:
    for match in _URL_RE.finditer(text):
        canonical = _canonical_url(match.group(0).rstrip(".,;:"))
        if canonical is not None:
            urls.add(canonical)


def _dict_value(value: object | None, key: str) -> object | None:
    return value.get(key) if isinstance(value, dict) else None


def _canonical_url(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    hostname = parsed.hostname
    if not hostname:
        return None
    netloc = hostname.lower()
    if parsed.port and not (
        (parsed.scheme.lower() == "http" and parsed.port == 80)
        or (parsed.scheme.lower() == "https" and parsed.port == 443)
    ):
        netloc = f"{netloc}:{parsed.port}"
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, ""))


def _deep_research_subagent_budget_remaining(context: RunContext) -> bool:
    return deep_research_planned_or_started_subagent_count(
        context
    ) < deep_research_max_subagent_requests(context)


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
        if is_research_report_path(args.get("path") or args.get("file_path")):
            return _report_artifact_confirmed_if_possible(context)
    return False


def _report_artifact_confirmed_if_possible(context: RunContext) -> bool:
    if "workspace_cwd" in context.metadata or isinstance(
        context.metadata.get("deep_research_artifacts"), dict
    ):
        return deep_research_report_artifact_exists(context)
    return True


def _research_evidence_ready_for_final_repair(context: RunContext) -> bool:
    task_contract = context.run_input.tool_policy.metadata.get("task_contract")
    if not isinstance(task_contract, dict):
        return False
    if task_contract.get("requires_research") is not True:
        return False
    evidence = research_evidence_from_tool_results(
        get_tool_loop_state(context).tool_results()
    )
    if task_contract.get("research_depth") == "source_verified_report":
        if not _tool_available_for_repair(context, "web_fetch"):
            return evidence.search_calls > 0 or evidence.fetch_calls > 0
        if evidence.failed_fetches >= SOURCE_VERIFIED_FETCHES and (
            evidence.search_calls > 0 or evidence.fetch_calls > 0
        ):
            return True
        return evidence.source_verified(
            required_fetches=SOURCE_VERIFIED_FETCHES,
            required_domains=SOURCE_VERIFIED_DOMAINS,
        )
    return evidence.search_calls > 0 or evidence.fetch_calls > 0


def _domain_of(url: str) -> str:
    try:
        return urlsplit(url).netloc.lower()
    except ValueError:
        return ""


def _deep_research_diversity_targets(
    context: RunContext,
) -> tuple[list[str], list[str]]:
    """Return (fresh_candidate_urls, blocked_domains) for a diversity top-up.

    Builds the rolled-up source ledger (parent fetches + joined child reads) and
    splits its candidate URLs into ones whose domain has *not* yet been fetched
    or blocked (worth trying for a fresh domain) versus the domains that already
    failed/blocked (worth warning the model away from re-fetching). Without this
    the forced diversity-fetch keeps re-picking paywalled domains and never
    clears the distinct-domain floor.
    """
    tool_results = get_tool_loop_state(context).tool_results()
    merged_ledger, _ = rollup_child_source_ledgers(
        research_source_ledger_from_tool_results(tool_results),
        research_evidence_from_tool_results(tool_results),
        child_source_ledgers_from_context(context),
    )
    verified_domains = {
        _domain_of(str(row.get("url") or "")) for row in merged_ledger.verified_reads
    }
    blocked_domains = {
        _domain_of(str(row.get("url") or ""))
        for row in (*merged_ledger.blocked_reads, *merged_ledger.failed_reads)
    }
    blocked_domains.discard("")
    seen_targets: set[str] = set()
    fresh_candidate_urls: list[str] = []
    for row in merged_ledger.search_candidates:
        url = str(row.get("url") or "").strip()
        if not url:
            continue
        domain = _domain_of(url)
        if not domain or domain in verified_domains or domain in blocked_domains:
            continue
        if domain in seen_targets:
            continue
        seen_targets.add(domain)
        fresh_candidate_urls.append(url)
    return fresh_candidate_urls[:4], sorted(blocked_domains)


def _research_contract_repair_nudge(
    context: RunContext, reasons: tuple[str, ...]
) -> str:
    """Return a compact one-shot repair instruction for contract violations."""
    fragments: list[str] = []
    if REPAIR_CHILD_SYNTHESIS_PENDING in reasons:
        fragments.append(
            "joined child research notes are waiting for parent-owned synthesis; "
            "do not search again or spawn another child; write or patch "
            "research/report.md and research/sources.jsonl from the child notes"
        )
    if REPAIR_UNFINISHED_TODOS in reasons:
        fragments.append(
            "the visible todo/checklist still has pending or in-progress items"
        )
    if REPAIR_MISSING_RESEARCH_EVIDENCE in reasons:
        fragments.append("the user requested research but no web evidence was used")
    if REPAIR_MISSING_FETCHED_SOURCES in reasons:
        fragments.append(
            "source-verified work needs fetched/read pages, not search results only"
        )
    if REPAIR_PARENT_REVIEW_PENDING in reasons:
        fragments.append(
            "child research has been folded in, but the parent still owes its own "
            "verify+review pass: open at least one source yourself with web_fetch, "
            "then read_file research/report.md, artifact_preview it, and file_patch a "
            "targeted correction (do not rewrite the whole report)"
        )
    if _deep_research_child_searched_without_fetch(context):
        fetch_fragment = (
            "a child gathered search candidates but never opened a page; do not "
            "search again — open at least one concrete candidate URL with "
            "web_fetch (or source_read) and cite the fetched source"
        )
        candidate_urls = list(_deep_research_child_candidate_urls(context))[:5]
        if candidate_urls:
            fetch_fragment += "; candidate URLs to fetch: " + ", ".join(candidate_urls)
        fragments.append(fetch_fragment)
    if REPAIR_INSUFFICIENT_SOURCE_DIVERSITY in reasons:
        evidence = research_evidence_from_tool_results(
            get_tool_loop_state(context).tool_results()
        )
        domains = ", ".join(evidence.unique_domains)
        message = "the fetched evidence needs more distinct source domains"
        if domains:
            message += f"; already verified domain(s): {domains}"
        fresh_urls, blocked_domains = _deep_research_diversity_targets(context)
        if blocked_domains:
            message += (
                "; do NOT re-fetch these already-blocked/paywalled domains: "
                + ", ".join(blocked_domains)
            )
        if fresh_urls:
            message += (
                "; fetch one of these untried candidate URLs on a NEW domain: "
                + ", ".join(fresh_urls)
            )
        else:
            message += (
                "; run web_search for an open-access source (e.g. an arXiv/PDF "
                "or university host) on a domain you have not fetched, then "
                "web_fetch it"
            )
        fragments.append(message)
    if REPAIR_FINAL_MISSING_SOURCE_LINKS in reasons:
        fragments.append("the final answer must include visible source links")
    artifact_hint = deep_research_artifact_repair_hint(context)
    if artifact_hint:
        fragments.append(artifact_hint)
    if REPAIR_CHILD_SYNTHESIS_PENDING in reasons:
        handoff = context.metadata.get("deep_research_child_synthesis")
        if isinstance(handoff, dict):
            summary = str(handoff.get("summary") or "").strip()
            if summary:
                fragments.append(f"child notes preview: {summary[:1200]}")
                fragments.append(
                    "use this embedded preview directly; do not read child "
                    "transcript or skill files by absolute path"
                )
    reason_text = (
        "; ".join(fragments) if fragments else "the run contract is incomplete"
    )
    return (
        "Contract repair required before the final answer: "
        f"{reason_text}. Continue now using only the real available tools "
        "(todo_write, web_search, web_fetch, read_file, artifact_preview, "
        "file_write, file_patch when available). "
        "Update the visible todo state when a step is done, cite fetched URLs in "
        "the final response, and reply in the user's language."
    )
