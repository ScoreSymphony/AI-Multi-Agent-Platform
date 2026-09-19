from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

from ai_multi_agent_platform.deployment.composition import (
    build_control_plane,
    build_evaluation,
    build_execution,
    build_health,
    build_http,
    build_kernel,
    build_observability,
    build_platform_services,
    build_repository_foundation,
    build_repository_runtime,
    build_runtime_services,
    build_security,
    build_storage,
    build_verification,
)
from ai_multi_agent_platform.deployment.config import SingleNodeConfig
from ai_multi_agent_platform.deployment.single_node import build_single_node_deployment


def test_major_single_node_builders_compose_without_optional_adapters(tmp_path: Path) -> None:
    config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)

    storage = build_storage(config)
    observability = build_observability()
    security = build_security(config, observability)
    runtime = build_runtime_services(config, storage, security)
    platform_services = build_platform_services(config, storage, security, runtime)
    repositories = build_repository_foundation(config, storage, security)
    execution = build_execution(
        config,
        storage,
        security,
        observability,
        runtime,
        repositories,
    )
    verification = build_verification(config)
    kernel = build_kernel(
        config,
        storage,
        observability,
        runtime,
        execution,
        verification,
    )
    repository_runtime = build_repository_runtime(storage, repositories, kernel.kernel)
    evaluation = build_evaluation(
        config,
        storage,
        security,
        observability,
        runtime,
        execution,
        kernel,
    )
    health = build_health(storage, execution, observability)
    control_plane = build_control_plane(
        config,
        storage,
        security,
        observability,
        runtime,
        platform_services,
        repositories,
        repository_runtime,
        verification,
        kernel,
        evaluation,
        health,
    )
    http = build_http(config, security, control_plane)

    assert security.secrets is None
    assert kernel.kernel is not None
    assert repository_runtime.repository_run_integration is not None
    assert "agents" in control_plane.control_plane.registered_collections
    assert http.app is not None


def test_single_node_root_is_small_orchestration_and_restart_safe(tmp_path: Path) -> None:
    source = inspect.getsource(build_single_node_deployment)
    assert len(source.splitlines()) <= 150

    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "restart", secure_cookie=False)
        first = build_single_node_deployment(config)
        first.bootstrap_admin("admin", "composition-builder-password")
        first_smoke = await first.run_reference_smoke()

        restarted = build_single_node_deployment(config)
        restarted.bootstrap_admin("admin", "composition-builder-password")
        second_smoke = await restarted.run_reference_smoke()

        assert restarted.verification_completion is not None
        assert second_smoke.task_id == first_smoke.task_id
        assert second_smoke.run_id == first_smoke.run_id

    asyncio.run(scenario())
