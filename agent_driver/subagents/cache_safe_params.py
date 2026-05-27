"""Phase 12 H19 — prompt-cache sharing across SubagentGroup children.

When a SubagentGroup spawns N child agents, the children share the
parent's IMMUTABLE prefix (system prompt, tool catalog, model id,
message preamble). Providers that support prompt caching (Anthropic
ephemeral, OpenAI prompt-cache-enabled deployments, vLLM kv-cache) can
return ``cache_read_input_tokens`` for calls 2..N when the prefix is
bytewise identical — typically 3-4× input-token savings on a 4-way
fan-out.

This module exposes:

* :class:`CacheSafeParams` — the immutable prefix slice. Hashed for
  fast equality checks; the hash is part of each child's
  ``app_metadata["_cache_safe_params"]`` so the host can verify
  by-reference sharing in transit.
* :func:`compute_cache_safe_params` — derive params from a parent run
  input. Stable serialization (sorted JSON) ensures children spawned
  in different orders produce identical hashes.
* :func:`apply_to_child_run_input` — attach the params hash to a
  child ``AgentRunInput.app_metadata`` so the provider adapter can
  emit cache hints when it builds the request.
* :func:`provider_cache_hint_for` — translate (params, provider_kind)
  into the provider-specific request hint shape. The actual request
  modification lives in each provider adapter (H19b follow-up); this
  module is the source of truth for what hint to emit.

State boundaries (CRITICAL):

* IMMUTABLE / SHARED: system prompt, tool manifest list, model id,
  parent message prefix. These are part of CacheSafeParams.
* MUTABLE / PER-CHILD: workspace cwd, abortable cancellation token,
  child-specific arguments, child's own message buffer. These are
  NEVER part of CacheSafeParams and are constructed per-child by the
  executor.

Sharing the mutable state across children would cause cross-child
mutation bleed-through (e.g. one child's file write visible to a
peer's read). Sharing the immutable state is provider-side
deduplication and saves tokens.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from agent_driver.contracts.runtime import AgentRunInput


CACHE_SAFE_METADATA_KEY = "_cache_safe_params"


@dataclass(frozen=True, slots=True)
class CacheSafeParams:
    """Immutable parent-prefix slice safe to share across children.

    Fields:
        system_prompt_hash: stable hash of the system prompt (sha256
            hex digest of the UTF-8 encoded text); empty when no
            system prompt is configured.
        tools_signature: stable hash of the registered tool list
            (sha256 hex of sorted JSON-encoded manifest names+
            descriptions); empty when no tools.
        model: canonical model id (``run_input.model_role`` resolved
            value) — siblings with different model ids cannot share a
            cache slot because the provider keys cache by model.
        parent_prefix_hash: stable hash of the parent's
            input/messages preamble; siblings spawned from the same
            parent share this.
        cache_key: the composite hash used by the provider adapter
            to assert by-reference sharing across requests in transit
            (sha256 of the other 4 fields concatenated).
    """

    system_prompt_hash: str
    tools_signature: str
    model: str
    parent_prefix_hash: str
    cache_key: str

    def to_metadata(self) -> dict[str, str]:
        """Compact dict suitable for ``app_metadata`` round-trip."""
        return {
            "system_prompt_hash": self.system_prompt_hash,
            "tools_signature": self.tools_signature,
            "model": self.model,
            "parent_prefix_hash": self.parent_prefix_hash,
            "cache_key": self.cache_key,
        }

    @classmethod
    def from_metadata(cls, raw: dict[str, Any]) -> "CacheSafeParams":
        """Reconstruct from ``app_metadata`` dict; missing fields
        coerce to empty strings (the hash will then differ from a
        valid params, which is correct — invalid metadata cannot
        accidentally claim cache hit)."""
        return cls(
            system_prompt_hash=str(raw.get("system_prompt_hash", "")),
            tools_signature=str(raw.get("tools_signature", "")),
            model=str(raw.get("model", "")),
            parent_prefix_hash=str(raw.get("parent_prefix_hash", "")),
            cache_key=str(raw.get("cache_key", "")),
        )


def _stable_hash(parts: list[str]) -> str:
    """SHA-256 over ``\n``-joined input. Deterministic, collision-safe."""
    blob = "\n".join(parts).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _hash_text(text: str | None) -> str:
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_tool_manifests(manifests: list[Any] | None) -> str:
    """Stable signature for a tool catalog.

    Hashes the SORTED list of ``(name, description)`` tuples — order
    of registration doesn't affect the signature (so children that
    register tools in different orders still match).
    """
    if not manifests:
        return ""
    items: list[tuple[str, str]] = []
    for manifest in manifests:
        name = getattr(manifest, "name", None) or ""
        description = getattr(manifest, "description", None) or ""
        items.append((str(name), str(description)))
    items.sort()
    encoded = json.dumps(items, ensure_ascii=True, sort_keys=True)
    return _hash_text(encoded)


def _hash_message_prefix(input_str: str | None, messages: list[Any] | None) -> str:
    """Stable hash of the parent's input/messages preamble.

    The preamble is what the LLM sees as the user-side prefix
    (NOT including any child-specific args). Hashing covers the
    ``input`` string and the role+content of each preamble message.
    """
    parts: list[str] = []
    if input_str:
        parts.append(f"input::{input_str}")
    if messages:
        for msg in messages:
            role = getattr(msg, "role", "")
            content = getattr(msg, "content", "")
            try:
                content_str = json.dumps(content, ensure_ascii=True, sort_keys=True, default=str)
            except (TypeError, ValueError):
                content_str = str(content)
            parts.append(f"{role}::{content_str}")
    if not parts:
        return ""
    return _stable_hash(parts)


def compute_cache_safe_params(
    *,
    run_input: AgentRunInput,
    system_prompt: str | None = None,
    tools: list[Any] | None = None,
    model: str | None = None,
) -> CacheSafeParams:
    """Derive CacheSafeParams from a parent run input + ambient state.

    Args:
        run_input: the parent's ``AgentRunInput`` (we hash its
            ``input`` + ``messages`` as the prefix).
        system_prompt: the resolved system prompt text; ``None`` when
            the host doesn't pre-render one (the provider adapter may
            still emit a default — that's OK, just means
            ``system_prompt_hash`` is empty).
        tools: the registered ``ToolManifest`` list as seen by the
            agent. Order-independent — the hash sorts by ``(name,
            description)``.
        model: canonical model id (resolves ``run_input.model_role``).
            Empty when unknown; siblings with empty model id can still
            share a cache slot among themselves but never with a
            sibling that has a non-empty model id.
    """
    system_prompt_hash = _hash_text(system_prompt)
    tools_signature = _hash_tool_manifests(tools)
    parent_prefix_hash = _hash_message_prefix(run_input.input, run_input.messages)
    model_id = (model or "").strip()
    cache_key = _stable_hash(
        [
            system_prompt_hash,
            tools_signature,
            model_id,
            parent_prefix_hash,
        ]
    )
    return CacheSafeParams(
        system_prompt_hash=system_prompt_hash,
        tools_signature=tools_signature,
        model=model_id,
        parent_prefix_hash=parent_prefix_hash,
        cache_key=cache_key,
    )


def apply_to_child_run_input(
    child_input: AgentRunInput, params: CacheSafeParams
) -> AgentRunInput:
    """Attach CacheSafeParams metadata to a child's ``AgentRunInput``.

    Returns a new ``AgentRunInput`` with ``app_metadata`` updated;
    leaves all other fields unchanged. The provider adapter reads
    this metadata when constructing the LLM request and emits the
    appropriate cache hint (Anthropic ``cache_control``, OpenAI
    ``prompt_cache``, vLLM auto-detect = no-op).
    """
    merged_metadata = dict(child_input.app_metadata or {})
    merged_metadata[CACHE_SAFE_METADATA_KEY] = params.to_metadata()
    return child_input.model_copy(update={"app_metadata": merged_metadata})


# Provider kinds the cache-hint helper understands. Hosts pass the
# string id of their provider; unknown kinds get a no-op hint (still
# safe — caching just won't apply for that request).
ProviderKind = Literal[
    "anthropic", "openai_compatible", "vllm", "ollama", "fake", "unknown"
]


@dataclass(frozen=True, slots=True)
class ProviderCacheHint:
    """Provider-specific cache hint shape.

    Fields:
        kind: provider kind the hint was generated for.
        request_overrides: dict to merge into the LLM request body.
            Examples:
              Anthropic — ``{"system": [{"type": "text", "text": "...",
                "cache_control": {"type": "ephemeral"}}]}``.
              OpenAI-compat — ``{"extra_body": {"prompt_cache": true}}``.
              vLLM — empty (kv-cache is automatic).
        message_cache_breakpoint: when provider supports per-message
            cache breakpoints (Anthropic), the index AFTER WHICH the
            cache should freeze. ``None`` for providers without
            per-message breakpoints.
    """

    kind: str
    request_overrides: dict[str, Any] = field(default_factory=dict)
    message_cache_breakpoint: int | None = None


def provider_cache_hint_for(
    *,
    params: CacheSafeParams,
    provider_kind: str,
) -> ProviderCacheHint:
    """Return the provider-specific cache hint for these params.

    Phase 12 H19 — this is a pure helper. The provider adapter is
    responsible for actually merging the ``request_overrides`` into
    the outgoing request. That wiring lives in H19b (provider-side).
    """
    kind = (provider_kind or "unknown").strip().lower()
    if kind == "anthropic":
        # Anthropic uses per-message cache_control markers. We tag the
        # system prompt as ephemeral so a 5-minute TTL applies; child
        # calls 2..N within that window hit the cache.
        return ProviderCacheHint(
            kind="anthropic",
            request_overrides={
                "_cache_safe_params": params.to_metadata(),
                "_cache_control_target": "system_prompt",
            },
            # System prompt is index 0; cache breakpoint AFTER it.
            message_cache_breakpoint=0,
        )
    if kind in ("openai_compatible", "openai", "vllm"):
        # Both lanes share the OpenAI Chat-Completions shape. OpenAI's
        # own auto-cache is opaque (no opt-in needed); vLLM kv-cache
        # is also automatic but we surface the params so observability
        # can correlate cache hits.
        return ProviderCacheHint(
            kind=kind,
            request_overrides={
                "extra_body": {"_cache_safe_params": params.to_metadata()},
            },
        )
    if kind == "ollama":
        # Ollama doesn't support prompt caching in a portable way; emit
        # the params for observability but no request override.
        return ProviderCacheHint(
            kind="ollama",
            request_overrides={},
        )
    return ProviderCacheHint(kind="unknown", request_overrides={})


__all__ = [
    "CACHE_SAFE_METADATA_KEY",
    "CacheSafeParams",
    "ProviderCacheHint",
    "apply_to_child_run_input",
    "compute_cache_safe_params",
    "provider_cache_hint_for",
]
