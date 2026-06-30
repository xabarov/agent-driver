"""Deterministic context trimming pipeline."""

from __future__ import annotations

from typing import Any

from agent_driver.context.trimming.tool_stub import build_tool_trim_stub_content
from agent_driver.contracts.context import (
    ContextBudget,
    TrimAuditRecord,
    TrimmedContext,
)
from agent_driver.contracts.enums import TrimAction

# When the final (current-turn) message overflows the char budget it is truncated rather than
# dropped, so the request never ends up with zero messages. This floor guarantees the truncated
# turn keeps a meaningful slice even under heavy budget pressure.
_MIN_LAST_MESSAGE_CHARS = 512
_TRUNCATION_MARKER = "\n…[truncated to fit context budget]"


def _format_observation_lines(observation_rows: list[dict[str, Any]]) -> list[str]:
    """Render bounded observations into prompt lines."""
    lines: list[str] = []
    for row in observation_rows:
        preview = row.get("text_preview")
        if not isinstance(preview, str):
            continue
        source = "observation"
        provenance = row.get("provenance")
        if isinstance(provenance, dict):
            source = str(provenance.get("source", "observation"))
        lines.append(f"[{source}] {preview}")
    return lines


def _tool_call_id(row: dict[str, Any]) -> str:
    provenance = row.get("provenance")
    if not isinstance(provenance, dict):
        return ""
    return str(provenance.get("tool_call_id", ""))


def _trim_observations(
    *,
    input_observations: list[dict[str, Any]],
    max_observations: int | None,
) -> tuple[list[dict[str, Any]], list[TrimAuditRecord]]:
    """Trim observations first while preserving newest rows."""
    if max_observations is None or max_observations < 0:
        return list(input_observations), []
    drop_count = max(0, len(input_observations) - max_observations)
    dropped = input_observations[:drop_count]
    retained = input_observations[drop_count:]
    audit: list[TrimAuditRecord] = []
    for index, row in enumerate(dropped):
        audit.append(
            TrimAuditRecord(
                record_id=f"trim_obs_{index}",
                kind="observation",
                action=TrimAction.DROPPED,
                reason="max_observations_exceeded",
                metadata={
                    "observation_id": str(row.get("observation_id", "")),
                    "tool_call_id": _tool_call_id(row),
                },
            )
        )
    for index, row in enumerate(retained):
        audit.append(
            TrimAuditRecord(
                record_id=f"trim_obs_kept_{index}",
                kind="observation",
                action=TrimAction.KEPT,
                metadata={
                    "observation_id": str(row.get("observation_id", "")),
                    "tool_call_id": _tool_call_id(row),
                },
            )
        )
    return retained, audit


def _append_observations_to_tail_message(
    *,
    working_messages: list[dict[str, object]],
    retained_observations: list[dict[str, Any]],
) -> list[dict[str, object]]:
    """Inject observation previews into the last message content."""
    observation_lines = _format_observation_lines(retained_observations)
    if not observation_lines or not working_messages:
        return working_messages
    updated_messages = [dict(item) for item in working_messages]
    last = dict(updated_messages[-1])
    content = str(last.get("content", ""))
    suffix = "\n\nObservations:\n" + "\n".join(observation_lines)
    last["content"] = f"{content}{suffix}"
    updated_messages[-1] = last
    return updated_messages


