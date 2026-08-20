"""Tiered compression of OLD tool-result bulk (epic 035 phase A).

Port of openclaude ``compressToolHistory.ts`` adapted to our message shape (a tool
result is a ``ChatMessage(role=TOOL)`` — not a structured block list). For STATELESS
/ no-prompt-cache providers (a fallback to a cheap backend rewrites the whole prefix
every call), the bulk text of OLD tool results is shrunk by tier so the history
doesn't saturate the window — while newer results stay full-fidelity.

Three tiers, sized from the effective window (openclaude ``getTiers``): the most
recent tool results stay full; a mid band is truncated to a bounded preview with a
length-carrying marker; everything older collapses to a ~15-token stub encoding the
tool name so the model can re-invoke to recover the omitted output.

Guarantees (verbatim from the reference):
* **Structure inviolate** — only TOOL-message ``content`` shrinks; no message is
  added or removed, so every tool_use/tool_result pairing survives.
* **Idempotent** — already-stubbed content is terminal (never re-stubbed); a
  truncated mid-tier result upgrades to a stub only when it ages into the old tier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.context.token_estimation import estimate_tokens
from agent_driver.context.tool_content_shrink import shrink_json_tool_content

MID_MAX_CHARS = 2000

_TRUNCATION_MARKER_RE = re.compile(r"\n\[…truncated (\d+) chars from tool history\]$")
_STUB_MARKER_RE = re.compile(r"^\[tool result → \d+ chars omitted\]$")


@dataclass(frozen=True, slots=True)
class _Tiers:
    recent: int
    mid: int


def get_tiers(effective_window: int) -> _Tiers:
    """Tool-result counts kept full (recent) / truncated (mid), scaled from window.

    recent tier stays under ~25% of the window at full fidelity; recent+mid under
    ~50% (bounded bulk); everything older collapses to a stub.
    """
    if effective_window < 16_000:
        return _Tiers(2, 3)
    if effective_window < 32_000:
        return _Tiers(3, 5)
    if effective_window < 64_000:
        return _Tiers(4, 8)
    if effective_window < 128_000:
        return _Tiers(5, 10)
    if effective_window < 256_000:
        return _Tiers(8, 15)
    if effective_window < 500_000:
        return _Tiers(12, 25)
    return _Tiers(25, 50)


def _truncation_marker(omitted: int) -> str:
    return f"\n[…truncated {omitted} chars from tool history]"


def _stub(original_len: int) -> str:
    return f"[tool result → {original_len} chars omitted]"


def _original_length(content: str) -> int:
    """Recover the pre-shrink length whether the content is fresh/truncated/stub."""
    stub = _STUB_MARKER_RE.match(content.strip())
    if stub:
        digits = re.search(r"→ (\d+) chars omitted", content)
        return int(digits.group(1)) if digits else len(content)
    trunc = _TRUNCATION_MARKER_RE.search(content)
    if trunc:
        return (len(content) - len(trunc.group(0))) + int(trunc.group(1))
    return len(content)


def compress_tool_history(
    messages: list[ChatMessage],
    *,
    effective_window: int,
) -> tuple[list[ChatMessage], dict[str, Any]]:
    """Shrink OLD tool-result bulk by tier. Returns ``(messages, audit)``.

    Idempotent and structure-preserving: only TOOL-message content changes. Fresh
    content in the recent tier is untouched; the mid tier is truncated to
    ``MID_MAX_CHARS`` (skipped if already ≤); everything older is stubbed. Audit
    carries raw-free counts + estimated chars/tokens saved.
    """
    tiers = get_tiers(effective_window)
    # Index tool-result messages oldest→newest so tier boundaries count from newest.
    tool_indices = [i for i, m in enumerate(messages) if m.role == ChatRole.TOOL]
    if not tool_indices:
        return messages, {"activated": False, "tool_results": 0}

    total = len(tool_indices)
    result = list(messages)
    truncated = 0
    stubbed = 0
    chars_saved = 0
    for rank, msg_index in enumerate(reversed(tool_indices)):
        # rank 0 = newest tool result
        msg = result[msg_index]
        content = msg.content or ""
        if rank < tiers.recent:
            continue  # recent tier: full fidelity
        original = _original_length(content)
        if rank < tiers.recent + tiers.mid:
            # mid tier: truncate to a bounded preview (skip if already small/stub).
            if _STUB_MARKER_RE.match(content.strip()):
                continue  # already terminal
            if len(content) <= MID_MAX_CHARS or _TRUNCATION_MARKER_RE.search(content):
                continue  # already ≤ cap or already truncated
            # Structure-preserving: a serialized-JSON tool result is shrunk by
            # truncating its long string leaves (still-valid JSON), never a raw
            # content[:N] slice that would cut the JSON mid-structure. Non-JSON prose
            # keeps the plain slice + marker.
            json_shrunk = shrink_json_tool_content(content)
            if json_shrunk is not None:
                if json_shrunk == content:
                    continue  # valid JSON, nothing over the leaf budget
                new_content = json_shrunk
            else:
                new_content = content[:MID_MAX_CHARS].rstrip() + _truncation_marker(
                    original - MID_MAX_CHARS
                )
            chars_saved += len(content) - len(new_content)
            result[msg_index] = msg.model_copy(update={"content": new_content})
            truncated += 1
        else:
            # old tier: collapse to a stub (skip if already a stub).
            if _STUB_MARKER_RE.match(content.strip()):
                continue
            new_content = _stub(original)
            chars_saved += len(content) - len(new_content)
            result[msg_index] = msg.model_copy(update={"content": new_content})
            stubbed += 1

    activated = bool(truncated or stubbed)
    return result, {
        "activated": activated,
        "tool_results": total,
        "truncated": truncated,
        "stubbed": stubbed,
        "chars_saved": chars_saved,
        "estimated_tokens_saved": estimate_tokens(chars_saved),
    }


__all__ = ["compress_tool_history", "get_tiers", "MID_MAX_CHARS"]
