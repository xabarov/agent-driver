"""Route-level provider profiles and deterministic preflight summaries.

This layer is intentionally separate from ``ProviderDescriptor``. Descriptors
describe how to construct a provider; route profiles describe request-shaping
facts and safe policy hints for one provider/model/base-url family.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_driver.llm.contracts import LlmProviderKind, LlmRequest
from agent_driver.llm.provider_capabilities import (
    ProviderCapabilityProfile,
    resolve_openai_compatible_capabilities,
)

_CHECKED_CAPABILITIES: tuple[str, ...] = (
    "supports_tool_calls",
    "supports_forced_tool_choice",
    "supports_strict_json_schema",
    "supports_reasoning",
    "supports_reasoning_details",
    "thinking_extra_body_mode",
    "emits_provider_request_id",
    "supports_streaming",
    "max_token_field",
    "context_window",
    "max_output_tokens",
)


@dataclass(frozen=True, slots=True)
class ProviderRouteProfile:
    """Redaction-safe route facts for one OpenAI-compatible provider path."""

    profile_id: str
    provider_id: str
    provider_kind: str
    base_url_family: str
    model_id: str
    supports_tool_calls: bool
    supports_forced_tool_choice: bool
    supports_strict_json_schema: bool
    supports_reasoning: bool
    supports_reasoning_details: bool
    thinking_extra_body_mode: str
    emits_provider_request_id: bool
    supports_streaming: bool
    max_token_field: str
    context_window: int | None = None
    max_output_tokens: int | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_metadata(self) -> dict[str, Any]:
        """Return a JSON-safe, redaction-safe profile payload."""
        data: dict[str, Any] = {
            "profile_id": self.profile_id,
            "provider_id": self.provider_id,
            "provider_kind": self.provider_kind,
            "base_url_family": self.base_url_family,
            "model_id": self.model_id,
            "supports_tool_calls": self.supports_tool_calls,
            "supports_forced_tool_choice": self.supports_forced_tool_choice,
            "supports_strict_json_schema": self.supports_strict_json_schema,
            "supports_reasoning": self.supports_reasoning,
            "supports_reasoning_details": self.supports_reasoning_details,
            "thinking_extra_body_mode": self.thinking_extra_body_mode,
            "emits_provider_request_id": self.emits_provider_request_id,
            "supports_streaming": self.supports_streaming,
            "max_token_field": self.max_token_field,
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
        }
        if self.notes:
            data["notes"] = list(self.notes)
        return data


@dataclass(frozen=True, slots=True)
class ProviderPreflightResult:
    """Deterministic provider preflight/preview result with no live request."""

    provider_name: str
    provider_kind: str
    model: str
    route_profile: ProviderRouteProfile
    capability_profile: ProviderCapabilityProfile
    request_shape: dict[str, Any]
    status: str
    checked_capabilities: tuple[str, ...] = _CHECKED_CAPABILITIES
    downgrades: tuple[str, ...] = ()

    def to_metadata(self) -> dict[str, Any]:
        """Return redaction-safe support/status metadata."""
        return {
            "provider": {
                "name": self.provider_name,
                "kind": self.provider_kind,
                "model": self.model,
            },
            "route_profile_id": self.route_profile.profile_id,
            "base_url_family": self.route_profile.base_url_family,
            "capability_profile": self.capability_profile.to_metadata(),
            "preflight": {
                "status": self.status,
                "checked_capabilities": list(self.checked_capabilities),
                "downgrades": list(self.downgrades),
            },
            "request_shape": self.request_shape,
            "provider_request_ids": [],
            "usage": {"cost_usd_estimate": None},
            "latency_ms": None,
            "redaction": {
                "safe_by_default": True,
                "contains_api_key": False,
                "contains_raw_base_url": False,
            },
        }


def resolve_openai_compatible_route_profile(
    *,
    provider_name: str,
    base_url: str,
    model: str,
    provider_kind: str | LlmProviderKind = LlmProviderKind.OPENAI_COMPATIBLE,
    capability_profile: ProviderCapabilityProfile | None = None,
) -> ProviderRouteProfile:
    """Infer a deterministic route profile for an OpenAI-compatible endpoint."""
    capability = capability_profile or resolve_openai_compatible_capabilities(
        provider_name=provider_name,
        base_url=base_url,
        model=model,
    )
    provider_id = capability.provider_id or _normalize_id(provider_name)
    family = capability.base_url_family
    model_id = capability.model_id
    model_l = model_id.lower()
    kind = provider_kind.value if isinstance(provider_kind, LlmProviderKind) else str(provider_kind)
    notes = list(capability.notes)

    supports_forced_tool_choice = family == "openai" or provider_id == "openai"
    supports_strict_json_schema = family in {"openai", "vllm"}
    emits_provider_request_id = family in {"openai", "openrouter"}
    thinking_mode = _thinking_extra_body_mode(
        provider_id=provider_id,
        family=family,
        supports_reasoning=capability.supports_reasoning,
    )

    if not supports_forced_tool_choice:
        notes.append("forced_named_tool_choice_should_not_be_assumed")
    if not supports_strict_json_schema:
        notes.append("strict_json_schema_should_be_downgraded_or_validated")
    if emits_provider_request_id:
        notes.append("provider_request_id_header_expected_when_available")

    return ProviderRouteProfile(
        profile_id=_profile_id(
            provider_id=provider_id,
            base_url_family=family,
            model_id=model_id,
        ),
        provider_id=provider_id or "openai_compatible",
        provider_kind=kind,
        base_url_family=family,
        model_id=model_id,
        supports_tool_calls=capability.supports_tool_calls,
        supports_forced_tool_choice=supports_forced_tool_choice,
        supports_strict_json_schema=supports_strict_json_schema,
        supports_reasoning=capability.supports_reasoning,
        supports_reasoning_details=capability.supports_reasoning_details,
        thinking_extra_body_mode=thinking_mode,
        emits_provider_request_id=emits_provider_request_id,
        supports_streaming=capability.supports_streaming,
        max_token_field=_max_token_field(model_l=model_l, family=family),
        context_window=_context_window_hint(model_l=model_l, family=family),
        max_output_tokens=capability.max_output_tokens,
        notes=tuple(dict.fromkeys(notes)),
    )


def preview_provider_preflight(
    *,
    provider_name: str,
    provider_kind: str | LlmProviderKind,
    model: str,
    route_profile: ProviderRouteProfile,
    capability_profile: ProviderCapabilityProfile,
    request: LlmRequest | None = None,
) -> ProviderPreflightResult:
    """Build a request-shape preview without contacting the provider."""
    request_shape, downgrades = request_shape_policy_summary(
        route_profile=route_profile,
        request=request,
    )
    status = "degraded" if downgrades else "ok"
    kind = provider_kind.value if isinstance(provider_kind, LlmProviderKind) else str(provider_kind)
    return ProviderPreflightResult(
        provider_name=provider_name,
        provider_kind=kind,
        model=model,
        route_profile=route_profile,
        capability_profile=capability_profile,
        request_shape=request_shape,
        status=status,
        downgrades=tuple(downgrades),
    )


def request_shape_policy_summary(
    *,
    route_profile: ProviderRouteProfile,
    request: LlmRequest | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Return stable request-shape hints and any deterministic downgrades."""
    downgrades: list[str] = []
    forced_tool = _forced_tool_choice_name(request.tool_choice) if request else None
    has_tools = bool(request and request.tools)
    response_format = request.response_format if request else None

    tool_choice_policy = "provider_default"
    if forced_tool:
        if route_profile.supports_forced_tool_choice:
            tool_choice_policy = "forced_tool_choice_supported"
        else:
            tool_choice_policy = "forced_tool_choice_downgraded_to_auto"
            downgrades.append("forced_tool_choice")
    elif request and request.tool_choice == "none":
        tool_choice_policy = "tools_disabled"
    elif has_tools:
        tool_choice_policy = "auto_tool_choice"

    response_format_policy = "provider_default"
    if isinstance(response_format, dict):
        response_format_policy = _response_format_policy(
            response_format=response_format,
            route_profile=route_profile,
            downgrades=downgrades,
        )

    reasoning_policy = (
        f"reasoning_supported:{route_profile.thinking_extra_body_mode}"
        if route_profile.supports_reasoning
        else "reasoning_not_supported"
    )

    return (
        {
            "supports_tool_calls": route_profile.supports_tool_calls,
            "supports_forced_tool_choice": (
                route_profile.supports_forced_tool_choice
            ),
            "supports_strict_json_schema": (
                route_profile.supports_strict_json_schema
            ),
            "supports_reasoning": route_profile.supports_reasoning,
            "supports_reasoning_details": (
                route_profile.supports_reasoning_details
            ),
            "thinking_extra_body_mode": route_profile.thinking_extra_body_mode,
            "emits_provider_request_id": route_profile.emits_provider_request_id,
            "supports_streaming": route_profile.supports_streaming,
            "max_token_field": route_profile.max_token_field,
            "context_window": route_profile.context_window,
            "max_output_tokens": route_profile.max_output_tokens,
            "tool_choice_policy": tool_choice_policy,
            "response_format_policy": response_format_policy,
            "reasoning_policy": reasoning_policy,
        },
        downgrades,
    )


