from __future__ import annotations

import asyncio

from ai_multi_agent_platform.contracts import HealthStatus, ProviderContract, ProviderDescriptor
from ai_multi_agent_platform.control_plane.health import ControlPlaneHealth
from ai_multi_agent_platform.observability import AggregatedHealthProvider, ProviderHealthDependency


class _Provider(ProviderContract):
    def __init__(self, provider_id: str, status: HealthStatus) -> None:
        self.provider_id = provider_id
        self.status = status

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id=self.provider_id,
            provider_type="test",
            health=self.status,
            available=True,
        )

    async def health(self) -> HealthStatus:
        return self.status


def test_control_plane_projects_optional_dependency_degradation_without_losing_readiness() -> None:
    async def scenario() -> None:
        aggregate = AggregatedHealthProvider(
            (
                ProviderHealthDependency(
                    _Provider("optional-search", HealthStatus.UNAVAILABLE),
                    required=False,
                    max_retries=0,
                ),
            )
        )
        payload = await ControlPlaneHealth((aggregate,)).health()

        assert payload["ready"] is True
        assert payload["readiness_state"] == "degraded"
        provider = payload["providers"][0]
        assert provider["status"] == "degraded"
        assert provider["readiness_state"] == "degraded"
        dependency = provider["dependencies"][0]
        assert dependency["name"] == "optional-search"
        assert dependency["state"] == "unavailable"
        assert dependency["required"] is False
        assert dependency["error_code"] == "unavailable"
        assert dependency["failure_count"] == 1

    asyncio.run(scenario())


def test_control_plane_projects_required_dependency_failure_as_unready() -> None:
    async def scenario() -> None:
        aggregate = AggregatedHealthProvider(
            (
                ProviderHealthDependency(
                    _Provider("required-persistence", HealthStatus.UNAVAILABLE),
                    required=True,
                    max_retries=0,
                ),
            )
        )
        payload = await ControlPlaneHealth((aggregate,)).health()

        assert payload["ready"] is False
        assert payload["readiness_state"] == "unavailable"
        dependency = payload["providers"][0]["dependencies"][0]
        assert dependency["required"] is True
        assert dependency["state"] == "unavailable"

    asyncio.run(scenario())
