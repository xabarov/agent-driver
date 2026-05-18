"""Deterministic context trimming pipeline."""

from __future__ import annotations

from agent_driver.contracts.context import (
    ContextBudget,
    TrimAuditRecord,
    TrimmedContext,
)
from agent_driver.contracts.enums import TrimAction


def trim_context(  # pylint: disable=too-many-locals
    *,
    budget: ContextBudget,
    prompt_messages: list[dict[str, object]],
    digest_ids: list[str] | None = None,
    artifact_ids: list[str] | None = None,
) -> TrimmedContext:
    """Apply deterministic trimming to prompt messages under char budget."""
    digest_pool = list(digest_ids or [])
    artifact_pool = list(artifact_ids or [])
    kept: list[dict[str, object]] = []
    audit: list[TrimAuditRecord] = []
    running_chars = 0

    for index, message in enumerate(prompt_messages):
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
            "input_messages": len(prompt_messages),
            "kept_messages": len(kept),
            "final_chars": sum(len(str(item.get("content", ""))) for item in kept),
        },
    )
