"""Concrete adapters implementing platform-owned provider contracts."""

from .hermes import (
    HERMES_ADAPTER_ID,
    HERMES_PINNED_REVISION,
    HERMES_UPSTREAM_REPOSITORY,
    HermesAdapterConfig,
    HermesAgentMapper,
    HermesHttpResponse,
    HermesHttpTransport,
    HermesOrchestrator,
    HermesRunSnapshot,
    UrllibHermesHttpTransport,
)
from .litellm import (
    LiteLLMMode,
    LiteLLMModelProvider,
    LiteLLMProviderConfig,
    LiteLLMTelemetryMode,
)
from .openai_compatible import (
    HttpJsonResponse,
    OpenAICompatibleModelProvider,
    OpenAICompatibleProviderConfig,
    OpenAICompatibleTransport,
    UrllibOpenAICompatibleTransport,
)

__all__ = [
    "HERMES_ADAPTER_ID",
    "HERMES_PINNED_REVISION",
    "HERMES_UPSTREAM_REPOSITORY",
    "HermesAdapterConfig",
    "HermesAgentMapper",
    "HermesHttpResponse",
    "HermesHttpTransport",
    "HermesOrchestrator",
    "HermesRunSnapshot",
    "HttpJsonResponse",
    "LiteLLMMode",
    "LiteLLMModelProvider",
    "LiteLLMProviderConfig",
    "LiteLLMTelemetryMode",
    "OpenAICompatibleModelProvider",
    "OpenAICompatibleProviderConfig",
    "OpenAICompatibleTransport",
    "UrllibHermesHttpTransport",
    "UrllibOpenAICompatibleTransport",
]
