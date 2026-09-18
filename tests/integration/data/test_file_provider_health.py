from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.contracts import HealthStatus
from ai_multi_agent_platform.data import LocalFileProvider
from ai_multi_agent_platform.observability import (
    AggregatedHealthProvider,
    ProviderHealthDependency,
    ReadinessState,
)


def test_local_file_persistence_health_fails_required_readiness_closed(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = tmp_path / "files"
        provider = LocalFileProvider(root, tmp_path / "files.sqlite3")
        health = AggregatedHealthProvider(
            (
                ProviderHealthDependency(
                    provider,
                    required=True,
                    name="files",
                    max_retries=0,
                ),
            )
        )

        assert await provider.health() is HealthStatus.HEALTHY
        assert await health.health() is HealthStatus.HEALTHY
        assert health.service_health.readiness is ReadinessState.READY

        root.rmdir()

        assert await provider.health() is HealthStatus.UNAVAILABLE
        assert await health.health() is HealthStatus.UNAVAILABLE
        assert health.service_health.ready is False
        assert health.service_health.readiness is ReadinessState.UNAVAILABLE
        dependency = health.service_health.dependencies[0]
        assert dependency.name == "files"
        assert dependency.required is True
        assert dependency.error_code == "unavailable"

    asyncio.run(scenario())
