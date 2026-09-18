from ai_multi_agent_platform.security import (
    AuthorizationAction,
    ResourceType,
    canonical_control_plane_vocabulary,
)


def test_application_commands_use_canonical_application_authorization_vocabulary() -> None:
    expected = {
        "application.install": AuthorizationAction.CREATE,
        "application.configure": AuthorizationAction.MODIFY,
        "application.start": AuthorizationAction.EXECUTE,
        "application.stop": AuthorizationAction.EXECUTE,
        "application.restart": AuthorizationAction.EXECUTE,
        "application.remove": AuthorizationAction.DELETE,
        "application.reconcile": AuthorizationAction.ADMINISTER,
    }

    for command, action in expected.items():
        assert canonical_control_plane_vocabulary(command) == (
            action,
            ResourceType.APPLICATION,
        )
