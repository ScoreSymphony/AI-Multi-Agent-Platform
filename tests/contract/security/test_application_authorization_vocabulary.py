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


def test_application_resources_use_canonical_application_read_vocabulary() -> None:
    for action in (
        "application:list",
        "application-instance:read",
        "application-log:read",
        "application-resource-handler:list",
        "application-audit-event:list",
    ):
        expected_action = (
            AuthorizationAction.VIEW if action.endswith(":list") else AuthorizationAction.READ
        )
        assert canonical_control_plane_vocabulary(action) == (
            expected_action,
            ResourceType.APPLICATION,
        )
