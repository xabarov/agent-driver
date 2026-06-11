"""N5: compress recorded trajectories to a token budget for training datasets.

A captured :class:`Trajectory` can be far larger than what a fine-tuning /
distillation pipeline wants per example — long tool transcripts in the middle
dwarf the parts that carry the training signal: the opening turns (the task +
initial reasoning) and the closing turns (the final answer). This mirrors
hermes' ``trajectory_compressor``: keep the first ``keep_first`` and last
``keep_last`` messages intact, replace the elided middle with a single marker
turn, and — only if the preserved turns still overflow — truncate their content
to fit. The compression is recorded under ``metadata["compression"]`` so a
consumer can tell a trimmed example from a whole one.

Token counts use the runtime's ``chars // 4`` heuristic (see
``context/token_pressure.py``) so estimates stay consistent and dependency-free.
"""

from __future__ import annotations

from typing import Any

from agent_driver.batch.contracts import Trajectory

_CHARS_PER_TOKEN = 4
_ELISION_ROLE = "system"


def _estimate_tokens(text: str) -> int:
    """Approximate token count for one string (ceil of chars/4)."""
    return (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


def _messages_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(_estimate_tokens(str(m.get("content") or "")) for m in messages)


def _truncate_content(content: str, max_chars: int) -> str:
    """Keep the head and tail of ``content``, eliding the middle with a marker."""
    if max_chars <= 0 or len(content) <= max_chars:
        return content if len(content) <= max_chars else content[: max(0, max_chars)]
    marker = " …[truncated]… "
    if max_chars <= len(marker):
        return content[:max_chars]
    keep = max_chars - len(marker)
    head = keep // 2
    tail = keep - head
    return content[:head] + marker + content[len(content) - tail :]


def _elision_marker(dropped: list[dict[str, Any]]) -> dict[str, Any]:
    tokens = _messages_tokens(dropped)
    return {
        "role": _ELISION_ROLE,
        "content": (
            f"[... {len(dropped)} turn(s) / ~{tokens} tokens elided "
            "for training budget ...]"
        ),
    }


def compress_trajectory(
    trajectory: Trajectory,
    *,
    max_tokens: int,
    keep_first: int = 1,
    keep_last: int = 1,
) -> Trajectory:
    """Return a trajectory whose ``messages`` fit within ``max_tokens``.

    Preserves the first ``keep_first`` and last ``keep_last`` messages; the
    middle is replaced by one elision marker. If the preserved turns still
    exceed the budget their content is truncated (head+tail kept). Returns the
    input unchanged when it already fits or has nothing to elide and truncate.
    Records what happened under ``metadata["compression"]``.
    """
    if max_tokens <= 0:
        raise ValueError("max_tokens must be > 0")
    if keep_first < 0 or keep_last < 0:
        raise ValueError("keep_first and keep_last must be >= 0")

    messages = trajectory.messages
    original_tokens = _messages_tokens(messages)
    if original_tokens <= max_tokens:
        return trajectory

    count = len(messages)
    elided = 0
    if keep_first + keep_last < count:
        head = messages[:keep_first]
        tail = messages[count - keep_last :] if keep_last else []
        dropped = (
            messages[keep_first : count - keep_last]
            if keep_last
            else messages[keep_first:]
        )
        elided = len(dropped)
        result = [*head, _elision_marker(dropped), *tail]
    else:
        # Nothing to elide (all turns are preserved) — fall through to truncation.
        result = list(messages)

    # If still over budget, truncate the content of non-marker turns to fit.
    truncated = False
    if _messages_tokens(result) > max_tokens:
        truncated = _truncate_messages_to_fit(result, max_tokens=max_tokens)

    final_tokens = _messages_tokens(result)
    compression = {
        "original_message_count": count,
        "kept_message_count": len(result),
        "elided_message_count": elided,
        "original_tokens": original_tokens,
        "final_tokens": final_tokens,
        "max_tokens": max_tokens,
        "content_truncated": truncated,
    }
    return trajectory.model_copy(
        update={
            "messages": result,
            "metadata": {**trajectory.metadata, "compression": compression},
        }
    )


def _truncate_messages_to_fit(
    messages: list[dict[str, Any]], *, max_tokens: int
) -> bool:
    """Truncate message contents in place to fit ``max_tokens``; return if changed.

    Splits the budget evenly across the (non-elision) turns and trims each that
    overflows its share, keeping the head and tail of every turn.
    """
    truncatable = [
        index
        for index, message in enumerate(messages)
        if not _is_elision_marker(message)
    ]
    if not truncatable:
        return False
    marker_tokens = sum(
        _estimate_tokens(str(messages[i].get("content") or ""))
        for i in range(len(messages))
        if i not in set(truncatable)
    )
    budget = max(0, max_tokens - marker_tokens)
    per_turn_chars = (budget // max(1, len(truncatable))) * _CHARS_PER_TOKEN
    changed = False
    for index in truncatable:
        content = str(messages[index].get("content") or "")
        if _estimate_tokens(content) > per_turn_chars // _CHARS_PER_TOKEN:
            messages[index] = {
                **messages[index],
                "content": _truncate_content(content, per_turn_chars),
            }
            changed = True
    return changed


def _is_elision_marker(message: dict[str, Any]) -> bool:
    content = str(message.get("content") or "")
    return content.startswith("[... ") and "elided for training budget" in content


def compress_trajectories(
    trajectories: list[Trajectory],
    *,
    max_tokens: int,
    keep_first: int = 1,
    keep_last: int = 1,
) -> list[Trajectory]:
    """Compress each trajectory to ``max_tokens`` (see :func:`compress_trajectory`)."""
    return [
        compress_trajectory(
            traj, max_tokens=max_tokens, keep_first=keep_first, keep_last=keep_last
        )
        for traj in trajectories
    ]


__all__ = ["compress_trajectories", "compress_trajectory"]
