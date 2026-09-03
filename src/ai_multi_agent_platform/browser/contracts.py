"""Replaceable browser-provider contract behind canonical capabilities."""

from __future__ import annotations

from abc import abstractmethod

from ai_multi_agent_platform.capabilities.provider import CapabilityToolProvider
from ai_multi_agent_platform.contracts.types import OperationContext

from .models import BrowserProviderFeatures, BrowserSessionRef


class BrowserProvider(CapabilityToolProvider):
    """Browser-specific provider seam without binding the platform to one engine."""

    @property
    @abstractmethod
    def browser_features(self) -> BrowserProviderFeatures:
        """Return normalized browser-engine feature metadata."""

    @abstractmethod
    async def get_session(
        self,
        session_id: str,
        context: OperationContext,
    ) -> BrowserSessionRef:
        """Resolve one canonical session reference if it is visible in this scope."""

    @abstractmethod
    async def close_session(self, session_id: str, context: OperationContext) -> None:
        """Close one canonical browser session without exposing private backend IDs."""
