"""Concrete adapters implementing platform-owned provider contracts."""

from .litellm import LiteLLMMode, LiteLLMModelProvider, LiteLLMProviderConfig
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
    "OpenAICompatibleModelProvider",
    "OpenAICompatibleProviderConfig",
    "OpenAICompatibleTransport",
    "UrllibOpenAICompatibleTransport",
]