def _trim_messages_to_budget(
    *,
    working_messages: list[dict[str, object]],
    max_chars: int,
    digest_pool: list[str],
    artifact_pool: list[str],
) -> tuple[list[dict[str, object]], list[TrimAuditRecord]]:
    """Trim messages with digest/artifact fallback when budget overflows."""
    kept: list[dict[str, object]] = []
    audit: list[TrimAuditRecord] = []
    running_chars = 0
    last_tool_index = -1
    for idx, row in enumerate(working_messages):
        if str(row.get("role", "")).strip().lower() == "tool":
            last_tool_index = idx

    def _tool_stub(message: dict[str, object]) -> dict[str, object]:
        name = str(message.get("name") or "tool")
        tool_call_id = str(message.get("tool_call_id") or "")
        return {
            "role": "tool",
            "name": name,
            "tool_call_id": tool_call_id or None,
            "content": build_tool_trim_stub_content(
                tool_name=name,
                tool_call_id=tool_call_id,
            ),
            "metadata": {"tool_trim_stub": True},
        }

    for index, message in enumerate(working_messages):
        content = str(message.get("content", ""))
        prospective = running_chars + len(content)
        if prospective <= max_chars:
            kept.append(message)
            running_chars = prospective
            audit.append(
                TrimAuditRecord(
                    record_id=f"trim_{index}",
                    kind="message",
                    action=TrimAction.KEPT,
                    metadata={"length": len(content)},
                )
            )
            continue
        if digest_pool:
            digest_id = digest_pool.pop(0)
            audit.append(
                TrimAuditRecord(
                    record_id=f"trim_{index}",
                    kind="message",
                    action=TrimAction.DIGESTED,
                    reason="budget_overflow",
                    metadata={"digest_id": digest_id, "length": len(content)},
                )
            )
            continue
        if artifact_pool:
            artifact_id = artifact_pool.pop(0)
            audit.append(
                TrimAuditRecord(
                    record_id=f"trim_{index}",
                    kind="message",
                    action=TrimAction.REPLACED_WITH_ARTIFACT,
                    reason="budget_overflow",
                    metadata={"artifact_id": artifact_id, "length": len(content)},
                )
            )
            continue
        if index == last_tool_index:
            stub = _tool_stub(message)
            stub_content = str(stub.get("content", ""))
            while kept and running_chars + len(stub_content) > max_chars:
                removed = kept.pop(0)
                removed_content = str(removed.get("content", ""))
                running_chars = max(0, running_chars - len(removed_content))
                audit.append(
                    TrimAuditRecord(
                        record_id=f"trim_rebalance_{index}_{len(kept)}",
                        kind="message",
                        action=TrimAction.DROPPED,
                        reason="budget_rebalanced_for_tool_stub",
                        metadata={"length": len(removed_content)},
                    )
                )
            kept.append(stub)
            running_chars += len(stub_content)
            audit.append(
                TrimAuditRecord(
                    record_id=f"trim_{index}",
                    kind="message",
                    action=TrimAction.REPLACED_WITH_ARTIFACT,
                    reason="budget_overflow_tool_stub",
                    metadata={"length": len(content), "tool_stub": True},
                )
            )
            continue
        if index == len(working_messages) - 1:
            # Never drop the final message (the current turn): truncating it keeps the request
            # valid. Dropping it can leave zero messages, which providers reject outright
            # ("Input required: specify prompt or messages"). Rebalance by dropping older kept
            # messages first so the current turn keeps a meaningful budget.
            budget_left = max_chars - running_chars
            while kept and budget_left < min(len(content), _MIN_LAST_MESSAGE_CHARS):
                removed = kept.pop(0)
                removed_content = str(removed.get("content", ""))
                running_chars = max(0, running_chars - len(removed_content))
                budget_left = max_chars - running_chars
                audit.append(
                    TrimAuditRecord(
                        record_id=f"trim_rebalance_last_{index}_{len(kept)}",
                        kind="message",
                        action=TrimAction.DROPPED,
                        reason="budget_rebalanced_for_last_message",
                        metadata={"length": len(removed_content)},
                    )
                )
            keep_chars = max(_MIN_LAST_MESSAGE_CHARS if not kept else 0, min(len(content), budget_left))
            keep_chars = min(len(content), max(keep_chars, 1))
            truncated_message = dict(message)
            new_content = content[:keep_chars].rstrip()
            if len(new_content) < len(content):
                new_content = f"{new_content}{_TRUNCATION_MARKER}"
            truncated_message["content"] = new_content
            kept.append(truncated_message)
            running_chars += len(new_content)
            audit.append(
                TrimAuditRecord(
                    record_id=f"trim_{index}",
                    kind="message",
                    action=TrimAction.TRUNCATED,
                    reason="budget_overflow_last_message",
                    metadata={"length": len(content), "kept_length": len(new_content)},
                )
            )
            continue
        audit.append(
            TrimAuditRecord(
                record_id=f"trim_{index}",
                kind="message",
                action=TrimAction.DROPPED,
                reason="budget_overflow",
                metadata={"length": len(content)},
            )
        )
    return kept, audit


