from __future__ import annotations

from pathlib import Path

from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment


def test_single_node_composes_canonical_agent_portability(tmp_path: Path) -> None:
    deployment = build_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "single-node", secure_cookie=False)
    )

    workflow = deployment.control_plane.portability_workflow
    assert workflow is not None
    assert workflow.export_resource_types == ("agent", "agent_team")
    assert "portability-packages" in deployment.control_plane.manifest()["resources"]
    assert "portability-import-previews" in deployment.control_plane.manifest()["resources"]
    assert "portability-import-reports" in deployment.control_plane.manifest()["resources"]
    assert "portability.export" in deployment.control_plane.manifest()["commands"]
    assert "portability.preview" in deployment.control_plane.manifest()["commands"]
    assert "portability.import" in deployment.control_plane.manifest()["commands"]
