from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import AuthorizationDecision, AuthorizationRequest
from ai_multi_agent_platform.control_plane import (
    TASK_PROJECT_BULK_MOVE_COMMAND,
    TASK_PROJECT_MOVE_ACTION,
    TASK_PROJECT_MOVE_COMMAND,
    ControlPlane,
    RequestContext,
)
from ai_multi_agent_platform.control_plane.models import ActorContext
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel, TaskState
from ai_multi_agent_platform.task_reassignment import PreparedTaskProjectMove
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


def _context(key: str) -> RequestContext:
    return RequestContext(
        request_id=f"request:{key}",
        correlation_id=f"correlation:{key}",
        actor=ActorContext(
            principal_ref="user:alice",
            owner_type="user",
            owner_id="user:alice",
        ),
        idempotency_key=key,
    )


def _stack(*, authorization=None):
    events = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=events,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=events,
        authorization=authorization,
    )
    return control_plane, kernel


async def _project(control_plane: ControlPlane, key: str):
    return control_plane.scopes.create_project(
        key=key,
        name=key,
        owner_type="user",
        owner_id="user:alice",
    )


async def _task(kernel: PlatformKernel, key: str, project_id: str):
    return await kernel.create_task(
        idempotency_key=key,
        title=key,
        objective=f"objective:{key}",
        owner_type="user",
        owner_id="user:alice",
        project_id=project_id,
    )


def _bulk_payload(first_id: str, second_id: str, destination_id: str):
    return {
        "moves": [
            {"task_id": first_id, "destination_project_id": destination_id},
            {"task_id": second_id, "destination_project_id": destination_id},
        ]
    }


def test_bulk_exact_retry_is_idempotent_and_does_not_append_events() -> None:
    async def scenario() -> None:
        control_plane, kernel = _stack()
        source = await _project(control_plane, "source")
        destination = await _project(control_plane, "destination")
        first = await _task(kernel, "first", source.id)
        second = await _task(kernel, "second", source.id)
        context = _context("bulk-retry")
        payload = _bulk_payload(first.task_id, second.task_id, destination.id)

        first_result = await control_plane.execute_command(
            context,
            TASK_PROJECT_BULK_MOVE_COMMAND,
            "tasks",
            payload,
        )
        history_lengths = {
            first.task_id: len(await kernel.history(first.task_id)),
            second.task_id: len(await kernel.history(second.task_id)),
        }

        retried = await control_plane.execute_command(
            context,
            TASK_PROJECT_BULK_MOVE_COMMAND,
            "tasks",
            payload,
        )

        assert first_result["count"] == retried["count"] == 2
        assert len(await kernel.history(first.task_id)) == history_lengths[first.task_id]
        assert len(await kernel.history(second.task_id)) == history_lengths[second.task_id]
        assert (await kernel.get_task(first.task_id)).task.project_id == destination.id
        assert (await kernel.get_task(second.task_id)).task.project_id == destination.id
        reservation_events = [
            event
            for task_id in (first.task_id, second.task_id)
            for event in await kernel.history(task_id)
            if event.event_type == "task.project_bulk_move_reserved"
        ]
        assert len(reservation_events) == 1

    asyncio.run(scenario())


def test_bulk_idempotency_key_is_bound_to_the_complete_move_set() -> None:
    async def scenario() -> None:
        control_plane, kernel = _stack()
        source = await _project(control_plane, "source")
        destination = await _project(control_plane, "destination")
        first = await _task(kernel, "first", source.id)
        second = await _task(kernel, "second", source.id)
        third = await _task(kernel, "third", source.id)
        context = _context("bulk-bound-set")

        await control_plane.execute_command(
            context,
            TASK_PROJECT_BULK_MOVE_COMMAND,
            "tasks",
            _bulk_payload(first.task_id, second.task_id, destination.id),
        )

        changed = {
            "moves": [
                {"task_id": first.task_id, "destination_project_id": destination.id},
                {"task_id": second.task_id, "destination_project_id": destination.id},
                {"task_id": third.task_id, "destination_project_id": destination.id},
            ]
        }
        with pytest.raises(ContractError, match="different Task Project bulk move set"):
            await control_plane.execute_command(
                context,
                TASK_PROJECT_BULK_MOVE_COMMAND,
                "tasks",
                changed,
            )

        assert (await kernel.get_task(third.task_id)).task.project_id == source.id

    asyncio.run(scenario())


