"""Pre-send message hygiene shared by the main loop and the aux/side path.

Epic 043 B: an empty non-final user/assistant turn makes strict providers reject
the whole request ("messages must have non-empty content"), and an interrupted or
degenerate turn can leave exactly that shape mid-history. This is the single owner
of the repair — imported by both the main LLM-call step and the aux completion
substrate so a padded prefix can never be the reason either path is rejected.

Lives in the ``llm`` layer (not ``runtime``) so the aux substrate can share it
without a layering inversion.
"""

from __future__ import annotations

from typing import Any

from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmRequest
from agent_driver.llm.reasoning_hygiene import strip_leading_think_block

# Placeholder for a non-final turn that would otherwise be empty. A benign single
# dot keeps role alternation valid without reading as intent.
_EMPTY_NON_FINAL_PLACEHOLDER = "."

# Metadata keys whose presence means a turn carries real payload even when
# ``content`` is empty — a "designed-empty" turn that MUST NOT be rewritten
# (assistant tool-call carriers, provider reasoning echo, audio/attachment rides).
_PAYLOAD_METADATA_KEYS = (
    "tool_calls",
    "reasoning_details",
    "reasoning",
    "output_audio",
    "attachments",
)


def _message_has_payload(message: ChatMessage) -> bool:
    """Return whether a message carries any real payload (content or metadata)."""
    if isinstance(message.content, str) and message.content.strip():
        return True
    # Tool-result rows are keyed by tool_call_id and legitimately terse.
    if message.role is ChatRole.TOOL or message.tool_call_id:
        return True
    metadata = message.metadata if isinstance(message.metadata, dict) else {}
    return any(metadata.get(key) for key in _PAYLOAD_METADATA_KEYS)


def repair_empty_non_final_messages(request: Any) -> Any:
    """Pad empty non-final user/assistant turns before sending to the provider.

    Only NON-FINAL turns with no payload are padded (copy-on-write); the final
    turn and any designed-empty carrier (tool_calls, reasoning echo, tool rows)
    are never touched. Idempotent — a run-through request returns unchanged.
    """
    if not isinstance(request, LlmRequest):
        return request
    messages = request.messages
    if len(messages) < 2:
        return request
    last_index = len(messages) - 1
    changed = False
    repaired: list[ChatMessage] = []
    for index, message in enumerate(messages):
        if (
            index != last_index
            and message.role in (ChatRole.USER, ChatRole.ASSISTANT)
            and not _message_has_payload(message)
        ):
            metadata = (
                dict(message.metadata) if isinstance(message.metadata, dict) else {}
            )
            metadata["empty_non_final_repaired"] = True
            repaired.append(
                message.model_copy(
                    update={
                        "content": _EMPTY_NON_FINAL_PLACEHOLDER,
                        "metadata": metadata,
                    }
                )
            )
            changed = True
        else:
            repaired.append(message)
    if not changed:
        return request
    return request.model_copy(update={"messages": repaired})


def quarantine_inline_reasoning(request: Any) -> tuple[Any, int]:
    """Strip inline CoT from assistant history turns; return ``(request, n)``.

    Epic 043 D: an assistant turn exposing its own chain-of-thought (a leading
    ``<think>`` block persisted before the 043-A ingestion fix, or replayed by a
    host that stored it) reads to a provider classifier as a prefill/reasoning
    injection and can make every later call come back empty — an unescapable
    poisoned prefix the empty-recovery ladder cannot answer around. This is the
    bounded quarantine step: sanitize each assistant turn's content (least
    destructive — the turn is kept, only the CoT removed) and report how many
    turns changed so the caller retries at most once and only when it mattered.
    """
    if not isinstance(request, LlmRequest):
        return request, 0
    changed = 0
    repaired: list[ChatMessage] = []
    for message in request.messages:
        if message.role is ChatRole.ASSISTANT and isinstance(message.content, str):
            cleaned, stripped = strip_leading_think_block(message.content)
            if stripped:
                changed += 1
                repaired.append(message.model_copy(update={"content": cleaned}))
                continue
        repaired.append(message)
    if not changed:
        return request, 0
    return request.model_copy(update={"messages": repaired}), changed


__all__ = ["quarantine_inline_reasoning", "repair_empty_non_final_messages"]