def _response_format_policy(
    *,
    response_format: dict[str, Any],
    route_profile: ProviderRouteProfile,
    downgrades: list[str],
) -> str:
    kind = response_format.get("type")
    if kind == "json_object":
        return "json_object_supported"
    if kind != "json_schema":
        return "provider_extension_passthrough"
    schema = response_format.get("json_schema")
    strict = isinstance(schema, dict) and schema.get("strict") is True
    if strict and route_profile.supports_strict_json_schema:
        return "strict_json_schema_supported"
    if strict:
        downgrades.append("strict_json_schema")
        return "strict_json_schema_downgraded_to_json_object_or_validation"
    if route_profile.supports_strict_json_schema:
        return "json_schema_supported"
    downgrades.append("json_schema")
    return "json_schema_downgraded_to_json_object_or_validation"


def _thinking_extra_body_mode(
    *,
    provider_id: str,
    family: str,
    supports_reasoning: bool,
) -> str:
    if family == "openrouter":
        return "openrouter_reasoning"
    if family in {"local", "vllm"}:
        return "chat_template_kwargs.enable_thinking"
    if family == "openai" and supports_reasoning:
        return "native_reasoning_controls"
    if provider_id in {"deepseek", "kimi"}:
        return "provider_extra_body.reasoning"
    return "none"


