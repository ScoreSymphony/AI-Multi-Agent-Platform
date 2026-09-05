"""Concrete adapters implementing platform-owned provider contracts."""

from .hermes import (
    HERMES_ADAPTER_ID,
    HERMES_CONFIGURATION_SCHEMA,
    HERMES_PINNED_REVISION,
    HERMES_UPSTREAM_REPOSITORY,
    HermesAdapterConfig,
    HermesAgentMapper,
    HermesBridgeMode,
    HermesCompatibilityStatus,
    HermesDiagnosticsMode,
    HermesHttpResponse,
    HermesHttpTransport,
    HermesOrchestrator,
    HermesRetryBehavior,
    HermesRunSnapshot,
    HermesRuntimeMode,
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
    OpenAICompatibleProviderConfig,
    OpenAICompatibleTransport,
    UrllibOpenAICompatibleTransport,
)
from .openai_compatible_streaming import (
    OpenAICompatibleModelProvider,
    OpenAICompatibleStreamingTransport,
    UrllibOpenAICompatibleStreamingTransport,
)

__all__ = [
    "HERMES_ADAPTER_ID",
    "HERMES_CONFIGURATION_SCHEMA",
    "HERMES_PINNED_REVISION",
    "HERMES_UPSTREAM_REPOSITORY",
    "HermesAdapterConfig",
    "HermesAgentMapper",
    "HermesBridgeMode",
    "HermesCompatibilityStatus",
    "HermesDiagnosticsMode",
    "HermesHttpResponse",
    "HermesHttpTransport",
    "HermesOrchestrator",
    "HermesRetryBehavior",
    "HermesRuntimeMode",
    "HermesRunSnapshot",
    "HttpJsonResponse",
    "LiteLLMMode",
    "LiteLLMModelProvider",
    "LiteLLMProviderConfig",
    "LiteLLMTelemetryMode",
    "OpenAICompatibleModelProvider",
    "OpenAICompatibleProviderConfig",
    "OpenAICompatibleStreamingTransport",
    "OpenAICompatibleTransport",
    "UrllibHermesHttpTransport",
    "UrllibOpenAICompatibleStreamingTransport",
    "UrllibOpenAICompatibleTransport",
]
