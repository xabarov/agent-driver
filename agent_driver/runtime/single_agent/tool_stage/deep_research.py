"""Deep-research tool-stage coercion, repair, and artifact helpers.

Extracted from ``tool_stage/__init__.py`` (behavior-preserving): all the
deep-research-specific suppression / clamping / coercion / repair / artifact
helpers that the tool-stage orchestrator applies before/around tool execution.
Depends only on contracts + research modules (never back on the orchestrator),
so the import is one-directional: ``__init__`` imports these, not vice-versa.
"""

from __future__ import annotations

import json
import re
from typing import Any

from agent_driver.llm.contracts import LlmFinishReason
from agent_driver.llm.tool_call_parser import strip_text_form_tool_calls
from agent_driver.runtime.deep_research_gating import (
    deep_research_medium_or_hard,
    deep_research_planned_or_started_subagent_count,
    deep_research_tool_available,
    deep_research_tool_result_succeeded,
    is_research_report_path,
    is_research_source_ledger_path,
    normalize_artifact_path,
)
from agent_driver.runtime.metadata_state import get_tool_loop_state
from agent_driver.runtime.research_artifacts import (
    deep_research_report_artifact_exists,
    deep_research_source_ledger_artifact_exists,
)
from agent_driver.runtime.research_evidence import (
    research_source_ledger_from_tool_results,
)
from agent_driver.runtime.research_session_contract import (
    deep_research_post_artifact_next_tool,
)
from agent_driver.runtime.single_agent.types import RunContext
from agent_driver.tools.executor.planned import extract_planned_tool_calls

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


def _suppress_deep_research_terminal_tool_calls(context: RunContext) -> None:
    """Treat post-artifact tool drift as final text once both research artifacts exist."""
    if not _deep_research_terminal_artifacts_seen(context):
        return
    # Do not suppress while the delegating parent still owes tool-driven work:
    # its own verify+review pass (read/preview/patch + a verify-fetch) AND topping
    # up the rolled-up fetch/domain floor. The auto-written draft creates both
    # artifacts, but those forced tool calls are legitimate, not post-terminal
    # drift.
    if deep_research_post_artifact_next_tool(context) is not None:
        return
    response = context.llm_response
    if response is None:
        return
    planned_calls = extract_planned_tool_calls(response)
    if not planned_calls:
        return
    content = response.message.content or ""
    response.message.content = strip_text_form_tool_calls(content)
    response.finish_reason = LlmFinishReason.STOP
    response.metadata["planned_tool_calls"] = []
    context.metadata["deep_research_terminal_tool_calls_suppressed"] = {
        "count": len(planned_calls),
        "tools": [call.tool_name for call in planned_calls],
        "reason": "artifacts_ready",
    }


def _clamp_deep_research_parent_artifact_batch(context: RunContext) -> None:
    """Drop sibling tool drift when the parent is writing research artifacts."""
    handoff = context.metadata.get("deep_research_child_synthesis")
    if not (isinstance(handoff, dict) and handoff.get("pending") is True):
        return
    response = context.llm_response
    if response is None:
        return
    planned_calls = extract_planned_tool_calls(response)
    if not planned_calls or not any(
        call.tool_name == "file_write" for call in planned_calls
    ):
        return
    kept = [call for call in planned_calls if call.tool_name == "file_write"]
    if len(kept) == len(planned_calls):
        return
    response.metadata["planned_tool_calls"] = [
        call.model_dump(mode="json") for call in kept
    ]
    context.metadata["deep_research_parent_artifact_batch_clamped"] = {
        "kept": len(kept),
        "dropped": len(planned_calls) - len(kept),
        "reason": "parent_artifact_writes_only",
        "dropped_tools": [
            call.tool_name for call in planned_calls if call.tool_name != "file_write"
        ],
    }


