"""Public deployment composition acceptance coverage for #651."""

from __future__ import annotations

from pathlib import Path

from ai_multi_agent_platform.context import (
    CONTEXT_BUNDLE_COLLECTION,
    CONTEXT_RUN_BINDING_COLLECTION,
)
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.handoffs import (
    HANDOFF_COLLECTION,
    HANDOFF_CONSUMPTION_COLLECTION,
    ProductionHandoffRuntime,
    SQLiteHandoffRepository,
)


def test_public_single_node_deployment_owns_durable_handoff_runtime(tmp_path: Path) -> None:
    config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
    deployment = build_single_node_deployment(config)

    assert isinstance(deployment.handoffs.runtime, ProductionHandoffRuntime)
    assert isinstance(deployment.handoffs.repository, SQLiteHandoffRepository)
    assert deployment.handoffs.runtime.repository is deployment.handoffs.repository
    assert deployment.handoffs.runtime.service is deployment.handoffs.service
    assert deployment.handoffs.runtime.coordinated is deployment.handoffs.coordinated
    assert deployment.handoffs.context_runtime.runtime is deployment.agent_runtime
    assert deployment.handoffs.consumer_requirements.agents is deployment.agents.repository
    assert deployment.handoffs.references.verification is deployment.verification_runtime.evidence
    assert deployment.handoffs.references.authorization is deployment.approval_gate.provider

    assert (config.database_dir / "handoffs.sqlite3").exists()
    assert (config.database_dir / "research.sqlite3").exists()
    assert (config.database_dir / "skills.json").parent == config.database_dir

    required_collections = {
        HANDOFF_COLLECTION,
        HANDOFF_CONSUMPTION_COLLECTION,
        CONTEXT_BUNDLE_COLLECTION,
        CONTEXT_RUN_BINDING_COLLECTION,
    }
    assert required_collections.issubset(deployment.control_plane.registered_collections)
    assert not any(
        command.startswith("handoff") for command in deployment.control_plane.registered_commands
    )


def test_public_single_node_restart_reopens_same_handoff_state_paths(tmp_path: Path) -> None:
    config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
    first = build_single_node_deployment(config)
    second = build_single_node_deployment(config)

    assert first.handoffs.repository._path == second.handoffs.repository._path
    assert (
        first.handoffs.context_bundle_repository.path
        == second.handoffs.context_bundle_repository.path
    )
    assert (
        first.handoffs.context_binding_repository.path
        == second.handoffs.context_binding_repository.path
    )
    assert first.handoffs.skill_repository.path == second.handoffs.skill_repository.path
    assert first.handoffs.research_repository.path == second.handoffs.research_repository.path