def _max_token_field(*, model_l: str, family: str) -> str:
    if family == "openai" and model_l.startswith(("o1", "o3", "o4")):
        return "max_completion_tokens"
    return "max_tokens"


def _context_window_hint(*, model_l: str, family: str) -> int | None:
    if "gpt-5" in model_l:
        return 400_000
    if "gpt-4" in model_l:
        return 128_000
    if "qwen3" in model_l:
        return 262_144 if family == "openrouter" else 32_768
    return None


def _profile_id(*, provider_id: str, base_url_family: str, model_id: str) -> str:
    model_slug = _normalize_id(model_id).replace("/", "__")
    return f"{provider_id}:{base_url_family}:{model_slug}"


def _forced_tool_choice_name(tool_choice: object | None) -> str | None:
    if not isinstance(tool_choice, dict):
        return None
    if tool_choice.get("type") == "tool":
        name = tool_choice.get("name")
        return name if isinstance(name, str) and name else None
    if tool_choice.get("type") == "function":
        function = tool_choice.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            return name if isinstance(name, str) and name else None
    return None


def _normalize_id(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


__all__ = [
    "ProviderPreflightResult",
    "ProviderRouteProfile",
    "preview_provider_preflight",
    "request_shape_policy_summary",
    "resolve_openai_compatible_route_profile",
]
