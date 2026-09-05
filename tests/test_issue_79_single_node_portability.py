from __future__ import annotations

from pathlib import Path

from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment


def test_single_node_composes_canonical_portability(tmp_path: Path) -> None:
    deployment = build_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "single-node", secure_cookie=False)
    )

    workflow = deployment.control_plane.portability_workflow
    assert workflow is not None
    assert workflow.export_resource_types == (
        "agent",
        "agent_team",
        "evaluation_suite",
        "project",
        "template",
    )

    collections = set(deployment.control_plane.registered_collections)
    commands = set(deployment.control_plane.registered_commands)
    assert {
        "portability-packages",
        "portability-import-previews",
        "portability-import-reports",
    }.issubset(collections)
    assert {
        "portability.export",
        "portability.package.validate",
        "portability.preview",
        "portability.import",
    }.issubset(commands)
