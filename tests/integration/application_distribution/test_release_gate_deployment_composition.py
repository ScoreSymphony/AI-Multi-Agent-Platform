"""Production-shaped release-gate composition coverage for issue #750."""

from __future__ import annotations

from pathlib import Path

from ai_multi_agent_platform.application_distribution import (
    ApplicationReleaseGateCoordinator,
    StaticReleaseGatePolicy,
)
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment


def test_public_single_node_deployment_wires_canonical_release_gate_authorities(
    tmp_path: Path,
) -> None:
    policy = StaticReleaseGatePolicy()
    deployment = build_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False),
        application_release_gate_policy=policy,
    )

    coordinator = deployment.application_releases.gate_coordinator
    assert isinstance(coordinator, ApplicationReleaseGateCoordinator)
    assert coordinator.policy is policy
    assert coordinator.files is deployment.files
    assert coordinator.verification is deployment.verification
    assert coordinator.verification_access is not None
    assert coordinator.verification_access.verification is deployment.verification
    assert coordinator.evaluations is deployment.evaluation_repository


def test_public_single_node_deployment_installs_fail_closed_empty_gate_policy_by_default(
    tmp_path: Path,
) -> None:
    deployment = build_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
    )

    coordinator = deployment.application_releases.gate_coordinator
    assert isinstance(coordinator, ApplicationReleaseGateCoordinator)
    assert isinstance(coordinator.policy, StaticReleaseGatePolicy)
    assert coordinator.verification is deployment.verification
    assert coordinator.evaluations is deployment.evaluation_repository
