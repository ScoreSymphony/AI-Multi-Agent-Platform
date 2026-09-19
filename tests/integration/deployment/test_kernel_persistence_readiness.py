from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.contracts import HealthStatus
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.observability import ReadinessState


def test_missing_kernel_persistence_fails_single_node_readiness_closed(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        deployment = build_single_node_deployment(config)

        assert await deployment.health_provider.health() is HealthStatus.HEALTHY
        assert deployment.health_provider.service_health.readiness is ReadinessState.READY

        kernel_path = config.database_dir / "kernel.sqlite3"
        assert kernel_path.is_file()
        kernel_path.unlink()

        assert await deployment.health_provider.health() is HealthStatus.UNAVAILABLE
        assert deployment.health_provider.service_health.ready is False
        assert deployment.health_provider.service_health.readiness is ReadinessState.UNAVAILABLE
        dependency = next(
            item
            for item in deployment.health_provider.service_health.dependencies
            if item.name == "kernel-persistence"
        )
        assert dependency.required is True
        assert dependency.state is ReadinessState.UNAVAILABLE
        assert dependency.error_code == "backend_error"
        assert kernel_path.exists() is False

    asyncio.run(scenario())
