"""Concrete adapters implementing platform-owned provider contracts."""

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
    "HttpJsonResponse",
    "LiteLLMMode",
    "LiteLLMModelProvider",
    "LiteLLMProviderConfig",
    "LiteLLMTelemetryMode",
    "OpenAICompatibleModelProvider",
    "OpenAICompatibleProviderConfig",
    "OpenAICompatibleTransport",
    "UrllibOpenAICompatibleTransport",
]
