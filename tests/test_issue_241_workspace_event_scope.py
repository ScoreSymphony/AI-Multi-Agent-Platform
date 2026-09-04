from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.automation import (
    AutomationService,
    IdentityContext,
    InMemoryAutomationRepository,
    TaskTemplate,
    TriggerDefinition,
    TriggerType,
)
from ai_multi_agent_platform.automation.workspace_event_scope import (
    CanonicalWorkspaceEventScopeResolver,
    WorkspaceEventScopeResolver,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, PlatformEvent
from ai_multi_agent_platform.domain import Event, OwnerRef, new_id
from ai_multi_agent_platform.workspaces import (
    InMemoryRunWorkspaceBindingRepository,
    RunWorkspaceBinding,
)


async def _task_creator(*args: object) -> str:
    del args
    return new_id("task")


def _identity() -> IdentityContext:
    return IdentityContext(
        principal_ref="user:workspace-owner",
        owner_type="user",
        owner_id="workspace-owner",
    )


def _template() -> TaskTemplate:
    return TaskTemplate(title="Workspace event", objective="Exercise workspace event isolation")


def _event(
    *,
    run_id: str,
    project_id: str,
    owner: OwnerRef,
    occurred_at: datetime,
) -> Event:
    return Event(
        event_type="run.completed",
        subject_type="run",
        subject_id=run_id,
        correlation_id="issue-241-workspace-event",
        owner_ref=owner,
        project_id=project_id,
        occurred_at=occurred_at,
        payload={"kind": "done"},
    )


async def _bind_run(
    repository: InMemoryRunWorkspaceBindingRepository,
    *,
    run_id: str,
    workspace_id: str,
) -> None:
    await repository.bind(
        RunWorkspaceBinding(
            run_id=run_id,
            task_id=new_id("task"),
            workspace_id=workspace_id,
            workspace_snapshot_id=new_id("workspace_snapshot"),
            content_checksum="a" * 64,
        )
    )


def test_workspace_scoped_automation_accepts_matching_canonical_run_binding() -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        workspace_id = new_id("workspace")
        run_id = new_id("run")
        owner = OwnerRef(type="user", id="workspace-owner")
        bindings = InMemoryRunWorkspaceBindingRepository()
        await _bind_run(bindings, run_id=run_id, workspace_id=workspace_id)

        repository = InMemoryAutomationRepository()
        service = AutomationService(repository=repository, task_creator=_task_creator)
        service.configure_workspace_event_scope_resolver(
            CanonicalWorkspaceEventScopeResolver(run_workspace_bindings=bindings)
        )
        now = datetime(2026, 9, 4, 13, 0, tzinfo=UTC)
        automation = await service.create_automation(
            name="matching workspace",
            description="",
            identity=_identity(),
            project_id=project_id,
            workspace_id=workspace_id,
            trigger=TriggerDefinition(
                type=TriggerType.PLATFORM_EVENT,
                event_type="run.completed",
                filters={"kind": "done"},
            ),
            task_template=_template(),
            now=now,
        )

        deliveries = await service.deliver_canonical_platform_event(
            _event(
                run_id=run_id,
                project_id=project_id,
                owner=owner,
                occurred_at=now + timedelta(seconds=1),
            )
        )
        assert len(deliveries) == 1
        assert deliveries[0].automation_id == automation.id

    asyncio.run(scenario())


def test_cross_workspace_event_is_hidden_before_delivery_or_dedupe_mutation() -> None:
    async def scenario() -> None:
        events: list[dict[str, JsonValue]] = []

        async def sink(event: dict[str, JsonValue]) -> None:
            events.append(event)

        project_id = new_id("project")
        automation_workspace_id = new_id("workspace")
        event_workspace_id = new_id("workspace")
        run_id = new_id("run")
        owner = OwnerRef(type="user", id="workspace-owner")
        bindings = InMemoryRunWorkspaceBindingRepository()
        await _bind_run(bindings, run_id=run_id, workspace_id=event_workspace_id)

        repository = InMemoryAutomationRepository()
        service = AutomationService(
            repository=repository,
            task_creator=_task_creator,
            event_sink=sink,
        )
        service.configure_workspace_event_scope_resolver(
            CanonicalWorkspaceEventScopeResolver(run_workspace_bindings=bindings)
        )
        now = datetime(2026, 9, 4, 14, 0, tzinfo=UTC)
        automation = await service.create_automation(
            name="cross workspace",
            description="",
            identity=_identity(),
            project_id=project_id,
            workspace_id=automation_workspace_id,
            trigger=TriggerDefinition(
                type=TriggerType.PLATFORM_EVENT,
                event_type="run.completed",
                filters={"kind": "done"},
            ),
            task_template=_template(),
            now=now,
        )

        deliveries = await service.deliver_canonical_platform_event(
            _event(
                run_id=run_id,
                project_id=project_id,
                owner=owner,
                occurred_at=now + timedelta(seconds=1),
            )
        )
        assert deliveries == ()
        assert await service.list_deliveries(automation.id) == ()
        visibility_events = [
            event for event in events if event.get("type") == "automation.event_visibility"
        ]
        assert visibility_events[-1]["reason_code"] == "workspace_scope_mismatch"
        assert "event_id" not in visibility_events[-1]
        assert "subject_id" not in visibility_events[-1]
        assert "workspace_id" not in visibility_events[-1]

    asyncio.run(scenario())


def test_workspace_scope_without_authoritative_binding_fails_closed() -> None:
    async def scenario() -> None:
        events: list[dict[str, JsonValue]] = []

        async def sink(event: dict[str, JsonValue]) -> None:
            events.append(event)

        project_id = new_id("project")
        workspace_id = new_id("workspace")
        owner = OwnerRef(type="user", id="workspace-owner")
        bindings = InMemoryRunWorkspaceBindingRepository()
        repository = InMemoryAutomationRepository()
        service = AutomationService(
            repository=repository,
            task_creator=_task_creator,
            event_sink=sink,
        )
        service.configure_workspace_event_scope_resolver(
            CanonicalWorkspaceEventScopeResolver(run_workspace_bindings=bindings)
        )
        now = datetime(2026, 9, 4, 15, 0, tzinfo=UTC)
        automation = await service.create_automation(
            name="unproven workspace",
            description="",
            identity=_identity(),
            project_id=project_id,
            workspace_id=workspace_id,
            trigger=TriggerDefinition(
                type=TriggerType.PLATFORM_EVENT,
                event_type="run.completed",
            ),
            task_template=_template(),
            now=now,
        )

        deliveries = await service.deliver_canonical_platform_event(
            _event(
                run_id=new_id("run"),
                project_id=project_id,
                owner=owner,
                occurred_at=now + timedelta(seconds=1),
            )
        )
        assert deliveries == ()
        assert await service.list_deliveries(automation.id) == ()
        assert any(
            event.get("type") == "automation.event_visibility"
            and event.get("reason_code") == "workspace_scope_unproven"
            for event in events
        )

    asyncio.run(scenario())


def test_workspace_scope_resolver_failure_fails_closed_without_resource_details() -> None:
    class FailingResolver(WorkspaceEventScopeResolver):
        async def resolve_workspace_id(self, event: PlatformEvent) -> str | None:
            del event
            raise ContractError(ErrorCode.BACKEND_ERROR, "workspace backend unavailable")

    async def scenario() -> None:
        events: list[dict[str, JsonValue]] = []

        async def sink(event: dict[str, JsonValue]) -> None:
            events.append(event)

        project_id = new_id("project")
        workspace_id = new_id("workspace")
        owner = OwnerRef(type="user", id="workspace-owner")
        repository = InMemoryAutomationRepository()
        service = AutomationService(
            repository=repository,
            task_creator=_task_creator,
            event_sink=sink,
        )
        service.configure_workspace_event_scope_resolver(FailingResolver())
        now = datetime(2026, 9, 4, 16, 0, tzinfo=UTC)
        automation = await service.create_automation(
            name="resolver failure",
            description="",
            identity=_identity(),
            project_id=project_id,
            workspace_id=workspace_id,
            trigger=TriggerDefinition(
                type=TriggerType.PLATFORM_EVENT,
                event_type="run.completed",
            ),
            task_template=_template(),
            now=now,
        )

        deliveries = await service.deliver_canonical_platform_event(
            _event(
                run_id=new_id("run"),
                project_id=project_id,
                owner=owner,
                occurred_at=now + timedelta(seconds=1),
            )
        )
        assert deliveries == ()
        assert await service.list_deliveries(automation.id) == ()
        visibility = [
            event for event in events if event.get("type") == "automation.event_visibility"
        ][-1]
        assert visibility["reason_code"] == "workspace_scope_resolution_failed"
        assert set(visibility) == {
            "type",
            "automation_id",
            "automation_revision",
            "outcome",
            "reason_code",
            "project_scoped",
            "workspace_scoped",
        }

    asyncio.run(scenario())
