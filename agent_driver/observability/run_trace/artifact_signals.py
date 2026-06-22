"""Artifact and report-document signals for run-trace summaries."""

from __future__ import annotations

from typing import Any

from agent_driver.observability.run_trace.tools import count_events as _count_events
from agent_driver.observability.run_trace.tools import event_data as _event_data
from agent_driver.observability.run_trace.tools import event_tools

from ._common import (
    _dedupe_paths,
    _path_targets_report,
    _tool_payload_succeeded,
)


def _tool_is_parent_report_write(tool: dict[str, Any]) -> bool:
    if not _tool_payload_succeeded(tool):
        return False
    tool_name = tool.get("tool_name") or tool.get("name")
    if tool_name not in {"file_write", "file_edit", "file_patch"}:
        return False
    args = tool.get("args")
    if not isinstance(args, dict):
        return False
    return _path_targets_report(args.get("path") or args.get("file_path"))


def _artifact_event_is_parent_report_write(data: dict[str, Any]) -> bool:
    return data.get("path") == "research/report.md" and data.get("tool_name") in {
        "file_write",
        "file_edit",
        "file_patch",
    }


def _artifact_event_is_source_ledger_write(data: dict[str, Any]) -> bool:
    return data.get("path") == "research/sources.jsonl" and data.get("tool_name") in {
        "file_write",
        "file_edit",
        "file_patch",
        "source_ledger",
    }


def _artifact_summary(events: list[dict[str, object]]) -> dict[str, Any]:
    updates: list[dict[str, Any]] = []
    report_paths: list[str] = []
    source_ledger_paths: list[str] = []
    claims_paths: list[str] = []
    source_ledger_records = 0
    claims_records = 0
    claims_verified = 0
    claims_unsupported = 0
    for event in events:
        if event.get("event") not in {"artifact_created", "artifact_updated"}:
            continue
        data = _event_data(event)
        path = data.get("path")
        if not isinstance(path, str) or not path:
            continue
        operation = data.get("operation")
        kind = data.get("kind")
        updates.append(
            {
                "event": event.get("event"),
                "path": path,
                "kind": kind if isinstance(kind, str) else None,
                "operation": operation if isinstance(operation, str) else None,
                "mode": data.get("mode") if isinstance(data.get("mode"), str) else None,
                "size_bytes": data.get("size_bytes", data.get("bytes")),
                "tool_name": data.get("tool_name"),
                "tool_call_id": data.get("tool_call_id"),
            }
        )
        if path == "research/report.md":
            report_paths.append(path)
        if path == "research/sources.jsonl":
            source_ledger_paths.append(path)
            record_count = data.get("record_count")
            if isinstance(record_count, int) and not isinstance(record_count, bool):
                source_ledger_records = max(source_ledger_records, record_count)
        if path in {"research/claims.jsonl", "research/claims.md"}:
            claims_paths.append(path)
            record_count = data.get("record_count")
            if isinstance(record_count, int) and not isinstance(record_count, bool):
                claims_records = max(claims_records, record_count)
            verified_count = data.get("verified_count")
            if isinstance(verified_count, int) and not isinstance(verified_count, bool):
                claims_verified = max(claims_verified, verified_count)
            unsupported_count = data.get("unsupported_count")
            if isinstance(unsupported_count, int) and not isinstance(
                unsupported_count, bool
            ):
                claims_unsupported = max(claims_unsupported, unsupported_count)
    return {
        "updates": updates,
        "update_count": len(updates),
        "created_count": _count_events(events, "artifact_created"),
        "updated_count": _count_events(events, "artifact_updated"),
        "paths": _dedupe_paths([item["path"] for item in updates]),
        "report_updated": bool(report_paths),
        "report_trace_update_seen": bool(report_paths),
        "report_write_seen": _report_write_seen(events, updates),
        "report_update_count": len(report_paths),
        "report_full_write_count": _report_full_write_count(updates),
        "report_patch_count": _report_patch_count(updates),
        "report_lifecycle": _report_lifecycle_from_updates(updates),
        **_report_read_edit_flow_summary(events),
        "source_ledger_updated": bool(source_ledger_paths),
        "source_ledger_update_count": len(source_ledger_paths),
        "source_ledger_record_count": source_ledger_records,
        "claims_updated": bool(claims_paths),
        "claims_update_count": len(claims_paths),
        "claims_record_count": claims_records,
        "claims_verified_count": claims_verified,
        "claims_unsupported_count": claims_unsupported,
    }


def _report_full_write_count(updates: list[dict[str, Any]]) -> int:
    count = 0
    for item in updates:
        if item.get("path") != "research/report.md":
            continue
        if item.get("tool_name") != "file_write":
            continue
        mode = item.get("mode")
        if mode == "append":
            continue
        count += 1
    return count


