"""Build redaction-safe provenance summaries from runtime events."""

from __future__ import annotations

from collections import Counter
from typing import Any

from agent_driver.contracts.context import (
    ArtifactProvenance,
    ContextLedgerSummary,
    ContextProvenanceRecord,
    MemoryFactProvenance,
    SideEffectTransaction,
    SkillAttachment,
    SourceEvidenceRecord,
)
from agent_driver.observability.source_evidence import merge_source_evidence

_SECRET_KEY_MARKERS = ("token", "secret", "password", "api_key", "auth")
_CONTEXT_LIST_KEYS = ("context_provenance", "context_provenance_records")
_MEMORY_LIST_KEYS = ("memory_fact_provenance", "memory_facts")
_SKILL_LIST_KEYS = ("skill_attachments", "invoked_skill_refs", "skill_invocations")
_ARTIFACT_LIST_KEYS = ("artifact_provenance", "artifact_refs")
_SOURCE_LIST_KEYS = ("source_evidence", "sources")
_TRANSACTION_LIST_KEYS = ("side_effect_transactions", "transactions")
_WRITE_TOOLS = {"file_write", "file_edit", "file_patch", "notebook_edit"}
_READ_TOOLS = {
    "read_file",
    "artifact_read",
    "artifact_preview",
    "web_search",
    "web_fetch",
    "source_read",
    "browser_read",
    "pdf_read",
}
_CODE_EXEC_TOOLS = {"bash", "shell", "python", "python_exec", "powershell"}


def build_provenance_summary(
    *,
    events: list[dict[str, object]],
    metadata: dict[str, Any] | None = None,
    required_evidence: list[str] | None = None,
) -> dict[str, Any]:
    """Return compact provenance blocks and contract verdicts."""

    meta = metadata if isinstance(metadata, dict) else {}
    context_records = _context_records(events=events, metadata=meta)
    memory_facts = _memory_facts(events=events, metadata=meta)
    skill_attachments = _skill_attachments(events=events, metadata=meta)
    artifact_records = _artifact_records(events=events, metadata=meta)
    source_records = _source_records(events=events, metadata=meta)
    transactions = _side_effect_transactions(events=events, metadata=meta)
    context_summary = _context_summary(context_records, metadata=meta)
    required = set(required_evidence or [])
    violations = _contract_violations(
        required=required,
        context_count=len(context_records),
        skill_count=len(skill_attachments),
        artifact_count=len(artifact_records),
        source_count=len(source_records),
        transaction_count=len(transactions),
        side_effect_seen=_side_effect_seen(events),
        provenance_loss=context_summary.compacted_count > 0
        and context_summary.high_risk_missing_count > 0,
    )
    return {
        "context_provenance": context_summary.model_dump(mode="json"),
        "memory_fact_provenance": {
            "facts": [item.model_dump(mode="json") for item in memory_facts],
            "count": len(memory_facts),
            "link_check_statuses": sorted(
                {
                    item.link_check_status
                    for item in memory_facts
                    if item.link_check_status
                }
            ),
            "redaction": {
                "safe_by_default": True,
                "contains_raw_memory_facts": False,
            },
        },
        "skill_attachments": {
            "attachments": [item.model_dump(mode="json") for item in skill_attachments],
            "count": len(skill_attachments),
            "statuses": sorted({item.status for item in skill_attachments}),
            "redaction": {"safe_by_default": True, "contains_raw_skill_body": False},
        },
        "artifact_provenance": {
            "artifacts": [item.model_dump(mode="json") for item in artifact_records],
            "count": len(artifact_records),
            "paths": sorted(
                {item.path for item in artifact_records if isinstance(item.path, str)}
            ),
            "redaction": {
                "safe_by_default": True,
                "contains_raw_artifact_content": False,
            },
        },
        "source_evidence": {
            "sources": [item.model_dump(mode="json") for item in source_records],
            "count": len(source_records),
            "domains": sorted(
                {item.domain for item in source_records if isinstance(item.domain, str)}
            ),
            "fetch_statuses": sorted({item.fetch_status for item in source_records}),
            "redaction": {"safe_by_default": True, "contains_raw_source_pages": False},
        },
        "side_effect_transactions": {
            "transactions": [item.model_dump(mode="json") for item in transactions],
            "count": len(transactions),
            "classes": sorted({item.side_effect_class for item in transactions}),
            "redaction": {"safe_by_default": True, "contains_raw_file_contents": False},
        },
        "contract_verdicts": {
            "required_evidence": sorted(required),
            "violations": violations,
            "status": "fail" if any(violations.values()) else "pass",
        },
    }


