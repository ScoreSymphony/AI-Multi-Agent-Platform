from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.automation import (
    Automation,
    AutomationService,
    AutomationState,
    IdentityContext,
    InMemoryAutomationRepository,
    TaskTemplate,
    TriggerDefinition,
    TriggerType,
)
from ai_multi_agent_platform.automation.workspace_event_scope import (
    CanonicalWorkspaceEventScopeResolver,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import Event, new_id
from ai_multi_agent_platform.workspaces import (
    InMemoryRunWorkspaceBindingRepository,
    RunWorkspaceBinding,
)


def _user_identity(owner_id: str = "issue-241-final") -> IdentityContext:
    return IdentityContext(
        principal_ref=f"user:{owner_id}",
        owner_type="user",
        owner_id=owner_id,
    )


def _service_identity() -> IdentityContext:
    return IdentityContext(
        principal_ref="service:automation-runtime",
        owner_type="service",
        owner_id="automation-runtime",
    )


class _ConfigurationAwareService(AutomationService):
    async def _validate_configuration_for_revalidation(self, automation: Automation) -> None:
        if automation.task_template.payload.get("configuration_state") != "valid":
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "configuration is still invalid",
            )


def test_invalid_schedule_update_revalidate_preserves_position_and_audits_recovery() -> None:
    async def scenario() -> None:
        events: list[dict[str, JsonValue]] = []
        calls = 0
        created_at = datetime(2026, 9, 4, 18, 0, tzinfo=UTC)
        scheduled_at = created_at + timedelta(hours=1)

        async def creator(*args: object) -> str:
            nonlocal calls
            del args
            calls += 1
            return new_id("task")

        async def sink(event: dict[str, JsonValue]) -> None:
            events.append(event)

        service = _ConfigurationAwareService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
            event_sink=sink,
        )
        automation = await service.create_automation(
            name="repairable invalid schedule",
            description="",
            identity=_user_identity(),
            trigger=TriggerDefinition(type=TriggerType.ONE_TIME, at=scheduled_at),
            task_template=TaskTemplate(
                title="Repairable task",
                objective="Prove update and revalidation semantics",
                payload={"configuration_state": "invalid"},
            ),
            now=created_at,
        )
        invalidated = await service.invalidate_automation(
            automation.id,
            reason_code="required_reference_invalid",
            now=created_at + timedelta(minutes=5),
        )

        assert await service.evaluate_due(now=scheduled_at) == ()
        assert calls == 0
        assert invalidated.next_evaluation_at == scheduled_at

        updated = await service.update_automation(
            automation.id,
            task_template=TaskTemplate(
                title="Repairable task",
                objective="Prove update and revalidation semantics",
                payload={"configuration_state": "valid"},
            ),
            now=created_at + timedelta(minutes=10),
        )
        assert updated.state is AutomationState.INVALID
        assert updated.next_evaluation_at == scheduled_at

        recovered = await service.revalidate_automation(
            automation.id,
            now=created_at + timedelta(minutes=15),
        )
        assert recovered.state is AutomationState.ENABLED
        assert recovered.next_evaluation_at == scheduled_at
        assert recovered.invalidation_reason_code is None

        fired = await service.evaluate_due(now=scheduled_at)
        assert len(fired) == 1
        assert calls == 1

        lifecycle = [event for event in events if event.get("type") == "automation.lifecycle"]
        assert [event.get("action") for event in lifecycle] == ["invalidated", "revalidated"]
        assert lifecycle[0]["invalidation_reason_code"] == "required_reference_invalid"
        assert lifecycle[1]["invalidation_reason_code"] is None

    asyncio.run(scenario())


def test_workspace_event_from_different_project_is_rejected_before_delivery_mutation() -> None:
    async def scenario() -> None:
        events: list[dict[str, JsonValue]] = []
        automation_project = new_id("project")
        foreign_project = new_id("project")
        workspace_id = new_id("workspace")
        run_id = new_id("run")
        now = datetime(2026, 9, 4, 20, 0, tzinfo=UTC)
        bindings = InMemoryRunWorkspaceBindingRepository()
        await bindings.bind(
            RunWorkspaceBinding(
                run_id=run_id,
                task_id=new_id("task"),
                workspace_id=workspace_id,
                workspace_snapshot_id=new_id("workspace_snapshot"),
                content_checksum="c" * 64,
            )
        )

        async def creator(*args: object) -> str:
            del args
            return new_id("task")

        async def sink(event: dict[str, JsonValue]) -> None:
            events.append(event)

        service = AutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
            event_sink=sink,
        )
        service.configure_workspace_event_scope_resolver(
            CanonicalWorkspaceEventScopeResolver(run_workspace_bindings=bindings)
        )
        automation = await service.create_automation(
            name="project boundary",
            description="",
            identity=_user_identity(),
            project_id=automation_project,
            workspace_id=workspace_id,
            trigger=TriggerDefinition(
                type=TriggerType.PLATFORM_EVENT,
                event_type="run.completed",
            ),
            task_template=TaskTemplate(title="Scoped", objective="Stay in project"),
            now=now,
        )

        deliveries = await service.deliver_canonical_platform_event(
            Event(
                event_type="run.completed",
                subject_type="run",
                subject_id=run_id,
                correlation_id="issue-241-foreign-project",
                project_id=foreign_project,
                occurred_at=now + timedelta(seconds=1),
            )
        )

        assert deliveries == ()
        assert await service.list_deliveries(automation.id) == ()
        visibility = [
            event for event in events if event.get("type") == "automation.event_visibility"
        ][-1]
        assert visibility["reason_code"] == "project_scope_mismatch"
        assert "event_id" not in visibility
        assert "subject_id" not in visibility
        assert "workspace_id" not in visibility

    asyncio.run(scenario())


def test_global_unowned_event_is_visible_only_to_service_owned_automation() -> None:
    async def scenario() -> None:
        now = datetime(2026, 9, 4, 21, 0, tzinfo=UTC)
        calls = 0

        async def creator(*args: object) -> str:
            nonlocal calls
            del args
            calls += 1
            return new_id("task")

        service = AutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
        )
        user_automation = await service.create_automation(
            name="user global watcher",
            description="",
            identity=_user_identity("global-user"),
            trigger=TriggerDefinition(
                type=TriggerType.PLATFORM_EVENT,
                event_type="platform.maintenance",
            ),
            task_template=TaskTemplate(title="User", objective="Must stay isolated"),
            now=now,
        )
        service_automation = await service.create_automation(
            name="service global watcher",
            description="",
            identity=_service_identity(),
            trigger=TriggerDefinition(
                type=TriggerType.PLATFORM_EVENT,
                event_type="platform.maintenance",
            ),
            task_template=TaskTemplate(title="Service", objective="Handle global event"),
            now=now,
        )

        deliveries = await service.deliver_canonical_platform_event(
            Event(
                event_type="platform.maintenance",
                subject_type="service",
                subject_id="automation-runtime",
                correlation_id="issue-241-global-event",
                occurred_at=now + timedelta(seconds=1),
            )
        )

        assert len(deliveries) == 1
        assert deliveries[0].automation_id == service_automation.id
        assert await service.list_deliveries(user_automation.id) == ()
        assert len(await service.list_deliveries(service_automation.id)) == 1
        assert calls == 1

    asyncio.run(scenario())