def _enforce_max_messages(
    *,
    kept: list[dict[str, object]],
    max_messages: int | None,
) -> tuple[list[dict[str, object]], list[TrimAuditRecord]]:
    """Apply deterministic max_messages cap after primary trimming."""
    if max_messages is None or len(kept) <= max_messages:
        return kept, []
    overflow = len(kept) - max_messages
    dropped = kept[:overflow]
    retained = kept[overflow:]
    audit: list[TrimAuditRecord] = []
    for idx, item in enumerate(dropped):
        audit.append(
            TrimAuditRecord(
                record_id=f"trim_max_messages_{idx}",
                kind="message",
                action=TrimAction.DROPPED,
                reason="max_messages_exceeded",
                metadata={"content_length": len(str(item.get("content", "")))},
            )
        )
    return retained, audit


def trim_context(
    *,
    budget: ContextBudget,
    prompt_messages: list[dict[str, object]],
    digest_ids: list[str] | None = None,
    artifact_ids: list[str] | None = None,
    observation_rows: list[dict[str, Any]] | None = None,
) -> TrimmedContext:
    """Apply deterministic trimming to prompt messages under char budget."""
    working_messages = [dict(item) for item in prompt_messages]
    digest_pool = list(digest_ids or [])
    artifact_pool = list(artifact_ids or [])
    audit: list[TrimAuditRecord] = []
    input_observations = list(observation_rows or [])
    retained_observations, obs_audit = _trim_observations(
        input_observations=input_observations,
        max_observations=budget.max_observations,
    )
    audit.extend(obs_audit)
    working_messages = _append_observations_to_tail_message(
        working_messages=working_messages,
        retained_observations=retained_observations,
    )
    kept, message_audit = _trim_messages_to_budget(
        working_messages=working_messages,
        max_chars=budget.max_chars,
        digest_pool=digest_pool,
        artifact_pool=artifact_pool,
    )
    audit.extend(message_audit)
    kept, max_message_audit = _enforce_max_messages(
        kept=kept,
        max_messages=budget.max_messages,
    )
    audit.extend(max_message_audit)

    retained_digest_ids = [
        record.metadata["digest_id"]
        for record in audit
        if record.action == TrimAction.DIGESTED and "digest_id" in record.metadata
    ]
    retained_artifact_ids = [
        record.metadata["artifact_id"]
        for record in audit
        if record.action == TrimAction.REPLACED_WITH_ARTIFACT
        and "artifact_id" in record.metadata
    ]

    return TrimmedContext(
        prompt_messages=kept,
        retained_digest_ids=[str(item) for item in retained_digest_ids],
        retained_artifact_ids=[str(item) for item in retained_artifact_ids],
        audit=audit,
        metadata={
            "max_chars": budget.max_chars,
            "max_messages": budget.max_messages,
            "max_observations": budget.max_observations,
            "input_messages": len(working_messages),
            "kept_messages": len(kept),
            "input_observations": len(input_observations),
            "kept_observations": len(retained_observations),
            "dropped_observations": len(input_observations)
            - len(retained_observations),
            "final_chars": sum(len(str(item.get("content", ""))) for item in kept),
            "retained_observations": retained_observations,
        },
    )