def _coerce_deep_research_artifact_repair_batch(context: RunContext) -> None:
    """Rewrite artifact-repair drift to the single missing artifact write."""
    report_exists = deep_research_report_artifact_exists(
        context
    ) or _deep_research_report_seen_in_tool_results(context)
    ledger_exists = deep_research_source_ledger_artifact_exists(
        context
    ) or _deep_research_ledger_seen_in_tool_results(context)
    if report_exists == ledger_exists:
        return
    response = context.llm_response
    if response is None:
        return
    planned_calls = extract_planned_tool_calls(response)
    if not planned_calls:
        return
    target = "sources" if report_exists else "report"
    first = planned_calls[0]
    repaired = first.model_copy(
        update={
            "tool_name": "file_write",
            "args": _deep_research_parent_file_write_args(context, target=target),
            "metadata": {
                **first.metadata,
                "deep_research_args_repaired": True,
                "deep_research_repair_reason": "artifact_repair_tool_coerced",
                "original_tool_name": first.tool_name,
            },
        }
    )
    response.metadata["planned_tool_calls"] = [repaired.model_dump(mode="json")]
    context.metadata["deep_research_artifact_repair_batch_coerced"] = {
        "target": (
            "research/sources.jsonl" if target == "sources" else "research/report.md"
        ),
        "dropped": max(0, len(planned_calls) - 1),
        "original_tool": first.tool_name,
    }


def _coerce_deep_research_parent_synthesis_write(context: RunContext) -> None:
    """Convert post-child tool drift into the required parent report write."""
    handoff = context.metadata.get("deep_research_child_synthesis")
    if not isinstance(handoff, dict) or handoff.get("pending") is not True:
        return
    if deep_research_report_artifact_exists(context) or _report_artifact_path_seen(
        context
    ):
        return
    response = context.llm_response
    if response is None:
        return
    planned_calls = extract_planned_tool_calls(response)
    if not planned_calls:
        return
    if any(
        call.tool_name in {"file_write", "write", "web_fetch"} for call in planned_calls
    ):
        return
    first = planned_calls[0]
    repaired = first.model_copy(
        update={
            "tool_name": "file_write",
            "args": _deep_research_parent_file_write_args(context, target="report"),
            "metadata": {
                **first.metadata,
                "deep_research_args_repaired": True,
                "deep_research_repair_reason": "parent_synthesis_tool_coerced",
                "original_tool_name": first.tool_name,
            },
        }
    )
    response.metadata["planned_tool_calls"] = [repaired.model_dump(mode="json")]
    context.metadata["deep_research_parent_synthesis_tool_coerced"] = {
        "original_tool": first.tool_name,
        "dropped": max(0, len(planned_calls) - 1),
        "target": "research/report.md",
    }


def _deep_research_terminal_artifacts_seen(context: RunContext) -> bool:
    return (
        deep_research_report_artifact_exists(context)
        and deep_research_source_ledger_artifact_exists(context)
    ) or (
        _deep_research_report_seen_in_tool_results(context)
        and _deep_research_ledger_seen_in_tool_results(context)
    )


def _deep_research_report_seen_in_tool_results(context: RunContext) -> bool:
    return _deep_research_artifact_seen_in_tool_results(context, "report")


def _deep_research_ledger_seen_in_tool_results(context: RunContext) -> bool:
    return _deep_research_artifact_seen_in_tool_results(context, "sources")


def _deep_research_artifact_seen_in_tool_results(
    context: RunContext, target: str
) -> bool:
    paths = {"report": "research/report.md", "sources": "research/sources.jsonl"}
    expected = paths[target]
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
        if call.get("tool_name") not in {"file_write", "file_patch", "file_edit"}:
            continue
        args = call.get("args")
        if not isinstance(args, dict):
            continue
        path = normalize_artifact_path(args.get("path") or args.get("file_path"))
        if path == expected:
            return True
    return False


def _clamp_deep_research_initial_subagent_batch(context: RunContext) -> None:
    """Prevent same-response direct discovery/write beside the first child call."""
    if not deep_research_medium_or_hard(context):
        return
    if deep_research_planned_or_started_subagent_count(context) > 0:
        return
    response = context.llm_response
    if response is None:
        return
    planned_calls = extract_planned_tool_calls(response)
    if not planned_calls or not any(
        call.tool_name == "agent_tool" for call in planned_calls
    ):
        _coerce_deep_research_initial_direct_discovery(context, planned_calls)
        return
    kept = [call for call in planned_calls if call.tool_name == "agent_tool"]
    if len(kept) == len(planned_calls):
        return
    response.metadata["planned_tool_calls"] = [
        call.model_dump(mode="json") for call in kept
    ]
    context.metadata["deep_research_initial_subagent_batch_clamped"] = {
        "kept": len(kept),
        "dropped": len(planned_calls) - len(kept),
        "reason": "medium_hard_first_child_only",
    }


