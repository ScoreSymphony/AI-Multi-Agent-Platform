from __future__ import annotations

import asyncio
import importlib
import json

TERMINAL_COMMANDS = {
    "terminal.session.create",
    "terminal.session.input",
    "terminal.session.resize",
    "terminal.session.terminate",
}


def test_terminal_registration_is_visible_in_manifest_and_openapi() -> None:
    control_plane = importlib.import_module("ai_multi_agent_platform.control_plane")
    kernel_api = importlib.import_module("ai_multi_agent_platform.kernel")
    security = importlib.import_module("ai_multi_agent_platform.security")
    terminal_api = importlib.import_module("ai_multi_agent_platform.terminal")
    testing = importlib.import_module("ai_multi_agent_platform.testing")

    async def scenario() -> None:
        repository = kernel_api.InMemoryKernelRepository()
        kernel = kernel_api.PlatformKernel(
            orchestrator=testing.FakeOrchestrator(),
            lifecycle=testing.FakeLifecycleBackend(),
            repository=repository,
        )
        terminal = terminal_api.TerminalSessionService(
            security.AuthorizationGate(security.LocalAuthorizationProvider(())),
            (terminal_api.ReferenceTerminalAdapter(poll_interval_seconds=0.001),),
        )
        http = control_plane.ControlPlaneHTTP(
            control_plane.ControlPlane(
                kernel=kernel,
                events=repository,
                terminal_sessions=terminal,
            )
        )

        manifest = await http.handle(control_plane.HTTPRequest(method="GET", path="/api/v1"))
        assert manifest.status == 200
        assert isinstance(manifest.body, dict)
        resources = manifest.body["resources"]
        commands = manifest.body["commands"]
        assert isinstance(resources, list)
        assert isinstance(commands, list)
        assert "terminal-sessions" in resources
        assert TERMINAL_COMMANDS.issubset(set(commands))

        openapi = await http.handle(
            control_plane.HTTPRequest(method="GET", path="/api/v1/openapi.json")
        )
        assert openapi.status == 200
        encoded = json.dumps(openapi.body, sort_keys=True)
        assert "terminal-sessions" in encoded
        for command in TERMINAL_COMMANDS:
            assert command in encoded

    asyncio.run(scenario())
