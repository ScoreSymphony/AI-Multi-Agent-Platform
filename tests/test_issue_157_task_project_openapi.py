from __future__ import annotations

import asyncio

from ai_multi_agent_platform.control_plane import (
    TASK_PROJECT_BULK_MOVE_COMMAND,
    TASK_PROJECT_MOVE_COMMAND,
    ControlPlane,
    ControlPlaneHTTP,
    HTTPRequest,
    build_openapi,
)
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


def _control_plane() -> ControlPlane:
    events = InMemoryKernelRepository()
    return ControlPlane(
        kernel=PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=events,
        ),
        events=events,
    )


def test_project_move_commands_are_canonical_not_extension_registrations() -> None:
    control_plane = _control_plane()

    assert TASK_PROJECT_MOVE_COMMAND not in control_plane.registered_commands
    assert TASK_PROJECT_BULK_MOVE_COMMAND not in control_plane.registered_commands


def test_project_move_commands_have_explicit_openapi_contracts() -> None:
    specification = build_openapi()
    paths = specification["paths"]

    assert f"/api/v1/commands/{TASK_PROJECT_MOVE_COMMAND}" in paths
    assert f"/api/v1/commands/{TASK_PROJECT_BULK_MOVE_COMMAND}" in paths
    assert specification["x-task-project-reassignment"] == {
        "commands": [TASK_PROJECT_MOVE_COMMAND, TASK_PROJECT_BULK_MOVE_COMMAND],
        "event": "task.project_reassigned",
        "historical_scope": "retained",
        "future_execution_scope": "destination_project_id",
        "bulk_atomic": False,
        "connected_bulk_moves": "rejected_without_multi_stream_atomic_commit",
    }


def test_runtime_openapi_uses_the_same_project_move_contract() -> None:
    async def scenario() -> None:
        response = await ControlPlaneHTTP(_control_plane()).handle(
            HTTPRequest(method="GET", path="/api/v1/openapi.json")
        )
        assert response.status == 200
        assert isinstance(response.body, dict)
        paths = response.body["paths"]
        assert f"/api/v1/commands/{TASK_PROJECT_MOVE_COMMAND}" in paths
        assert f"/api/v1/commands/{TASK_PROJECT_BULK_MOVE_COMMAND}" in paths
        assert response.body["x-task-project-reassignment"]["event"] == (
            "task.project_reassigned"
        )

    asyncio.run(scenario())
