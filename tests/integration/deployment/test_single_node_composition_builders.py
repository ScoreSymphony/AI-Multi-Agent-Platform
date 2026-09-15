from __future__ import annotations

from pathlib import Path

import pytest

from ai_multi_agent_platform.deployment.composition import (
    LifecycleBinding,
    build_control_plane,
    build_evaluation,
    build_execution,
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


def _config(tmp_path: Path) -> SingleNodeConfig:
    config = SingleNodeConfig(data_dir=tmp_path / "single-node", secure_cookie=False)
    config.prepare_directories()
    return config


def _evaluation_project_id(config: SingleNodeConfig) -> str:
    deployment = build_single_node_deployment(config)
    return next(
        project.id
        for project in deployment.scopes.list_projects()
        if project.name == "Platform Evaluation"
    )


def test_major_builders_compose_from_explicit_dependencies(tmp_path: Path) -> None:
    config = _config(tmp_path)
    storage = build_storage(config)
    observability = build_observability()
    security = build_security(config, observability)
    runtime = build_runtime_services(config, storage, security)
    platform_services = build_platform_services(config, storage, security, runtime)
    repositories = build_repository_foundation(config, storage, security)
    execution = build_execution(
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
        execution,
        verification,
        runtime,
        observability,
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
    control_plane = build_control_plane(
        config,
        storage,
        security,
        observability,
        runtime,
        platform_services,
        repositories,
        repository_runtime,
        execution,
        verification,
        kernel,
        evaluation,
    )
    http = build_http(config, security, control_plane)

    assert repositories.event_ingress is not None
    assert repository_runtime.run_integration is not None
    assert kernel.kernel is not None
    assert verification.completion_authority is not None
    assert control_plane.control_plane is not None
    assert http.app is not None


def test_missing_required_lifecycle_dependency_fails_clearly() -> None:
    with pytest.raises(ValueError, match="lifecycle delegate is required"):
        LifecycleBinding(None)  # type: ignore[arg-type]


def test_optional_adapters_are_not_required_for_reference_composition(tmp_path: Path) -> None:
    deployment = build_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "reference", secure_cookie=False)
    )

    assert deployment.distributed_runtime is None
    assert deployment.secrets is None
    assert deployment.verification_completion is not None


def test_restart_reuses_durable_composition_state(tmp_path: Path) -> None:
    config = SingleNodeConfig(data_dir=tmp_path / "restart", secure_cookie=False)

    first_id = _evaluation_project_id(config)
    restarted_id = _evaluation_project_id(config)

    assert restarted_id == first_id
