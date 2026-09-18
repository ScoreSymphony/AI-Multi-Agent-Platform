"""Provider-health aggregation for the Control Plane."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.interfaces import ProviderContract
from ai_multi_agent_platform.contracts.types import JsonValue


class ControlPlaneHealth:
    """Aggregate provider readiness without owning provider lifecycle."""

    def __init__(self, providers: tuple[ProviderContract, ...]) -> None:
        self._providers = providers

    async def health(self) -> dict[str, JsonValue]:
        providers: list[JsonValue] = []
        ready = True
        for provider in self._providers:
            status = await provider.health()
            descriptor = provider.descriptor
            if not descriptor.available or status.value == "unavailable":
                ready = False
            resource: dict[str, JsonValue] = {
                "id": descriptor.provider_id,
                "type": descriptor.provider_type,
                "status": status.value,
                "available": descriptor.available,
            }
            diagnostics = getattr(provider, "health_diagnostics", ())
            if diagnostics:
                resource["diagnostics"] = list(diagnostics)
            providers.append(resource)
        return {
            "status": "healthy",
            "ready": ready,
            "api_version": "v1",
            "providers": providers,
        }
