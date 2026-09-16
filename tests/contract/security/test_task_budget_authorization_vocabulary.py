from ai_multi_agent_platform.security.authorization import AuthorizationAction, ResourceType
from ai_multi_agent_platform.security.control_plane_bridge import canonical_control_plane_vocabulary


def test_task_budget_commands_map_to_task_authorization() -> None:
    assert canonical_control_plane_vocabulary("task-budget.configure") == (
        AuthorizationAction.CREATE,
        ResourceType.TASK,
    )
    assert canonical_control_plane_vocabulary("task-budget.revise") == (
        AuthorizationAction.MODIFY,
        ResourceType.TASK,
    )


def test_task_budget_resources_map_to_task_authorization() -> None:
    assert canonical_control_plane_vocabulary("task-execution-budget:list") == (
        AuthorizationAction.VIEW,
        ResourceType.TASK,
    )
    assert canonical_control_plane_vocabulary("task-execution-budget:read") == (
        AuthorizationAction.READ,
        ResourceType.TASK,
    )
    assert canonical_control_plane_vocabulary("task-budget:read") == (
        AuthorizationAction.READ,
        ResourceType.TASK,
    )