def _coerce_deep_research_initial_direct_discovery(
    context: RunContext, planned_calls: list[Any]
) -> None:
    """Rewrite pre-child discovery drift into the required first subagent."""
    if not planned_calls:
        return
    if not deep_research_tool_available(context, "agent_tool"):
        return
    if any(call.tool_name == "agent_tool" for call in planned_calls):
        return
    if not any(
        call.tool_name in _DEEP_RESEARCH_PRE_SUBAGENT_BLOCKED_TOOLS
        for call in planned_calls
    ):
        return
    first = planned_calls[0]
    prompt = str(getattr(context.run_input, "input", "") or "").strip()
    task = (
        "Find and summarize candidate sources for the parent Deep Research "
        "report. Return compact notes with URLs; do not write artifacts."
    )
    if prompt:
        task = f"{task}\n\nUser request: {prompt}"
    repaired = first.model_copy(
        update={
            "tool_name": "agent_tool",
            "args": {
                "task": task,
                "description": "Find source notes",
                "task_type": "research",
                "execution_mode": "sync",
            },
            "metadata": {
                **first.metadata,
                "deep_research_tool_coerced": True,
                "deep_research_repair_reason": "medium_hard_first_child_required",
                "original_tool_name": first.tool_name,
            },
        }
    )
    response = context.llm_response
    if response is None:
        return
    response.metadata["planned_tool_calls"] = [repaired.model_dump(mode="json")]
    context.metadata["deep_research_initial_direct_discovery_coerced"] = {
        "original_tool": first.tool_name,
        "dropped": max(0, len(planned_calls) - 1),
        "target": "agent_tool",
    }


def _repair_deep_research_parent_file_write_args(context: RunContext) -> None:
    """Fill empty forced file_write calls during parent synthesis.

    Some providers/models correctly select the narrowed ``file_write`` tool but
    emit ``{}`` arguments. In the parent-synthesis state, the safe target and
    content source are known: create a draft report from the embedded child
    handoff, then let the normal research contract repair loop verify/fetch.
    """
    response = context.llm_response
    if response is None:
        return
    planned_calls = extract_planned_tool_calls(response)
    if not planned_calls:
        return
    if deep_research_report_artifact_exists(
        context
    ) and not deep_research_source_ledger_artifact_exists(context):
        _repair_deep_research_source_ledger_file_write_args(context, planned_calls)
        return
    handoff = context.metadata.get("deep_research_child_synthesis")
    if not isinstance(handoff, dict) or handoff.get("pending") is not True:
        return
    if _report_artifact_path_seen(context):
        return
    repaired_payload: list[dict[str, Any]] = []
    repaired_count = 0
    file_write_index = 0
    report_write_seen_in_batch = False
    for call in planned_calls:
        normalized_call = _normalize_parent_file_write_call(call)
        if normalized_call is None:
            repaired_payload.append(call.model_dump(mode="json"))
            continue
        call = normalized_call
        args = dict(call.args) if isinstance(call.args, dict) else {}
        path = normalize_artifact_path(args.get("path"))
        if _is_research_source_ledger_alias_path(path):
            repaired = call.model_copy(
                update={
                    "args": _deep_research_parent_file_write_args(
                        context,
                        target="sources",
                    ),
                    "metadata": {
                        **call.metadata,
                        "deep_research_args_repaired": True,
                        "deep_research_repair_reason": (
                            "parent_synthesis_source_ledger_alias"
                        ),
                    },
                }
            )
            repaired_payload.append(repaired.model_dump(mode="json"))
            repaired_count += 1
            continue
        if _should_retarget_parent_file_write_to_report(context, call.args):
            args = _deep_research_parent_file_write_args(context, target="report")
            report_write_seen_in_batch = True
            repaired = call.model_copy(
                update={
                    "args": args,
                    "metadata": {
                        **call.metadata,
                        "deep_research_args_repaired": True,
                        "deep_research_repair_reason": (
                            "parent_synthesis_report_required"
                        ),
                    },
                }
            )
            repaired_payload.append(repaired.model_dump(mode="json"))
            repaired_count += 1
            continue
        if (
            report_write_seen_in_batch
            and is_research_report_path(path)
            and not deep_research_source_ledger_artifact_exists(context)
        ):
            repaired = call.model_copy(
                update={
                    "args": _deep_research_parent_file_write_args(
                        context,
                        target="sources",
                    ),
                    "metadata": {
                        **call.metadata,
                        "deep_research_args_repaired": True,
                        "deep_research_repair_reason": (
                            "parent_synthesis_source_ledger_required"
                        ),
                    },
                }
            )
            repaired_payload.append(repaired.model_dump(mode="json"))
            repaired_count += 1
            continue
        if _has_file_write_args(call.args):
            if is_research_report_path(path):
                report_write_seen_in_batch = True
            repaired_payload.append(call.model_dump(mode="json"))
            continue
        file_write_index += 1
        args = _deep_research_parent_file_write_args(
            context,
            target="sources" if file_write_index > 1 else "report",
        )
        if args["path"] == "research/report.md":
            report_write_seen_in_batch = True
        repaired = call.model_copy(
            update={
                "args": args,
                "metadata": {
                    **call.metadata,
                    "deep_research_args_repaired": True,
                    "deep_research_repair_reason": "parent_synthesis_empty_file_write",
                },
            }
        )
        repaired_payload.append(repaired.model_dump(mode="json"))
        repaired_count += 1
    if repaired_count == 0:
        return
    response.metadata["planned_tool_calls"] = repaired_payload
    context.metadata["deep_research_file_write_args_repaired"] = {
        "count": repaired_count,
        "reason": "parent_synthesis_file_write_repair",
    }