def test_bulk_retry_resumes_only_items_missing_after_partial_failure() -> None:
    async def scenario() -> None:
        control_plane, kernel = _stack()
        source = await _project(control_plane, "source")
        destination = await _project(control_plane, "destination")
        first = await _task(kernel, "first", source.id)
        second = await _task(kernel, "second", source.id)
        context = _context("bulk-partial")
        payload = _bulk_payload(first.task_id, second.task_id, destination.id)
        service = control_plane.task_project_reassignment
        original_commit = service.commit
        calls = 0

        async def flaky_commit(
            prepared: PreparedTaskProjectMove,
            *,
            idempotency_key: str,
            actor_ref: str | None,
            source: str = "task-project-reassignment",
        ) -> TaskState:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ContractError(ErrorCode.BACKEND_ERROR, "synthetic second-item failure")
            return await original_commit(
                prepared,
                idempotency_key=idempotency_key,
                actor_ref=actor_ref,
                source=source,
            )

        service.commit = flaky_commit  # type: ignore[method-assign]
        try:
            with pytest.raises(ContractError, match="synthetic second-item failure"):
                await control_plane.execute_command(
                    context,
                    TASK_PROJECT_BULK_MOVE_COMMAND,
                    "tasks",
                    payload,
                )
        finally:
            service.commit = original_commit  # type: ignore[method-assign]

        assert (await kernel.get_task(first.task_id)).task.project_id == destination.id
        assert (await kernel.get_task(second.task_id)).task.project_id == source.id
        first_history_length = len(await kernel.history(first.task_id))

        result = await control_plane.execute_command(
            context,
            TASK_PROJECT_BULK_MOVE_COMMAND,
            "tasks",
            payload,
        )

        assert result["count"] == 2
        assert (await kernel.get_task(first.task_id)).task.project_id == destination.id
        assert (await kernel.get_task(second.task_id)).task.project_id == destination.id
        assert len(await kernel.history(first.task_id)) == first_history_length

    asyncio.run(scenario())


class _DenyResource(FakeAuthorizationProvider):
    def __init__(self, denied_resource: Callable[[], str]) -> None:
        super().__init__()
        self._denied_resource = denied_resource

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        denied = (
            request.action == TASK_PROJECT_MOVE_ACTION
            and request.resource_ref == self._denied_resource()
        )
        return AuthorizationDecision(
            allowed=not denied,
            reason="Task Project move resource denied" if denied else None,
        )


def test_task_authorization_denial_blocks_move_before_scope_mutation() -> None:
    async def scenario() -> None:
        denied_task_id = ""
        authorization = _DenyResource(lambda: denied_task_id)
        control_plane, kernel = _stack(authorization=authorization)
        source = await _project(control_plane, "source")
        destination = await _project(control_plane, "destination")
        task = await _task(kernel, "task", source.id)
        denied_task_id = task.task_id

        with pytest.raises(ContractError):
            await control_plane.execute_command(
                _context("deny-task"),
                TASK_PROJECT_MOVE_COMMAND,
                task.task_id,
                {"destination_project_id": destination.id},
            )

        assert (await kernel.get_task(task.task_id)).task.project_id == source.id
        assert not any(
            event.event_type == "task.project_reassigned"
            for event in await kernel.history(task.task_id)
        )

    asyncio.run(scenario())


def test_source_project_authorization_denial_blocks_move() -> None:
    async def scenario() -> None:
        denied_project_id = ""
        authorization = _DenyResource(lambda: denied_project_id)
        control_plane, kernel = _stack(authorization=authorization)
        source = await _project(control_plane, "source")
        destination = await _project(control_plane, "destination")
        denied_project_id = source.id
        task = await _task(kernel, "task", source.id)

        with pytest.raises(ContractError):
            await control_plane.execute_command(
                _context("deny-source"),
                TASK_PROJECT_MOVE_COMMAND,
                task.task_id,
                {"destination_project_id": destination.id},
            )

        assert (await kernel.get_task(task.task_id)).task.project_id == source.id
        assert any(
            call.action == TASK_PROJECT_MOVE_ACTION and call.resource_ref == source.id
            for call in authorization.calls
        )

    asyncio.run(scenario())


def test_move_to_no_project_preserves_owner_and_future_run_has_no_project_scope() -> None:
    async def scenario() -> None:
        control_plane, kernel = _stack()
        source = await _project(control_plane, "source")
        task = await _task(kernel, "task", source.id)
        original_owner = task.task.owner_ref

        moved = await control_plane.execute_command(
            _context("move-no-project"),
            TASK_PROJECT_MOVE_COMMAND,
            task.task_id,
            {"destination_project_id": None},
        )

        assert moved["project_id"] is None
        canonical = await kernel.get_task(task.task_id)
        assert canonical.task.project_id is None
        assert canonical.task.owner_ref == original_owner
        move_event = next(
            event
            for event in await kernel.history(task.task_id)
            if event.event_type == "task.project_reassigned"
        )
        assert move_event.project_id == source.id
        assert move_event.payload["destination_project_id"] is None

        await kernel.ready_task(idempotency_key="ready-after-no-project", task_id=task.task_id)
        run = await kernel.start_task(
            idempotency_key="run-after-no-project",
            task_id=task.task_id,
        )
        assert run.run.project_id is None

    asyncio.run(scenario())
