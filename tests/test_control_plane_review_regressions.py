from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import (
    AuthorizationDecision,
    AuthorizationRequest,
)
from ai_multi_agent_platform.control_plane import (
    ActorContext,
    ControlPlane,
    PageQuery,
    RequestContext,
    build_openapi,
)
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


class OwnerScopedAuthorizationProvider(FakeAuthorizationProvider):
    def __init__(self, owner_id: str) -> None:
        super().__init__()
        self.owner_id = owner_id

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        allowed = request.context.owner_type == "user" and request.context.owner_id == self.owner_id
        return AuthorizationDecision(allowed=allowed, reason="owner-scope")


def _context(
    key: str | None = None,
    *,
    owner_id: str = "test",
) -> RequestContext:
    return RequestContext(
        request_id="request-review",
        correlation_id="correlation-review",
        actor=ActorContext(
            principal_ref=f"user:{owner_id}",
            owner_type="user",
            owner_id=owner_id,
        ),
        idempotency_key=key,
    )


def _stack() -> tuple[PlatformKernel, InMemoryKernelRepository]:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    return kernel, repository


def test_create_task_authorizes_requested_owner_and_lists_only_authorized_scope() -> None:
    async def scenario() -> None:
        kernel, repository = _stack()
        own = await kernel.create_task(
            idempotency_key="seed-own",
            title="Own",
            objective="Visible task",
            owner_type="user",
            owner_id="test",
            actor_ref="seed:test",
            source="test",
        )
        other = await kernel.create_task(
            idempotency_key="seed-other",
            title="Other",
            objective="Hidden task",
            owner_type="user",
            owner_id="other",
            actor_ref="seed:other",
            source="test",
        )
        authorization = OwnerScopedAuthorizationProvider("test")
        control_plane = ControlPlane(
            kernel=kernel,
            events=repository,
            authorization=authorization,
        )

        page = await control_plane.list_tasks(_context(), PageQuery())
        assert page["total"] == 1
        items = page["items"]
        assert isinstance(items, list)
        assert [item["id"] for item in items if isinstance(item, dict)] == [own.task_id]

        with pytest.raises(ContractError) as forbidden_read:
            await control_plane.get_task(_context(), other.task_id)
        assert forbidden_read.value.code is ErrorCode.FORBIDDEN

        with pytest.raises(ContractError) as forbidden_create:
            await control_plane.create_task(
                _context("create-other"),
                {
                    "title": "Cross owner",
                    "objective": "Must be denied",
                    "owner_type": "user",
                    "owner_id": "other",
                },
            )
        assert forbidden_create.value.code is ErrorCode.FORBIDDEN
        assert authorization.calls[-1].context.owner_id == "other"

    asyncio.run(scenario())


def test_reference_lookup_traverses_beyond_first_api_page() -> None:
    async def scenario() -> None:
        kernel, repository = _stack()
        control_plane = ControlPlane(kernel=kernel, events=repository)

        for index in range(201):
            task = await kernel.create_task(
                idempotency_key=f"seed-task-{index}",
                title=f"Task {index}",
                objective="Create a canonical plan reference",
                owner_type="user",
                owner_id="test",
                actor_ref="seed:test",
                source="test",
            )
            await kernel.ready_task(
                idempotency_key=f"ready-task-{index}",
                task_id=task.task_id,
                actor_ref="seed:test",
                source="test",
            )

        first_page = await control_plane.list_references(
            _context(),
            "plans",
            PageQuery(limit=200),
        )
        cursor = first_page["next_cursor"]
        assert isinstance(cursor, str)
        second_page = await control_plane.list_references(
            _context(),
            "plans",
            PageQuery(limit=200, cursor=cursor),
        )
        second_items = second_page["items"]
        assert isinstance(second_items, list) and len(second_items) == 1
        target = second_items[0]
        assert isinstance(target, dict)
        target_id = target["id"]
        assert isinstance(target_id, str)

        loaded = await control_plane.get_reference(_context(), "plans", target_id)
        assert loaded["id"] == target_id

    asyncio.run(scenario())


def test_repository_event_cursor_is_rejected_before_stream_response_starts() -> None:
    async def scenario() -> None:
        kernel, repository = _stack()
        task = await kernel.create_task(
            idempotency_key="cursor-task",
            title="Cursor",
            objective="Validate cursor before returning iterator",
            owner_type="user",
            owner_id="test",
            actor_ref="seed:test",
            source="test",
        )
        control_plane = ControlPlane(kernel=kernel, events=repository)

        with pytest.raises(ContractError) as missing_cursor:
            await control_plane.subscribe_task_events(
                _context(),
                task.task_id,
                after_event_id="event_missing",
            )
        assert missing_cursor.value.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_openapi_paths_are_not_prefixed_twice_by_server_url() -> None:
    spec = build_openapi()
    assert "servers" not in spec
    paths = spec["paths"]
    assert "/api/v1/tasks" in paths
