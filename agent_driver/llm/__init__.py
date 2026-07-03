"""LLM gateway package exports."""

from agent_driver.llm.contracts import (
    LlmFinishReason,
    LlmProviderKind,
    LlmRequest,
    LlmResponse,
    LlmStreamEvent,
    ProviderStatus,
    RouterStrategy,
)
from agent_driver.llm.error_classifier import (
    ClassifiedError,
    ProviderErrorReason,
    RecoveryAction,
    classify,
)
from agent_driver.llm.provider_descriptors import (
    ProviderDescriptor,
    ProviderResolutionError,
    ProviderSpec,
    ProviderTransport,
    get_provider_descriptor,
    list_provider_ids,
    register_provider_descriptor,
    resolve_provider,
)
from agent_driver.llm.provider_route_profiles import (
    ProviderPreflightResult,
    ProviderRequestShapePlan,
    ProviderRouteProfile,
    build_provider_request_shape_plan,
    preview_provider_preflight,
    request_shape_policy_summary,
    resolve_openai_compatible_route_profile,
)
from agent_driver.llm.providers import LlmProvider
from agent_driver.llm.providers_impl import (
    FakeProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
)
from agent_driver.llm.router import HealthAwareRouter
from agent_driver.llm.sanitize import sanitize_request_messages, strip_surrogates

__all__ = [
    "sanitize_request_messages",
    "strip_surrogates",
    "ClassifiedError",
    "FakeProvider",
    "HealthAwareRouter",
    "LlmFinishReason",
    "ProviderDescriptor",
    "ProviderPreflightResult",
    "ProviderRequestShapePlan",
    "ProviderErrorReason",
    "ProviderResolutionError",
    "ProviderRouteProfile",
    "ProviderSpec",
    "ProviderTransport",
    "RecoveryAction",
    "classify",
    "build_provider_request_shape_plan",
    "get_provider_descriptor",
    "list_provider_ids",
    "register_provider_descriptor",
    "preview_provider_preflight",
    "request_shape_policy_summary",
    "resolve_openai_compatible_route_profile",
    "resolve_provider",
    "LlmProvider",
    "LlmProviderKind",
    "LlmRequest",
    "LlmResponse",
    "LlmStreamEvent",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "ProviderStatus",
    "RouterStrategy",
]