def _repair_deep_research_source_ledger_file_write_args(
    context: RunContext,
    planned_calls: list[Any],
) -> None:
    response = context.llm_response
    if response is None:
        return
    repaired_payload: list[dict[str, Any]] = []
    repaired_count = 0
    for call in planned_calls:
        normalized_call = _normalize_parent_file_write_call(call)
        if normalized_call is None:
            repaired_payload.append(call.model_dump(mode="json"))
            continue
        normalized_args = normalized_call is not call
        call = normalized_call
        args = dict(call.args) if isinstance(call.args, dict) else {}
        if not is_research_source_ledger_path(args.get("path")):
            args = _deep_research_source_ledger_file_write_args(context)
            repaired_count += 1
        elif not isinstance(args.get("content"), str):
            args = {
                **args,
                "content": _deep_research_sources_jsonl_from_tool_results(context),
            }
            repaired_count += 1
        elif normalized_args:
            repaired_count += 1
        repaired_payload.append(
            call.model_copy(
                update={
                    "args": args,
                    "metadata": {
                        **call.metadata,
                        "deep_research_args_repaired": True,
                        "deep_research_repair_reason": "source_ledger_required",
                    },
                }
            ).model_dump(mode="json")
        )
    if repaired_count == 0:
        return
    response.metadata["planned_tool_calls"] = repaired_payload
    context.metadata["deep_research_file_write_args_repaired"] = {
        "count": repaired_count,
        "reason": "source_ledger_file_write_repair",
    }


def _should_retarget_parent_file_write_to_report(
    context: RunContext,
    args: dict[str, Any],
) -> bool:
    if deep_research_report_artifact_exists(context):
        return False
    path = normalize_artifact_path(args.get("path"))
    if not path:
        return False
    return not is_research_report_path(path)


def _normalize_parent_file_write_call(call: Any) -> Any | None:
    """Normalize shaped write aliases before Deep Research-specific repair."""
    if call.tool_name not in {"file_write", "write"}:
        return None
    args = dict(call.args) if isinstance(call.args, dict) else {}
    if "path" not in args:
        for key in ("file_path", "filepath"):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                args["path"] = value
                break
    if call.tool_name == "file_write" and args == call.args:
        return call
    metadata = {
        **call.metadata,
        "tool_args_normalized": args != call.args,
    }
    if call.tool_name != "file_write":
        metadata["original_tool_name"] = call.tool_name
        metadata["tool_alias_normalized"] = True
    return call.model_copy(
        update={
            "tool_name": "file_write",
            "args": args,
            "metadata": metadata,
        }
    )


