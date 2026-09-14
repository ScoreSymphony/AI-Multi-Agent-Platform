from __future__ import annotations

from ai_multi_agent_platform.control_plane import ControlPlane
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.security import AuthorizationGate, LocalAuthorizationProvider
from ai_multi_agent_platform.terminal import ReferenceTerminalAdapter, TerminalSessionService
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

TERMINAL_COMMANDS = (
    "terminal.session.create",
    "terminal.session.input",
    "terminal.session.resize",
    "terminal.session.terminate",
)


def test_terminal_surface_has_one_explicit_owner() -> None:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    sessions = TerminalSessionService(
        AuthorizationGate(LocalAuthorizationProvider(())),
        (ReferenceTerminalAdapter(),),
    )

    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        terminal_sessions=sessions,
    )

    assert "terminal" in control_plane.registered_modules
    assert control_plane.resource_owner("terminal-sessions") == "terminal"
    for command in TERMINAL_COMMANDS:
        assert control_plane.command_owner(command) == "terminal"
