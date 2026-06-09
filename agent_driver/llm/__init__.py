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
    "ProviderErrorReason",
    "ProviderResolutionError",
    "ProviderSpec",
    "ProviderTransport",
    "RecoveryAction",
    "classify",
    "get_provider_descriptor",
    "list_provider_ids",
    "register_provider_descriptor",
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
