"""Deterministic context trimming pipeline."""

from __future__ import annotations

from typing import Any

from agent_driver.contracts.context import (
    ContextBudget,
    TrimAuditRecord,
    TrimmedContext,
)
from agent_driver.contracts.enums import TrimAction


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


def trim_context(  # pylint: disable=too-many-locals
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
    kept: list[dict[str, object]] = []
    audit: list[TrimAuditRecord] = []
    running_chars = 0
    input_observations = list(observation_rows or [])
    retained_observations = input_observations

    if budget.max_observations is not None and budget.max_observations >= 0:
        drop_count = max(0, len(input_observations) - budget.max_observations)
        dropped = input_observations[:drop_count]
        retained_observations = input_observations[drop_count:]
        for index, row in enumerate(dropped):
            audit.append(
                TrimAuditRecord(
                    record_id=f"trim_obs_{index}",
                    kind="observation",
                    action=TrimAction.DROPPED,
                    reason="max_observations_exceeded",
                    metadata={
                        "observation_id": str(row.get("observation_id", "")),
                        "tool_call_id": str(
                            (
                                row.get("provenance", {})
                                if isinstance(row.get("provenance"), dict)
                                else {}
                            ).get("tool_call_id", "")
                        ),
                    },
                )
            )
        for index, row in enumerate(retained_observations):
            audit.append(
                TrimAuditRecord(
                    record_id=f"trim_obs_kept_{index}",
                    kind="observation",
                    action=TrimAction.KEPT,
                    metadata={
                        "observation_id": str(row.get("observation_id", "")),
                        "tool_call_id": str(
                            (
                                row.get("provenance", {})
                                if isinstance(row.get("provenance"), dict)
                                else {}
                            ).get("tool_call_id", "")
                        ),
                    },
                )
            )

    observation_lines = _format_observation_lines(retained_observations)
    if observation_lines and working_messages:
        last = dict(working_messages[-1])
        content = str(last.get("content", ""))
        suffix = "\n\nObservations:\n" + "\n".join(observation_lines)
        last["content"] = f"{content}{suffix}"
        working_messages[-1] = last

    for index, message in enumerate(working_messages):
        content = str(message.get("content", ""))
        prospective = running_chars + len(content)
        if prospective <= budget.max_chars:
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
        audit.append(
            TrimAuditRecord(
                record_id=f"trim_{index}",
                kind="message",
                action=TrimAction.DROPPED,
                reason="budget_overflow",
                metadata={"length": len(content)},
            )
        )

    if budget.max_messages is not None and len(kept) > budget.max_messages:
        overflow = len(kept) - budget.max_messages
        dropped = kept[:overflow]
        kept = kept[overflow:]
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
