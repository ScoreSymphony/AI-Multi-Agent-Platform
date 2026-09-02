"""Capability-aware extension of the generic ToolProvider seam."""

from __future__ import annotations

from abc import abstractmethod

from ai_multi_agent_platform.contracts.interfaces import ToolProvider

from .types import CapabilityRegistration


class CapabilityToolProvider(ToolProvider):
    """Tool provider that publishes canonical capability registrations."""

    @abstractmethod
    async def capability_registrations(self) -> tuple[CapabilityRegistration, ...]:
        """Return canonical capability bindings owned by the platform layer."""
