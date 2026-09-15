from __future__ import annotations

from ai_multi_agent_platform.control_plane import ControlPlane
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

TASK_MANAGEMENT_COMMANDS = (
    "task-management.update",
    "task-management.bulk-update",
)
TASK_PROJECT_REASSIGNMENT_COMMANDS = (
    "task.project.move",
    "task.project.bulk-move",
)


def test_task_management_commands_have_one_explicit_owner() -> None:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )

    control_plane = ControlPlane(kernel=kernel, events=repository)

    assert "task-management" in control_plane.registered_modules
    for command in TASK_MANAGEMENT_COMMANDS:
        assert command in control_plane.registered_commands
        assert control_plane.command_owner(command) == "task-management"


def test_task_project_reassignment_commands_have_one_explicit_owner() -> None:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )

    control_plane = ControlPlane(kernel=kernel, events=repository)

    assert "task-project-reassignment" in control_plane.registered_modules
    for command in TASK_PROJECT_REASSIGNMENT_COMMANDS:
        assert command in control_plane.registered_commands
        assert control_plane.command_owner(command) == "task-project-reassignment"
