from ai_multi_agent_platform.security.authorization import AuthorizationAction, ResourceType
from ai_multi_agent_platform.security.control_plane_bridge import canonical_control_plane_vocabulary


def test_invalid_lifecycle_commands_require_automation_administration() -> None:
    for command in ("automation.invalidate", "automation.revalidate"):
        assert canonical_control_plane_vocabulary(command) == (
            AuthorizationAction.ADMINISTER,
            ResourceType.AUTOMATION,
        )
