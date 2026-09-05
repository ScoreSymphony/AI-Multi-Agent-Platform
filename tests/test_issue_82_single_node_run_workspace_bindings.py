from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.workspaces import RunWorkspaceBinding


def test_single_node_uses_restart_safe_run_workspace_binding_repository(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        first = build_single_node_deployment(config)

        assert first.control_plane.run_workspace_bindings is first.run_workspace_bindings

        binding = RunWorkspaceBinding(
            run_id=new_id("run"),
            task_id=new_id("task"),
            workspace_id=new_id("workspace"),
            workspace_snapshot_id=new_id("workspace_snapshot"),
            content_checksum="a" * 64,
        )
        await first.run_workspace_bindings.bind(binding)

        restarted = build_single_node_deployment(config)
        assert restarted.control_plane.run_workspace_bindings is restarted.run_workspace_bindings
        persisted = await restarted.run_workspace_bindings.get(binding.run_id)
        assert persisted is not None
        assert persisted.same_target(binding)

    asyncio.run(scenario())