def _report_patch_count(updates: list[dict[str, Any]]) -> int:
    count = 0
    for item in updates:
        if item.get("path") != "research/report.md":
            continue
        tool_name = item.get("tool_name")
        operation = item.get("operation")
        if tool_name in {"file_edit", "file_patch"} or operation in {"edit", "patch"}:
            count += 1
    return count


def _report_lifecycle_from_updates(updates: list[dict[str, Any]]) -> str:
    report_updates = [
        item for item in updates if item.get("path") == "research/report.md"
    ]
    if not report_updates:
        return "not_started"
    if any(
        item.get("operation") == "capture" or item.get("mode") == "captured_inline"
        for item in report_updates
    ):
        return "captured_inline"
    if any(
        item.get("tool_name") in {"file_edit", "file_patch"}
        or item.get("operation") in {"edit", "patch"}
        for item in report_updates
    ):
        return "patched"
    if any(item.get("tool_name") == "file_write" for item in report_updates):
        return "created"
    return "created"


def _report_write_seen(
    events: list[dict[str, object]],
    updates: list[dict[str, Any]],
) -> bool:
    for item in updates:
        if item.get("path") == "research/report.md" and item.get("tool_name") in {
            "file_write",
            "file_edit",
            "file_patch",
        }:
            return True
    return False


def _report_read_edit_flow_summary(events: list[dict[str, object]]) -> dict[str, int]:
    fresh_read = False
    targeted_edits = 0
    stale_targeted_edits = 0
    report_reads = 0
    repeated_unchanged_reads = 0
    report_generation = 0
    # Track which read *tools* have inspected each report generation. A single
    # multi-modal review pass (read_file + artifact_preview of the same draft
    # before patching) is legitimate, so only re-running the *same* read tool on
    # an unchanged generation counts as a redundant repeat.
    reads_by_generation: dict[int, set[str]] = {}
    for action in _report_flow_actions(events):
        if action["kind"] == "read":
            report_reads += 1
            read_tool = str(action.get("tool_name") or "read")
            seen_tools = reads_by_generation.setdefault(report_generation, set())
            if read_tool in seen_tools:
                repeated_unchanged_reads += 1
            seen_tools.add(read_tool)
            fresh_read = True
            continue
        tool_name = action.get("tool_name")
        if tool_name == "file_write":
            report_generation += 1
            fresh_read = False
        elif tool_name in {"file_edit", "file_patch"}:
            targeted_edits += 1
            if not fresh_read:
                stale_targeted_edits += 1
            report_generation += 1
            fresh_read = False
    return {
        "report_read_count": report_reads,
        "repeated_unchanged_report_read_count": repeated_unchanged_reads,
        "report_targeted_edit_count": targeted_edits,
        "report_targeted_edit_without_fresh_read_count": stale_targeted_edits,
    }


def _report_flow_actions(events: list[dict[str, object]]) -> list[dict[str, str]]:
    updates_by_call_id: dict[str, list[dict[str, str]]] = {}
    for event in events:
        if event.get("event") not in {"artifact_created", "artifact_updated"}:
            continue
        data = _event_data(event)
        if data.get("path") != "research/report.md":
            continue
        tool_name = data.get("tool_name")
        if not isinstance(tool_name, str):
            continue
        action = {"kind": "write", "tool_name": tool_name}
        call_id = data.get("tool_call_id")
        if isinstance(call_id, str) and call_id:
            updates_by_call_id.setdefault(call_id, []).append(action)

    actions: list[dict[str, str]] = []
    paired_update_ids: set[int] = set()
    for event in events:
        if event.get("event") in {"artifact_created", "artifact_updated"}:
            data = _event_data(event)
            if data.get("path") != "research/report.md":
                continue
            call_id = data.get("tool_call_id")
            if isinstance(call_id, str) and call_id:
                continue
            tool_name = data.get("tool_name")
            if isinstance(tool_name, str):
                actions.append({"kind": "write", "tool_name": tool_name})
            continue
        if event.get("event") != "tool_call_completed":
            continue
        for tool in event_tools(_event_data(event)):
            tool_name = tool.get("tool_name") or tool.get("name")
            if tool_name in {"read_file", "artifact_read", "artifact_preview"}:
                args = tool.get("args")
                if isinstance(args, dict) and _path_targets_report(args.get("path")):
                    actions.append({"kind": "read", "tool_name": str(tool_name)})
            call_id = tool.get("tool_call_id")
            if isinstance(call_id, str) and call_id:
                for update in updates_by_call_id.get(call_id, []):
                    actions.append(update)
                    paired_update_ids.add(id(update))

    for updates in updates_by_call_id.values():
        for update in updates:
            if id(update) not in paired_update_ids:
                actions.append(update)
    return actions
