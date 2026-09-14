from __future__ import annotations

from ai_multi_agent_platform.control_plane import ControlPlane
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

AUTOMATION_RESOURCES = ("automations", "automation-deliveries")
AUTOMATION_COMMANDS = (
    "automation.create",
    "automation.update",
    "automation.pause",
    "automation.resume",
    "automation.disable",
    "automation.invalidate",
    "automation.revalidate",
    "automation.test",
    "automation.webhook",
    "automation.event",
    "automation.evaluate",
    "automation.retry-delivery",
)


def test_automation_surface_has_one_explicit_owner() -> None:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )

    control_plane = ControlPlane(kernel=kernel, events=repository)

    assert "automation" in control_plane.registered_modules
    for resource in AUTOMATION_RESOURCES:
        assert control_plane.resource_owner(resource) == "automation"
    for command in AUTOMATION_COMMANDS:
        assert control_plane.command_owner(command) == "automation"
