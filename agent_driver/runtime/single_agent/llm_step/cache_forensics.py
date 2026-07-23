"""Prompt-cache break forensics (epic 028 phase E, openclaude-class, урезанная).

Tracks a per-run fingerprint of the cacheable prefix (system + model + tool
schemas) alongside the provider-reported ``cache_read_tokens``. When the
prefix is byte-stable but the cache-read drops sharply (>5% AND >2000 tokens —
openclaude thresholds), the run emits WARNING ``prompt_cache_broken``: the
regression came from TTL expiry or provider instability, not from our request.
A prefix that changed on purpose is not a warning — that break is expected.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from agent_driver.contracts.enums import RuntimeEventType
from agent_driver.runtime.single_agent.lifecycle.events import emit_step_event
from agent_driver.runtime.single_agent.types import RunContext

_MIN_DROP_TOKENS = 2000
_MIN_DROP_RATIO = 0.05
_STATE_KEY = "prompt_cache_state"


def _prefix_fingerprint(request: Any) -> str:
    system_text = ""
    for message in getattr(request, "messages", []) or []:
        if str(getattr(message, "role", "")) in ("system", "ChatRole.SYSTEM"):
            system_text = str(getattr(message, "content", "") or "")
            break
    tools = getattr(request, "tools", None) or []
    try:
        tools_blob = json.dumps(tools, sort_keys=True, ensure_ascii=True)
    except (TypeError, ValueError):
        tools_blob = str(tools)
    raw = "\x00".join(
        (str(getattr(request, "model", "") or ""), system_text, tools_blob)
    )
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def check_prompt_cache_break(
    host: Any, context: RunContext, request: Any, usage: Any
) -> None:
    """Record cache state for this call; warn on an unexpected regression."""
    cache_read = getattr(usage, "cache_read_tokens", None) if usage else None
    if cache_read is None:
        return  # provider reports nothing — honesty rule: no fabricated verdicts
    fingerprint = _prefix_fingerprint(request)
    previous = context.metadata.get(_STATE_KEY)
    context.metadata[_STATE_KEY] = {
        "prefix_sha": fingerprint,
        "cache_read_tokens": int(cache_read),
    }
    if not isinstance(previous, dict):
        return
    prev_read = int(previous.get("cache_read_tokens") or 0)
    drop = prev_read - int(cache_read)
    if drop < _MIN_DROP_TOKENS or prev_read <= 0:
        return
    if (drop / prev_read) < _MIN_DROP_RATIO:
        return
    if previous.get("prefix_sha") != fingerprint:
        return  # prefix changed locally — the break is expected, not a warning
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.WARNING,
        payload={
            "warning": (
                f"Prompt cache regressed with a stable prefix: cache_read "
                f"{prev_read} -> {cache_read} (-{drop} tokens). Likely TTL "
                f"expiry or provider cache instability."
            ),
            "signal_id": "prompt_cache_broken",
            "severity": "info",
            "previous_cache_read_tokens": prev_read,
            "cache_read_tokens": int(cache_read),
            "dropped_tokens": drop,
        },
    )


__all__ = ["check_prompt_cache_break"]
