"""Concrete adapters implementing platform-owned provider contracts."""

from .openai_compatible import (
    HttpJsonResponse,
    OpenAICompatibleModelProvider,
    OpenAICompatibleProviderConfig,
    OpenAICompatibleTransport,
    UrllibOpenAICompatibleTransport,
)

__all__ = [
    "HttpJsonResponse",
    "OpenAICompatibleModelProvider",
    "OpenAICompatibleProviderConfig",
    "OpenAICompatibleTransport",
    "UrllibOpenAICompatibleTransport",
]
