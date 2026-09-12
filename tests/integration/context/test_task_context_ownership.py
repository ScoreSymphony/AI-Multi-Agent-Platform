"""Task-owned user-intent Context coverage originating in issue #680."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from ai_multi_agent_platform.context import (
    ContextSourceType,
    OperationalContextSourceRequest,
    TaskContextSourceAdapter,
)
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.domain import new_id


def _source_request(*, task_id: str, project_id: str) -> OperationalContextSourceRequest:
    return OperationalContextSourceRequest(
        task_id=task_id,
        run_id=new_id("run"),
        agent_id=new_id("agent"),
        agent_revision=1,
        project_id=project_id,
        workspace_id=None,
        operation=OperationContext(
            correlation_id=task_id,
            owner_type="user",
            owner_id="issue-680",
            project_id=project_id,
        ),
        actor_ref="user:issue-680",
    )


def test_explicit_user_objective_remains_task_owned_canonical_context() -> None:
    task_id = new_id("task")
    project_id = new_id("project")
    request = _source_request(task_id=task_id, project_id=project_id)
    objective = "Use the user-provided constraints exactly and cite the requested files."
    task = SimpleNamespace(
        id=task_id,
        title="User-requested task",
        description=objective,
        status=SimpleNamespace(value="running"),
        project_id=project_id,
        metadata={},
    )
    state = SimpleNamespace(task_id=task_id, task=task, revision=9)

    class _TaskRepository:
        async def get_task(self, requested_task_id: str):
            assert requested_task_id == task_id
            return state

    candidate = asyncio.run(TaskContextSourceAdapter(_TaskRepository()).collect(request))[0]

    assert candidate.source.source_type is ContextSourceType.TASK
    assert candidate.source.revision == "9"
    assert candidate.mandatory is True
    assert objective in (candidate.inline_content or "")
    assert candidate.source.source_type is not ContextSourceType.HUMAN
