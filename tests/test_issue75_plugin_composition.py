from ai_multi_agent_platform.control_plane import ControlPlane
from ai_multi_agent_platform.control_plane.notifications_live import (
    ControlPlane as NotificationControlPlane,
)
from ai_multi_agent_platform.control_plane.plugin_terminal_composition import (
    ControlPlane as PluginTerminalControlPlane,
)
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


def test_public_control_plane_composes_notifications_above_current_plugin_terminal_layer() -> None:
    assert issubclass(ControlPlane, NotificationControlPlane)
    assert issubclass(ControlPlane, PluginTerminalControlPlane)

    repository = InMemoryKernelRepository()
    control_plane = ControlPlane(
        kernel=PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=repository,
        ),
        events=repository,
    )

    assert "notifications" in control_plane.registered_collections
    assert "notification-preferences" in control_plane.registered_collections
    assert "notification.mark-read" in control_plane.registered_commands
    assert hasattr(control_plane, "plugin_registry")
    assert control_plane.plugin_registry is None
