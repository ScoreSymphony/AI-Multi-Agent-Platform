from ai_multi_agent_platform.security import (
    AuthorizationAction,
    ResourceType,
    canonical_control_plane_vocabulary,
)


def test_distributed_admin_commands_use_node_worker_authorization_vocabulary() -> None:
    for command in (
        "node.drain",
        "node.undrain",
        "node.maintenance-enable",
        "node.maintenance-disable",
    ):
        assert canonical_control_plane_vocabulary(command) == (
            AuthorizationAction.ADMINISTER,
            ResourceType.NODE,
        )

    for command in ("worker.drain", "worker.undrain"):
        assert canonical_control_plane_vocabulary(command) == (
            AuthorizationAction.ADMINISTER,
            ResourceType.WORKER,
        )


def test_unknown_distributed_dot_command_stays_resource_scoped() -> None:
    assert canonical_control_plane_vocabulary("node.future-operation") == (
        AuthorizationAction.MODIFY,
        ResourceType.NODE,
    )
    assert canonical_control_plane_vocabulary("worker.future-operation") == (
        AuthorizationAction.MODIFY,
        ResourceType.WORKER,
    )
