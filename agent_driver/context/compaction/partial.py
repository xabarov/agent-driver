"""Partial/reactive compaction helpers preserving bounded recency."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PartialCompactionOutput:
    """Compacted context using prefix/suffix preserving strategy."""

    prompt_messages: list[dict[str, str]]
    retained_observation_ids: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


def build_partial_compaction(
    *,
    messages: list[dict[str, str]],
    retain_recent_messages: int = 6,
    prefix_mode: bool = True,
) -> PartialCompactionOutput:
    """Summarize one slice while retaining bounded untouched context."""
    if not messages:
        return PartialCompactionOutput(prompt_messages=[])
    if len(messages) <= retain_recent_messages + 1:
        return PartialCompactionOutput(
            prompt_messages=list(messages),
            metadata={"strategy": "no_op", "reason": "below_threshold"},
        )
    if prefix_mode:
        prefix = messages[:-retain_recent_messages]
        suffix = messages[-retain_recent_messages:]
        summary = _summarize_slice(prefix)
        prompt_messages = [summary, *suffix]
        strategy = "prefix_summary"
        summarized_count = len(prefix)
    else:
        prefix = messages[:retain_recent_messages]
        suffix = messages[retain_recent_messages:]
        summary = _summarize_slice(suffix)
        prompt_messages = [*prefix, summary]
        strategy = "suffix_summary"
        summarized_count = len(suffix)
    return PartialCompactionOutput(
        prompt_messages=prompt_messages,
        metadata={
            "strategy": strategy,
            "summarized_message_count": summarized_count,
            "retained_message_count": len(prompt_messages),
        },
    )


def _summarize_slice(messages: list[dict[str, str]]) -> dict[str, str]:
    rows: list[str] = []
    for item in messages:
        role = str(item.get("role", "user"))
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        rows.append(f"- {role}: {content[:160]}")
        if len(rows) >= 10:
            break
    return {
        "role": "system",
        "content": "Partial compaction summary:\n" + ("\n".join(rows) if rows else "- (empty)"),
    }


__all__ = ["PartialCompactionOutput", "build_partial_compaction"]
