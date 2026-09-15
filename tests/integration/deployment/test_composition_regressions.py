from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import HealthStatus
from ai_multi_agent_platform.deployment import build_single_node_deployment
from ai_multi_agent_platform.deployment.config import SingleNodeConfig
from ai_multi_agent_platform.deployment.context_operationalization import install_single_node_context


def _config(tmp_path: Path) -> SingleNodeConfig:
    return SingleNodeConfig(data_dir=tmp_path / "single-node", secure_cookie=False)


def test_public_durable_composition_binds_context_lifecycle_without_kernel_private_state(
    tmp_path: Path,
) -> None:
    deployment = build_single_node_deployment(_config(tmp_path))

    assert deployment.execution_lifecycle.delegate.descriptor.provider_id == (
        "canonical-context-agent-lifecycle"
    )
    source = inspect.getsource(install_single_node_context)
    assert "kernel._lifecycle" not in source
    assert 'getattr(previous_lifecycle, "_inner"' not in source


def test_application_release_gate_is_bound_at_service_construction(tmp_path: Path) -> None:
    deployment = build_single_node_deployment(_config(tmp_path))

    assert deployment.application_releases.gate_coordinator is not None


@pytest.mark.asyncio
async def test_health_readiness_stays_healthy_after_builder_decomposition(tmp_path: Path) -> None:
    deployment = build_single_node_deployment(_config(tmp_path))

    assert await deployment.health_provider.health() is HealthStatus.HEALTHY
    assert deployment.health_provider.service_health.ready is True
