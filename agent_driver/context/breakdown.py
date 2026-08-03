"""Per-category context composition estimate (epic 044 A).

``estimate_token_pressure`` gives the aggregate ``chars // 4`` estimate that drives
the compaction trigger, but not *what* fills the window. Hosts (RAG-heavy MeetScript /
excel-ai) need the breakdown to render a ``/context`` view and to target retrieval:
which category — system prompt, tool schemas, tool outputs, runtime scaffolding, or the
conversation itself — is actually consuming the budget.

The estimate uses the SAME ``chars // 4`` heuristic as the compaction threshold, and the
reported total (``total_chars // 4``) equals the number the trigger sees — so the UI number
and the trigger never disagree (the hermes ``context_breakdown`` invariant). Per-category
token counts are display approximations (each is its own ``chars // 4``), and need not sum
exactly to the authoritative total because integer division rounds per category.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.scaffolding import is_scaffolding

# Category order is stable so a rendered breakdown is byte-stable across turns.
CONTEXT_BREAKDOWN_CATEGORIES = (
    "system_prompt",
    "tool_definitions",
    "tool_results",
    "scaffolding",
    "conversation",
    "message_metadata",
)

_CHARS_PER_TOKEN = 4


def _message_chars(message: Any) -> int:
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    return len(str(content or ""))


def _message_role(message: Any) -> str:
    role = getattr(message, "role", None)
    if role is None and isinstance(message, dict):
        role = message.get("role")
    return str(getattr(role, "value", role) or "")


def _json_chars(value: Any) -> int:
    try:
        return len(
            json.dumps(
                value,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        )
    except (TypeError, ValueError):
        return len(str(value))


def _message_metadata_chars(message: Any) -> int:
    metadata = getattr(message, "metadata", None)
    if metadata is None and isinstance(message, dict):
        metadata = message.get("metadata")
    return _json_chars(metadata) if metadata else 0


def _category_of(message: Any) -> str:
    """Classify a message into one breakdown category (order matters)."""
    role = _message_role(message)
    if role == ChatRole.SYSTEM.value:
        return "system_prompt"
    if is_scaffolding(message):
        return "scaffolding"
    if role == ChatRole.TOOL.value:
        return "tool_results"
    return "conversation"


def _tool_definition_chars(tools: Iterable[Any] | None) -> int:
    if not tools:
        return 0
    total = 0
    for tool in tools:
        total += _json_chars(tool)
    return total


def estimate_context_breakdown(
    messages: Iterable[Any],
    *,
    tools: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Return a per-category ``chars // 4`` breakdown of the next request.

    ``messages`` may be ``ChatMessage`` objects or serialized dicts. ``tools`` is the
    request's tool-definition list (schemas). The ``total_tokens`` is authoritative and
    equals what the compaction trigger sees for the same content.
    """
    chars: dict[str, int] = {cat: 0 for cat in CONTEXT_BREAKDOWN_CATEGORIES}
    for message in messages:
        chars[_category_of(message)] += _message_chars(message)
        chars["message_metadata"] += _message_metadata_chars(message)
    chars["tool_definitions"] += _tool_definition_chars(tools)

    total_chars = sum(chars.values())
    categories = {
        cat: {"chars": chars[cat], "tokens": chars[cat] // _CHARS_PER_TOKEN}
        for cat in CONTEXT_BREAKDOWN_CATEGORIES
    }
    return {
        "categories": categories,
        "total_chars": total_chars,
        # Authoritative: matches estimate_token_pressure's (chars // 4) — UI == trigger.
        "total_tokens": total_chars // _CHARS_PER_TOKEN,
    }


__all__ = ["CONTEXT_BREAKDOWN_CATEGORIES", "estimate_context_breakdown"]
