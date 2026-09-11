from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_multi_agent_platform.adapters.single_node_app import build_default_single_node_deployment
from ai_multi_agent_platform.application_distribution import (
    DeterministicGateCheck,
    ReleaseGateKind,
    StaticReleaseGatePolicy,
)
from ai_multi_agent_platform.configuration import ConfigurationError
from ai_multi_agent_platform.deployment import (
    load_application_release_gate_policy,
    load_single_node_config,
)


def _write_policy(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "requirements": [
                    {
                        "name": "checksum",
                        "kind": "deterministic",
                        "target_id": "linux-x64",
                        "deterministic_check": "file_checksum",
                    },
                    {
                        "name": "release-review",
                        "kind": "verification",
                        "target_id": "linux-x64",
                        "verification_policy_id": "verification_policy_release",
                        "verification_policy_version": 2,
                        "verification_stage_id": "package-smoke",
                    },
                    {
                        "name": "regression",
                        "kind": "evaluation",
                        "target_id": "linux-x64",
                        "evaluation_suite_id": "release-suite",
                        "evaluation_suite_version": "3",
                    },
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def test_release_gate_policy_catalog_maps_names_to_canonical_mechanisms(tmp_path: Path) -> None:
    policy = load_application_release_gate_policy(_write_policy(tmp_path / "release-gates.json"))

    assert isinstance(policy, StaticReleaseGatePolicy)
    checksum = policy.requirement("checksum")
    assert checksum is not None
    assert checksum.kind is ReleaseGateKind.DETERMINISTIC
    assert checksum.deterministic_check is DeterministicGateCheck.FILE_CHECKSUM

    verification = policy.requirement("release-review")
    assert verification is not None
    assert verification.kind is ReleaseGateKind.VERIFICATION
    assert verification.verification_policy_id == "verification_policy_release"
    assert verification.verification_policy_version == 2
    assert verification.verification_stage_id == "package-smoke"

    evaluation = policy.requirement("regression")
    assert evaluation is not None
    assert evaluation.kind is ReleaseGateKind.EVALUATION
    assert evaluation.evaluation_suite_id == "release-suite"
    assert evaluation.evaluation_suite_version == "3"


def test_release_gate_policy_catalog_rejects_incomplete_typed_requirement(tmp_path: Path) -> None:
    path = tmp_path / "invalid-release-gates.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "requirements": [
                    {
                        "name": "broken",
                        "kind": "deterministic",
                        "target_id": "linux-x64",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="deterministic release gates require"):
        load_application_release_gate_policy(path)


def test_shipped_single_node_profile_loads_explicit_release_gate_policy(tmp_path: Path) -> None:
    policy_path = _write_policy(tmp_path / "release-gates.json")
    config = load_single_node_config(
        {
            "AI_MAP_DATA_DIR": str(tmp_path / "platform"),
            "AI_MAP_SECURE_COOKIE": "false",
            "AI_MAP_APPLICATION_RELEASE_GATE_POLICY": str(policy_path),
        }
    )

    assert config.application_release_gate_policy == policy_path
    deployment = build_default_single_node_deployment(config)
    coordinator = deployment.application_releases.gate_coordinator
    assert coordinator is not None
    checksum = coordinator.policy.requirement("checksum")
    assert checksum is not None
    assert checksum.deterministic_check is DeterministicGateCheck.FILE_CHECKSUM
    assert coordinator.verification is deployment.verification
    assert coordinator.evaluations is deployment.evaluation_repository
