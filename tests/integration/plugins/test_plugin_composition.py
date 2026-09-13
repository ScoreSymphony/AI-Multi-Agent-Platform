import asyncio

from ai_multi_agent_platform.control_plane import (
    NOTIFICATION_COLLECTION,
    NOTIFICATION_COMMANDS,
    NOTIFICATION_PREFERENCE_COLLECTION,
    ControlPlane,
    ControlPlaneHTTP,
    HTTPRequest,
)
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

    # Notifications remain built-in private resources, not generic extension collections.
    assert NOTIFICATION_COLLECTION not in control_plane.registered_collections
    assert NOTIFICATION_PREFERENCE_COLLECTION not in control_plane.registered_collections
    assert all(
        command not in control_plane.registered_commands for command in NOTIFICATION_COMMANDS
    )
    assert hasattr(control_plane, "plugin_registry")
    assert control_plane.plugin_registry is None


def test_public_manifest_and_openapi_publish_notifications_as_canonical_resources() -> None:
    async def scenario() -> None:
        repository = InMemoryKernelRepository()
        control_plane = ControlPlane(
            kernel=PlatformKernel(
                orchestrator=FakeOrchestrator(),
                lifecycle=FakeLifecycleBackend(),
                repository=repository,
            ),
            events=repository,
        )
        http = ControlPlaneHTTP(control_plane)

        manifest = await http.handle(HTTPRequest(method="GET", path="/api/v1"))
        assert manifest.status == 200
        assert isinstance(manifest.body, dict)
        resources = manifest.body["resources"]
        commands = manifest.body["commands"]
        assert isinstance(resources, list)
        assert isinstance(commands, list)
        assert NOTIFICATION_COLLECTION in resources
        assert NOTIFICATION_PREFERENCE_COLLECTION in resources
        assert all(command in commands for command in NOTIFICATION_COMMANDS)

        openapi = await http.handle(HTTPRequest(method="GET", path="/api/v1/openapi.json"))
        assert openapi.status == 200
        assert isinstance(openapi.body, dict)
        paths = openapi.body["paths"]
        assert isinstance(paths, dict)
        assert f"/api/v1/{NOTIFICATION_COLLECTION}" in paths
        assert f"/api/v1/{NOTIFICATION_PREFERENCE_COLLECTION}" in paths
        assert openapi.body["x-registered-extension-collections"] == []
        assert openapi.body["x-registered-extension-commands"] == []
        assert openapi.body["x-notifications"]["search_indexed"] is True
        assert openapi.body["x-notifications"]["search_projection"] == (
            "privacy-minimized-derived-state"
        )

    asyncio.run(scenario())
