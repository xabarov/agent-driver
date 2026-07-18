"""Per-model context-window resolution (epic 017, model-aware context plane).

The runtime's token-pressure thresholds must derive from the model's REAL context
window. Before this module, the engine default (12k) silently applied whenever a
host did not hand-tune the numbers — on a 128k model a retrieval-heavy prompt
tripped compact/blocking far below capacity and the run degraded or failed
(MeetScript chat_v2 incident 2026-07-18).

Resolution order (reference: hermes ``models_dev.py`` registry → family fallbacks;
openclaude ``getContextWindowForModel`` priority chain):

1. Exact catalog entry from :func:`seed_provider_catalogs` (provider hint narrows).
2. Family-based fallback table (conservative, documented values).
3. ``None`` — caller keeps its configured default.

The floor guards against nonsensical catalog rows; live probing / models.dev
snapshot integration is the optional phase D of the epic and intentionally NOT
done here (no network in the runtime by default).
"""

from __future__ import annotations

from functools import lru_cache

# Hard floor for any resolved window: below this, pressure thresholds stop being
# meaningful for an agentic prompt (reference: hermes MINIMUM_CONTEXT_LENGTH=64k
# rejects sessions; we merely clamp, hosts may run small local models).
MIN_RESOLVED_CONTEXT_WINDOW = 16_000

# Conservative family fallbacks, matched as substrings against the lowercased
# model id (vendor prefixes like "deepseek/deepseek-v4-flash" match too). Ordered:
# the FIRST match wins, so more specific families precede generic ones.
_FAMILY_WINDOWS: tuple[tuple[str, int], ...] = (
    ("gpt-5", 400_000),
    ("gpt-4.1", 128_000),
    ("gpt-4o", 128_000),
    ("claude", 200_000),
    ("gemini", 1_000_000),
    ("deepseek-reasoner", 64_000),
    ("deepseek", 128_000),  # v3/v4 chat/flash family
    ("qwen3", 131_072),
    ("qwen", 131_072),
    ("glm", 128_000),
    ("llama", 128_000),
    ("mistral", 128_000),
    ("kimi", 131_072),
)


@lru_cache(maxsize=1)
def _catalog_windows() -> dict[str, int]:
    """Flatten catalog fixtures into {lowercased model id: window}."""
    from agent_driver.llm.provider_catalog import seed_provider_catalogs

    windows: dict[str, int] = {}
    for catalog in seed_provider_catalogs():
        for row in getattr(catalog, "models", ()) or ():
            window = getattr(row, "context_window", None)
            if not isinstance(window, int) or window <= 0:
                continue
            names = [str(getattr(row, "model_id", "") or "")]
            names.extend(str(alias) for alias in (getattr(row, "aliases", ()) or ()))
            for name in names:
                if name.strip():
                    windows[name.strip().lower()] = window
    return windows


def resolve_context_window(model: str | None) -> int | None:
    """Resolve the context window for a model id, or ``None`` when unknown."""
    lowered = str(model or "").strip().lower()
    if not lowered:
        return None
    exact = _catalog_windows().get(lowered)
    if exact is None and "/" in lowered:
        # Vendor-prefixed ids ("deepseek/deepseek-reasoner") match bare catalog rows.
        exact = _catalog_windows().get(lowered.rsplit("/", 1)[-1])
    if exact is not None:
        return max(MIN_RESOLVED_CONTEXT_WINDOW, exact)
    for family, window in _FAMILY_WINDOWS:
        if family in lowered:
            return max(MIN_RESOLVED_CONTEXT_WINDOW, window)
    return None


def provider_model_hint(provider: object) -> str | None:
    """Best-effort model id carried by a provider instance.

    OpenAI-compatible providers keep the configured model privately (``_model``);
    the protocol itself does not expose it, so this is a tolerant probe used only
    for context-window resolution.
    """
    for attribute in ("model", "_model"):
        value = getattr(provider, attribute, None)
        if isinstance(value, str) and value.strip():
            return value
    return None


__all__ = [
    "MIN_RESOLVED_CONTEXT_WINDOW",
    "provider_model_hint",
    "resolve_context_window",
]