def _has_file_write_args(args: dict[str, Any]) -> bool:
    return (
        isinstance(args.get("path"), str)
        and bool(args["path"].strip())
        and isinstance(args.get("content"), str)
        and bool(args["content"].strip())
    )


def _is_research_source_ledger_alias_path(path: str) -> bool:
    return path in {
        "research/sources.md",
        "research/sources.txt",
        "research/source-ledger.md",
        "research/source_ledger.md",
        "research/source-ledger.jsonl",
        "research/source_ledger.jsonl",
    }


def _deep_research_parent_file_write_args(
    context: RunContext,
    *,
    target: str,
) -> dict[str, Any]:
    if target == "sources":
        return {
            "path": "research/sources.jsonl",
            "content": _deep_research_sources_jsonl_from_child_notes(context),
            "mode": "overwrite",
            "create_parent": True,
        }
    return {
        "path": "research/report.md",
        "content": _deep_research_report_from_child_notes(context),
        "mode": "overwrite",
        "create_parent": True,
    }


def _deep_research_source_ledger_file_write_args(context: RunContext) -> dict[str, Any]:
    return {
        "path": "research/sources.jsonl",
        "content": _deep_research_sources_jsonl_from_tool_results(context),
        "mode": "overwrite",
        "create_parent": True,
    }


def _deep_research_report_from_child_notes(context: RunContext) -> str:
    handoff = context.metadata.get("deep_research_child_synthesis")
    summary = ""
    if isinstance(handoff, dict):
        summary = str(handoff.get("summary") or "").strip()
    if not summary:
        summary = "No child summary was available in the parent handoff."
    return (
        "# Research Report\n\n"
        "Status: draft from joined child research notes. Source verification "
        "and fetched-page checks are still pending.\n\n"
        "## Child Research Notes\n\n"
        f"{summary}\n\n"
        "## Next Verification Steps\n\n"
        "- Fetch and verify the candidate URLs before treating this report as final.\n"
        "- Replace or patch this draft with cited facts from fetched pages.\n"
    )


def _deep_research_sources_jsonl_from_child_notes(context: RunContext) -> str:
    notes = _deep_research_report_from_child_notes(context)
    urls = list(dict.fromkeys(re.findall(r"https?://[^\s)\]>\"']+", notes)))
    rows = [
        {
            "url": url.rstrip(".,;"),
            "status": "candidate",
            "source": "child_summary",
            "notes": "Candidate URL from child research notes; fetch verification pending.",
        }
        for url in urls
    ]
    if not rows:
        rows.append(
            {
                "status": "candidate",
                "source": "child_summary",
                "notes": "Child notes contained no concrete URL; parent verification is still pending.",
            }
        )
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def _deep_research_sources_jsonl_from_tool_results(context: RunContext) -> str:
    ledger = research_source_ledger_from_tool_results(
        get_tool_loop_state(context).tool_results()
    ).model_dump()
    rows: list[dict[str, Any]] = []
    for section in (
        "verified_reads",
        "blocked_reads",
        "failed_reads",
        "search_candidates",
        "assistant_links",
    ):
        values = ledger.get(section)
        if not isinstance(values, list):
            continue
        for index, item in enumerate(values, start=1):
            if not isinstance(item, dict):
                continue
            row = dict(item)
            row["ledger_section"] = section
            row["ledger_index"] = index
            rows.append(row)
    if not rows:
        rows.append(
            {
                "ledger_section": "candidate",
                "status": "candidate",
                "source": "parent_synthesis_repair",
                "notes": "Source ledger placeholder; verified source reads still pending.",
            }
        )
    return "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
    )


def _report_artifact_path_seen(context: RunContext) -> bool:
    for item in context.metadata.get("tool_results") or []:
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
        if isinstance(args, dict) and is_research_report_path(args.get("path")):
            return _report_artifact_confirmed_if_possible(context)
    return False


def _report_artifact_confirmed_if_possible(context: RunContext) -> bool:
    if "workspace_cwd" in context.metadata or isinstance(
        context.metadata.get("deep_research_artifacts"), dict
    ):
        return deep_research_report_artifact_exists(context)
    return True
