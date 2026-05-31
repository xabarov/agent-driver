"""Lightweight provider/model capability profiles.

The profile is intentionally descriptive, not a router. Runtime code can use it
for diagnostics, request metadata, UI warnings, and future guarded retries
without changing provider selection semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class ProviderCapabilityProfile:
    """Best-effort provider/model capability snapshot."""

    provider_id: str
    model_id: str
    base_url_family: str = "unknown"
    supports_streaming: bool = True
    supports_tool_calls: bool = True
    supports_parallel_tool_calls: bool = True
    supports_reasoning: bool = False
    supports_reasoning_details: bool = False
    requires_reasoning_echo: bool = False
    supports_json_schema: bool = False
    supports_native_web: bool = False
    max_output_tokens: int | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_metadata(self) -> dict[str, Any]:
        """Return a JSON-serializable metadata payload."""
        data: dict[str, Any] = {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "base_url_family": self.base_url_family,
            "supports_streaming": self.supports_streaming,
            "supports_tool_calls": self.supports_tool_calls,
            "supports_parallel_tool_calls": self.supports_parallel_tool_calls,
            "supports_reasoning": self.supports_reasoning,
            "supports_reasoning_details": self.supports_reasoning_details,
            "requires_reasoning_echo": self.requires_reasoning_echo,
            "supports_json_schema": self.supports_json_schema,
            "supports_native_web": self.supports_native_web,
        }
        if self.max_output_tokens is not None:
            data["max_output_tokens"] = self.max_output_tokens
        if self.notes:
            data["notes"] = list(self.notes)
        return data


def resolve_openai_compatible_capabilities(
    *,
    provider_name: str,
    base_url: str,
    model: str,
) -> ProviderCapabilityProfile:
    """Infer capabilities for an OpenAI-compatible provider endpoint."""
    normalized_provider = _normalize_id(provider_name)
    normalized_model = model.strip()
    model_l = normalized_model.lower()
    family = _base_url_family(base_url)
    notes: list[str] = []

    provider_id = normalized_provider
    if family == "openrouter":
        provider_id = "openrouter"
    elif family == "openai":
        provider_id = "openai"
    elif family in {"local", "vllm"}:
        provider_id = normalized_provider or family

    supports_reasoning = _model_supports_reasoning(model_l)
    supports_reasoning_details = provider_id == "openrouter" and supports_reasoning
    requires_reasoning_echo = _requires_reasoning_echo(provider_id, model_l, base_url)
    supports_json_schema = provider_id in {"openai", "openrouter"} or family in {
        "local",
        "vllm",
    }
    max_output_tokens = _max_output_tokens_hint(model_l)

    if provider_id == "openrouter":
        notes.append("openrouter_reasoning_details_may_be_present")
    if requires_reasoning_echo:
        notes.append("reasoning_content_echo_required_on_tool_turns")
    if family == "unknown":
        notes.append("capabilities_are_safe_defaults_for_unknown_openai_compatible")

    return ProviderCapabilityProfile(
        provider_id=provider_id or "openai_compatible",
        model_id=normalized_model,
        base_url_family=family,
        supports_reasoning=supports_reasoning,
        supports_reasoning_details=supports_reasoning_details,
        requires_reasoning_echo=requires_reasoning_echo,
        supports_json_schema=supports_json_schema,
        max_output_tokens=max_output_tokens,
        notes=tuple(notes),
    )


def _normalize_id(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def _base_url_family(base_url: str) -> str:
    host = (urlparse(base_url).hostname or "").lower()
    if not host:
        return "unknown"
    if host.endswith("openrouter.ai"):
        return "openrouter"
    if host.endswith("api.openai.com"):
        return "openai"
    if "vllm" in host:
        return "vllm"
    if host in {"localhost", "127.0.0.1", "0.0.0.0"}:
        return "local"
    if "deepseek" in host:
        return "deepseek"
    if "moonshot" in host or "kimi" in host:
        return "kimi"
    return "unknown"


def _model_supports_reasoning(model_l: str) -> bool:
    return any(
        marker in model_l
        for marker in (
            "gpt-5",
            "o1",
            "o3",
            "o4",
            "qwen3",
            "deepseek-v4",
            "deepseek-r1",
            "deepseek-reasoner",
            "glm-z1",
            "grok-4",
        )
    )


def _requires_reasoning_echo(provider_id: str, model_l: str, base_url: str) -> bool:
    base_l = base_url.lower()
    return (
        provider_id in {"deepseek", "kimi"}
        or "deepseek" in base_l
        or "moonshot" in base_l
        or "kimi" in model_l
    )


def _max_output_tokens_hint(model_l: str) -> int | None:
    if "gpt-5" in model_l:
        return 128_000
    if "qwen3" in model_l:
        return 32_768
    if "gpt-4" in model_l:
        return 16_384
    return None


__all__ = ["ProviderCapabilityProfile", "resolve_openai_compatible_capabilities"]
