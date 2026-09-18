from ai_multi_agent_platform.security import AuthorizationAction, ResourceType
from ai_multi_agent_platform.security.control_plane_bridge import canonical_control_plane_vocabulary


def test_notification_read_and_stream_actions_use_notification_resource_type() -> None:
    assert canonical_control_plane_vocabulary("notification:list") == (
        AuthorizationAction.VIEW,
        ResourceType.NOTIFICATION,
    )
    assert canonical_control_plane_vocabulary("notification:read") == (
        AuthorizationAction.READ,
        ResourceType.NOTIFICATION,
    )
    assert canonical_control_plane_vocabulary("notification:subscribe") == (
        AuthorizationAction.READ,
        ResourceType.NOTIFICATION,
    )


def test_notification_commands_do_not_fall_back_to_generic_authorization() -> None:
    modifying = (
        "notification.mark-read",
        "notification.mark-all-read",
        "notification.acknowledge",
        "notification.dismiss",
        "notification.archive",
        "notification.preference.update",
    )
    for command in modifying:
        assert canonical_control_plane_vocabulary(command) == (
            AuthorizationAction.MODIFY,
            ResourceType.NOTIFICATION,
        )

    assert canonical_control_plane_vocabulary("notification.delivery.retry") == (
        AuthorizationAction.EXECUTE,
        ResourceType.NOTIFICATION,
    )
