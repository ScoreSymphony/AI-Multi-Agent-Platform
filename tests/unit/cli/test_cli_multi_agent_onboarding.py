from __future__ import annotations

import argparse
from typing import Any, cast

from ai_multi_agent_platform.cli.onboarding import execute_onboarding


class RecordingClient:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, Any], str | None]] = []

    def post(
        self,
        path: str,
        *,
        body: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        self.posts.append((path, body, idempotency_key))
        return {"type": "multi_agent_first_run_result"}


def test_cli_multi_agent_first_run_uses_same_control_plane_command_as_web() -> None:
    client = RecordingClient()
    args = argparse.Namespace(
        command="run-multi-agent",
        objective="Research, execute, and review.",
        title="First goal",
        project_id="project-test",
        workspace_id="workspace-test",
        idempotency_key="first-run-retry-key",
    )

    result = execute_onboarding(args, cast(Any, client))

    assert result == {"type": "multi_agent_first_run_result"}
    assert client.posts == [
        (
            "/commands/onboarding.run-multi-agent-golden-path",
            {
                "resource_ref": "first-run",
                "objective": "Research, execute, and review.",
                "title": "First goal",
                "project_id": "project-test",
                "workspace_id": "workspace-test",
            },
            "first-run-retry-key",
        )
    ]