def _context_records(
    *, events: list[dict[str, object]], metadata: dict[str, Any]
) -> list[ContextProvenanceRecord]:
    rows = _records_from_lists(metadata, _CONTEXT_LIST_KEYS)
    for event in events:
        data = _event_data(event)
        rows.extend(_records_from_lists(data, _CONTEXT_LIST_KEYS))
        if event.get("event") == "context_provenance_recorded":
            rows.append(data)
    records: list[ContextProvenanceRecord] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        payload = _safe_payload(row)
        payload.setdefault("context_id", _first_str(row, "context_id", "id") or f"context_{index}")
        payload.setdefault("kind", _first_str(row, "kind", "type") or "context")
        payload.setdefault("status", _first_str(row, "status") or "attached")
        payload.setdefault("redaction_level", _first_str(row, "redaction_level") or "summary")
        records.append(
            ContextProvenanceRecord.model_validate(
                _model_payload(ContextProvenanceRecord, payload)
            )
        )
    for row in _trim_audit_records(metadata):
        records.append(ContextProvenanceRecord.model_validate(row))
    return _dedupe_models(records, "context_id")


def _trim_audit_records(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    trim_audit = metadata.get("trim_audit")
    if not isinstance(trim_audit, list):
        return rows
    for index, item in enumerate(trim_audit, start=1):
        if not isinstance(item, dict):
            continue
        action = _first_str(item, "action") or "trimmed"
        status = "truncated" if action in {"truncate", "trim", "trimmed"} else action
        rows.append(
            {
                "context_id": _first_str(item, "context_id", "id")
                or f"trim_audit_{index}",
                "kind": _first_str(item, "kind", "section") or "trim_audit",
                "source_ref": _first_str(item, "source_ref", "source"),
                "status": status if status in _context_statuses() else "truncated",
                "redaction_level": "summary",
                "token_estimate": _non_negative_int(
                    item.get("tokens") or item.get("token_estimate")
                ),
                "metadata": _metadata_subset(item, ("reason", "action")),
            }
        )
    return rows


def _context_summary(
    records: list[ContextProvenanceRecord], *, metadata: dict[str, Any]
) -> ContextLedgerSummary:
    by_kind = Counter(item.kind for item in records)
    by_status = Counter(item.status for item in records)
    last_verdict = _last_compaction_verdict(metadata)
    high_risk_missing = sum(
        1
        for item in records
        if item.status == "missing"
        and ("high_risk" in item.product_tags or item.metadata.get("risk") == "high")
    )
    return ContextLedgerSummary(
        records=records,
        counts_by_kind=dict(sorted(by_kind.items())),
        counts_by_status=dict(sorted(by_status.items())),
        dropped_count=by_status.get("dropped", 0),
        truncated_count=by_status.get("truncated", 0),
        compacted_count=by_status.get("compacted", 0),
        high_risk_missing_count=high_risk_missing,
        last_compaction_verdict=last_verdict,
    )


def _memory_facts(
    *, events: list[dict[str, object]], metadata: dict[str, Any]
) -> list[MemoryFactProvenance]:
    rows = _records_from_lists(metadata, _MEMORY_LIST_KEYS)
    for event in events:
        data = _event_data(event)
        rows.extend(_records_from_lists(data, _MEMORY_LIST_KEYS))
    facts: list[MemoryFactProvenance] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        payload = _safe_payload(row)
        payload.setdefault("fact_id", _first_str(row, "fact_id", "id") or f"fact_{index}")
        facts.append(
            MemoryFactProvenance.model_validate(
                _model_payload(MemoryFactProvenance, payload)
            )
        )
    return _dedupe_models(facts, "fact_id")


def _skill_attachments(
    *, events: list[dict[str, object]], metadata: dict[str, Any]
) -> list[SkillAttachment]:
    rows = _records_from_lists(metadata, _SKILL_LIST_KEYS)
    for event in events:
        data = _event_data(event)
        rows.extend(_records_from_lists(data, _SKILL_LIST_KEYS))
        if event.get("event") == "skill_invoked":
            rows.append(data)
        if event.get("event") == "tool_call_completed":
            for tool in _event_tools(data):
                name = tool.get("tool_name") or tool.get("name")
                if name in {"skill_view", "skill_tool"}:
                    rows.append(_skill_row_from_tool(tool))
    attachments: list[SkillAttachment] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        digest = _first_str(row, "digest", "manifest_checksum")
        name = _first_str(row, "name", "skill_name", "skill_id") or f"skill_{index}"
        payload = _safe_payload(row)
        if not _first_str(payload, "skill_id", "id"):
            payload["skill_id"] = _first_str(row, "skill_id", "id") or name
        if not _first_str(payload, "name"):
            payload["name"] = name
        payload.setdefault("source", _first_str(row, "source") or "filesystem")
        payload.setdefault("status", _first_str(row, "status") or "attached")
        payload.setdefault("resolved_path", _first_str(row, "resolved_path", "path"))
        payload.setdefault(
            "redacted_manifest_checksum",
            _redacted_checksum(digest),
        )
        payload.setdefault(
            "attachment_scope",
            _first_str(row, "attachment_scope", "scope") or "run",
        )
        attachments.append(
            SkillAttachment.model_validate(_model_payload(SkillAttachment, payload))
        )
    return _dedupe_models(attachments, "skill_id")


def _skill_row_from_tool(tool: dict[str, Any]) -> dict[str, Any]:
    args = tool.get("args") if isinstance(tool.get("args"), dict) else {}
    result = tool.get("structured_output")
    if not isinstance(result, dict):
        result = tool.get("result") if isinstance(tool.get("result"), dict) else {}
    return {
        "name": args.get("name") or result.get("name") or args.get("skill"),
        "path": args.get("path") or result.get("path"),
        "digest": result.get("digest"),
        "status": tool.get("status") or "observed",
        "activation_reason": "tool_call",
        "metadata": {"tool_name": tool.get("tool_name") or tool.get("name")},
    }


def _artifact_records(
    *, events: list[dict[str, object]], metadata: dict[str, Any]
) -> list[ArtifactProvenance]:
    rows = _records_from_lists(metadata, _ARTIFACT_LIST_KEYS)
    for event in events:
        data = _event_data(event)
        rows.extend(_records_from_lists(data, _ARTIFACT_LIST_KEYS))
        if event.get("event") in {"artifact_created", "artifact_updated"}:
            rows.append(data)
    artifacts: list[ArtifactProvenance] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        path = _first_str(row, "path", "uri")
        payload = _safe_payload(row)
        payload.setdefault(
            "artifact_id",
            _first_str(row, "artifact_id", "id") or path or f"artifact_{index}",
        )
        payload.setdefault(
            "artifact_type",
            _first_str(row, "artifact_type", "kind", "type") or "artifact",
        )
        payload.setdefault("source_tool", _first_str(row, "source_tool", "tool_name"))
        payload.setdefault("path", path)
        payload.setdefault(
            "safe_path_classification",
            _safe_path_classification(path),
        )
        payload.setdefault("preview_status", _first_str(row, "preview_status"))
        artifacts.append(
            ArtifactProvenance.model_validate(_model_payload(ArtifactProvenance, payload))
        )
    return _dedupe_models(artifacts, "artifact_id")


def _source_records(
    *, events: list[dict[str, object]], metadata: dict[str, Any]
) -> list[SourceEvidenceRecord]:
    rows = _records_from_lists(metadata, _SOURCE_LIST_KEYS)
    for event in events:
        data = _event_data(event)
        rows.extend(_records_from_lists(data, _SOURCE_LIST_KEYS))
        if event.get("event") in {"source_ledger_updated", "citation_coverage_updated"}:
            rows.extend(_records_from_lists(data, _SOURCE_LIST_KEYS))
        if event.get("event") == "tool_call_completed":
            for tool in _event_tools(data):
                rows.extend(_records_from_lists(tool, _SOURCE_LIST_KEYS))
    merged = merge_source_evidence([row for row in rows if isinstance(row, dict)])
    records: list[SourceEvidenceRecord] = []
    source_rows = merged or [row for row in rows if isinstance(row, dict)]
    for index, row in enumerate(source_rows, start=1):
        payload = _safe_payload(row)
        payload.setdefault(
            "source_id",
            _first_str(row, "source_id", "id") or f"source_{index}",
        )
        payload.setdefault(
            "source_type",
            _first_str(row, "source_type", "type") or "source",
        )
        payload.setdefault("canonical_url", _first_str(row, "canonical_url", "url"))
        payload.setdefault("domain", _first_str(row, "domain"))
        payload.setdefault("fetch_status", _first_str(row, "fetch_status", "status") or "observed")
        records.append(
            SourceEvidenceRecord.model_validate(
                _model_payload(SourceEvidenceRecord, payload)
            )
        )
    return _dedupe_models(records, "source_id")


def _side_effect_transactions(
    *, events: list[dict[str, object]], metadata: dict[str, Any]
) -> list[SideEffectTransaction]:
    rows = _records_from_lists(metadata, _TRANSACTION_LIST_KEYS)
    for event in events:
        data = _event_data(event)
        rows.extend(_records_from_lists(data, _TRANSACTION_LIST_KEYS))
        if event.get("event") == "runtime_decision":
            side_effect = data.get("side_effect")
            if isinstance(side_effect, dict):
                rows.append({**side_effect, "runtime_decision_id": data.get("decision_id")})
        if event.get("event") == "tool_call_completed":
            for tool in _event_tools(data):
                tx = _transaction_from_tool(tool)
                if tx:
                    rows.append(tx)
        if event.get("event") in {"artifact_created", "artifact_updated"}:
            rows.append(_transaction_from_artifact_event(data))
    transactions: list[SideEffectTransaction] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        payload = _safe_payload(row)
        payload.setdefault(
            "transaction_id",
            _first_str(row, "transaction_id", "id") or f"side_effect_{index}",
        )
        payload.setdefault(
            "side_effect_class",
            _first_str(row, "side_effect_class", "class") or "workspace-write",
        )
        transactions.append(
            SideEffectTransaction.model_validate(
                _model_payload(SideEffectTransaction, payload)
            )
        )
    return _dedupe_models(transactions, "transaction_id")


def _transaction_from_tool(tool: dict[str, Any]) -> dict[str, Any] | None:
    tool_name = _first_str(tool, "tool_name", "name")
    if not tool_name:
        return None
    side_effect_class = _side_effect_class(tool_name)
    if side_effect_class is None:
        return None
    args = tool.get("args") if isinstance(tool.get("args"), dict) else {}
    call_id = _first_str(tool, "tool_call_id", "id") or tool_name
    status = _first_str(tool, "status") or "observed"
    return {
        "transaction_id": f"tool:{call_id}",
        "side_effect_class": side_effect_class,
        "tool_name": tool_name,
        "target_ref": _first_str(args, "path", "file_path", "url"),
        "apply_status": "applied" if status in {"completed", "success"} else status,
        "policy_status": _first_str(tool, "policy_status"),
    }


def _transaction_from_artifact_event(data: dict[str, Any]) -> dict[str, Any]:
    path = _first_str(data, "path") or "artifact"
    tool_name = _first_str(data, "tool_name")
    call_id = _first_str(data, "tool_call_id") or path
    return {
        "transaction_id": f"artifact:{call_id}:{path}",
        "side_effect_class": "artifact-create",
        "tool_name": tool_name,
        "target_ref": path,
        "apply_status": "applied",
        "metadata": {"event_projection": True},
    }


def _contract_violations(
    *,
    required: set[str],
    context_count: int,
    skill_count: int,
    artifact_count: int,
    source_count: int,
    transaction_count: int,
    side_effect_seen: bool,
    provenance_loss: bool,
) -> dict[str, bool]:
    return {
        "missing_context_provenance": "context_provenance" in required
        and context_count == 0,
        "missing_skill_provenance": "skill_attachments" in required
        and skill_count == 0,
        "missing_artifact_provenance": "artifact_provenance" in required
        and artifact_count == 0,
        "missing_source_evidence": "source_evidence" in required and source_count == 0,
        "unsafe_side_effect_without_transaction_projection": side_effect_seen
        and transaction_count == 0,
        "compaction_provenance_loss": provenance_loss,
    }


def _records_from_lists(container: dict[str, Any], keys: tuple[str, ...]) -> list[Any]:
    rows: list[Any] = []
    for key in keys:
        value = container.get(key)
        if isinstance(value, list):
            rows.extend(value)
        elif isinstance(value, dict):
            rows.append(value)
    return rows


def _event_data(event: dict[str, object]) -> dict[str, Any]:
    data = event.get("data") or event.get("payload")
    return dict(data) if isinstance(data, dict) else {}


def _event_tools(data: dict[str, Any]) -> list[dict[str, Any]]:
    tools = data.get("tools")
    if isinstance(tools, list):
        return [tool for tool in tools if isinstance(tool, dict)]
    direct = data.get("tool_name")
    if isinstance(direct, str) and direct:
        return [data]
    return []


def _safe_payload(row: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        key: value
        for key, value in row.items()
        if key
        not in {
            "content",
            "text",
            "raw",
            "raw_content",
            "body",
            "page_content",
            "prompt",
        }
        and not _is_sensitive_key(str(key))
    }
    return _redact_value(allowed)


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("<redacted>" if _is_sensitive_key(str(key)) else _redact_value(item))
            for key, item in value.items()
            if key
            not in {
                "content",
                "text",
                "raw",
                "raw_content",
                "body",
                "page_content",
                "prompt",
            }
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


def _is_sensitive_key(key: str) -> bool:
    lower = key.lower()
    return lower == "base_url" or lower.endswith("_base_url") or any(
        marker in lower for marker in _SECRET_KEY_MARKERS
    )


def _metadata_subset(row: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: _redact_value(row[key]) for key in keys if key in row}


def _dedupe_models(items: list[Any], key: str) -> list[Any]:
    seen: set[str] = set()
    deduped: list[Any] = []
    for item in items:
        value = getattr(item, key, None)
        marker = str(value)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(item)
    return deduped


def _model_payload(model: Any, payload: dict[str, Any]) -> dict[str, Any]:
    fields = set(model.model_fields)
    return {key: value for key, value in payload.items() if key in fields}


def _first_str(container: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = container.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    return None


def _redacted_checksum(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip()
    if len(cleaned) <= 12:
        return cleaned
    return f"{cleaned[:12]}..."


def _safe_path_classification(path: str | None) -> str | None:
    if not path:
        return None
    if path.startswith("/") or ".." in path.replace("\\", "/").split("/"):
        return "unsafe_or_external"
    if path.startswith("research/"):
        return "workspace_research"
    return "workspace_relative"


def _side_effect_class(tool_name: str) -> str | None:
    if tool_name in _WRITE_TOOLS:
        return "workspace-write"
    if tool_name in _READ_TOOLS:
        if tool_name in {"web_search", "web_fetch", "source_read", "browser_read", "pdf_read"}:
            return "network-fetch"
        return "read-only"
    if tool_name in _CODE_EXEC_TOOLS:
        return "code-exec"
    return None


def _side_effect_seen(events: list[dict[str, object]]) -> bool:
    for event in events:
        if event.get("event") in {"artifact_created", "artifact_updated"}:
            return True
        if event.get("event") == "tool_call_completed":
            for tool in _event_tools(_event_data(event)):
                side_effect = _first_str(tool, "side_effect", "side_effect_class")
                if side_effect and side_effect not in {"read_only", "read-only", "none"}:
                    return True
                name = _first_str(tool, "tool_name", "name")
                if name and _side_effect_class(name) not in {None, "read-only"}:
                    return True
    return False


def _last_compaction_verdict(metadata: dict[str, Any]) -> str | None:
    for key in ("compaction_result", "compaction_decision"):
        value = metadata.get(key)
        if isinstance(value, dict):
            verdict = _first_str(value, "verdict", "status", "action")
            if verdict:
                return verdict
    return None


def _context_statuses() -> set[str]:
    return {"attached", "retained", "compacted", "dropped", "truncated", "expired", "missing"}


__all__ = ["build_provenance_summary"]
