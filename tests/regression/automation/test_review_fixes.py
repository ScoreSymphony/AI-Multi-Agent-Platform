from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.automation import (
    AutomationService,
    AutomationState,
    DeliveryStatus,
    IdentityContext,
    InMemoryAutomationRepository,
    TaskTemplate,
    TriggerDefinition,
    TriggerDelivery,
    TriggerType,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import AuthorizationDecision, AuthorizationRequest
from ai_multi_agent_platform.control_plane import (
    ActorContext,
    ControlPlane,
    PageQuery,
    RequestContext,
)
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.security.authorization import AuthorizationAction, ResourceType
from ai_multi_agent_platform.security.control_plane_bridge import canonical_control_plane_vocabulary
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


def _identity(owner_id: str = "owner") -> IdentityContext:
    return IdentityContext(
        principal_ref=f"user:{owner_id}",
        owner_type="user",
        owner_id=owner_id,
    )


def _template() -> TaskTemplate:
    return TaskTemplate(title="Review regression", objective="Exercise issue 18 hardening")


def _stack(
    authorization: FakeAuthorizationProvider | None = None,
) -> ControlPlane:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    return ControlPlane(
        kernel=kernel,
        events=repository,
        authorization=authorization or FakeAuthorizationProvider(),
    )


def _context(owner_id: str = "owner", key: str = "issue18-review") -> RequestContext:
    return RequestContext(
        request_id=f"request-{owner_id}",
        correlation_id=f"correlation-{owner_id}",
        actor=ActorContext(
            principal_ref=f"user:{owner_id}",
            owner_type="user",
            owner_id=owner_id,
        ),
        idempotency_key=key,
    )


class OwnerScopedAuthorizationProvider(FakeAuthorizationProvider):
    def __init__(self, owner_id: str) -> None:
        super().__init__()
        self.owner_id = owner_id

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        allowed = request.context.owner_type == "user" and request.context.owner_id == self.owner_id
        return AuthorizationDecision(allowed=allowed, reason="owner-scope")


def test_automation_authorization_vocabulary_is_canonical() -> None:
    assert canonical_control_plane_vocabulary("automation.update") == (
        AuthorizationAction.MODIFY,
        ResourceType.AUTOMATION,
    )
    assert canonical_control_plane_vocabulary("automation.webhook") == (
        AuthorizationAction.EXECUTE,
        ResourceType.AUTOMATION,
    )
    assert canonical_control_plane_vocabulary("automation:read") == (
        AuthorizationAction.READ,
        ResourceType.AUTOMATION,
    )
    assert canonical_control_plane_vocabulary("automation-delivery:list") == (
        AuthorizationAction.VIEW,
        ResourceType.AUTOMATION,
    )


def test_control_plane_filters_reads_and_mutations_by_stored_automation_owner() -> None:
    async def scenario() -> None:
        authorization = OwnerScopedAuthorizationProvider("owner")
        control_plane = _stack(authorization)
        own = await control_plane.automation_service.create_automation(
            name="own",
            description="",
            identity=_identity("owner"),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=_template(),
        )
        other = await control_plane.automation_service.create_automation(
            name="other",
            description="",
            identity=_identity("other"),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=_template(),
        )
        own_delivery = TriggerDelivery.create(
            automation_id=own.id,
            trigger_type=TriggerType.MANUAL,
            source="test",
            dedupe_key="manual:own",
            fired_at=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
        )
        other_delivery = TriggerDelivery.create(
            automation_id=other.id,
            trigger_type=TriggerType.MANUAL,
            source="test",
            dedupe_key="manual:other",
            fired_at=datetime(2026, 9, 3, 12, 1, tzinfo=UTC),
        )
        await control_plane.automation_service.repository.save_delivery(own_delivery)
        await control_plane.automation_service.repository.save_delivery(other_delivery)

        page = await control_plane.list_extension_resources(_context(), "automations", PageQuery())
        items = page["items"]
        assert isinstance(items, list)
        assert [item["id"] for item in items if isinstance(item, dict)] == [own.id]
        assert items[0]["owner_ref"] == {"type": "user", "id": "owner"}

        delivery_page = await control_plane.list_extension_resources(
            _context(), "automation-deliveries", PageQuery()
        )
        delivery_items = delivery_page["items"]
        assert isinstance(delivery_items, list)
        assert [item["id"] for item in delivery_items if isinstance(item, dict)] == [
            own_delivery.id
        ]

        with pytest.raises(ContractError) as forbidden_read:
            await control_plane.get_extension_resource(_context(), "automations", other.id)
        assert forbidden_read.value.code is ErrorCode.FORBIDDEN

        with pytest.raises(ContractError) as forbidden_delivery_read:
            await control_plane.get_extension_resource(
                _context(), "automation-deliveries", other_delivery.id
            )
        assert forbidden_delivery_read.value.code is ErrorCode.FORBIDDEN

        with pytest.raises(ContractError) as forbidden_update:
            await control_plane.execute_command(
                _context(key="cross-owner-update"),
                "automation.update",
                other.id,
                {"description": "must not change"},
            )
        assert forbidden_update.value.code is ErrorCode.FORBIDDEN
        refreshed = await control_plane.automation_service.get_automation(other.id)
        assert refreshed.description == ""
        assert authorization.calls[-1].context.owner_id == "other"

    asyncio.run(scenario())


def test_automation_create_replays_same_command_key_and_rejects_payload_reuse() -> None:
    async def scenario() -> None:
        control_plane = _stack()
        payload = {
            "name": "idempotent",
            "trigger": {"type": "manual"},
            "task_template": {"title": "Task", "objective": "Same command"},
        }
        first = await control_plane.execute_command(
            _context(key="create-key"),
            "automation.create",
            "automations",
            payload,
        )
        second = await control_plane.execute_command(
            _context(key="create-key"),
            "automation.create",
            "automations",
            payload,
        )
        assert first["id"] == second["id"]
        assert len(await control_plane.automation_service.list_automations()) == 1

        with pytest.raises(ContractError) as conflict:
            await control_plane.execute_command(
                _context(key="create-key"),
                "automation.create",
                "automations",
                {
                    **payload,
                    "name": "different",
                },
            )
        assert conflict.value.code is ErrorCode.CONFLICT

    asyncio.run(scenario())


def test_failed_webhook_verification_never_reuses_or_poisons_accepted_dedupe() -> None:
    async def scenario() -> None:
        calls = 0

        async def creator(*args: object) -> str:
            nonlocal calls
            calls += 1
            return "task_00000000-0000-0000-0000-000000000218"

        service = AutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
        )
        automation = await service.create_automation(
            name="webhook",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.WEBHOOK, webhook_source="github"),
            task_template=_template(),
        )

        with pytest.raises(ContractError) as first_rejection:
            await service.deliver_webhook(
                automation.id,
                event_id="same-event",
                payload={"ref": "main"},
                source="github",
                verified=False,
            )
        assert first_rejection.value.code is ErrorCode.FORBIDDEN

        accepted = await service.deliver_webhook(
            automation.id,
            event_id="same-event",
            payload={"ref": "main"},
            source="github",
            verified=True,
        )
        assert accepted.status is DeliveryStatus.SUCCEEDED
        assert calls == 1

        with pytest.raises(ContractError) as later_spoof:
            await service.deliver_webhook(
                automation.id,
                event_id="same-event",
                payload={"ref": "main"},
                source="github",
                verified=False,
            )
        assert later_spoof.value.code is ErrorCode.FORBIDDEN
        assert calls == 1

        history = await service.list_deliveries(automation.id)
        assert len([item for item in history if item.status is DeliveryStatus.REJECTED]) == 2
        assert len([item for item in history if item.status is DeliveryStatus.SUCCEEDED]) == 1

    asyncio.run(scenario())


def test_restart_recovers_persisted_processing_schedule_delivery(tmp_path: Path) -> None:
    async def scenario() -> None:
        from ai_multi_agent_platform.automation import SqliteAutomationRepository

        scheduled = datetime(2026, 9, 3, 14, 0, tzinfo=UTC)
        repository = SqliteAutomationRepository(tmp_path / "review-recovery.sqlite3")

        async def unused_creator(*args: object) -> str:
            raise AssertionError("seed service must not create a task")

        seed = AutomationService(repository=repository, task_creator=unused_creator)
        automation = await seed.create_automation(
            name="recover",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.ONE_TIME, at=scheduled),
            task_template=_template(),
            now=scheduled - timedelta(minutes=1),
        )
        delivery = replace(
            TriggerDelivery.create(
                automation_id=automation.id,
                trigger_type=TriggerType.ONE_TIME,
                source="schedule",
                dedupe_key=f"schedule:{automation.revision}:{scheduled.isoformat()}",
                fired_at=scheduled,
                payload={"scheduled_for": scheduled.isoformat()},
            ),
            status=DeliveryStatus.PROCESSING,
            attempt=1,
        )
        await repository.save_delivery(delivery)

        calls = 0

        async def creator(*args: object) -> str:
            nonlocal calls
            calls += 1
            return "task_00000000-0000-0000-0000-000000000219"

        restarted = AutomationService(repository=repository, task_creator=creator)
        fired = await restarted.evaluate_due(now=scheduled + timedelta(seconds=1))
        recovered = await restarted.get_delivery(delivery.id)
        refreshed = await restarted.get_automation(automation.id)

        assert len(fired) == 1
        assert recovered.status is DeliveryStatus.SUCCEEDED
        assert recovered.attempt == 1
        assert calls == 1
        assert refreshed.next_evaluation_at is None

    asyncio.run(scenario())


def test_recurring_interval_respects_datetime_resolution_and_skips_arithmetically() -> None:
    at = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="datetime resolution"):
        TriggerDefinition(
            type=TriggerType.RECURRING,
            at=at,
            interval_seconds=1e-9,
        )

    trigger = TriggerDefinition(
        type=TriggerType.RECURRING,
        at=at,
        interval_seconds=1,
    )
    now = at + timedelta(days=30, milliseconds=250)
    assert trigger.next_after(at, now) == at + timedelta(days=30, seconds=1)


def test_completed_one_time_schedule_does_not_refire_after_pause_resume() -> None:
    async def scenario() -> None:
        calls = 0
        scheduled = datetime(2026, 9, 3, 15, 0, tzinfo=UTC)

        async def creator(*args: object) -> str:
            nonlocal calls
            calls += 1
            return "task_00000000-0000-0000-0000-000000000220"

        service = AutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
        )
        automation = await service.create_automation(
            name="once",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.ONE_TIME, at=scheduled),
            task_template=_template(),
            now=scheduled - timedelta(minutes=1),
        )
        await service.evaluate_due(now=scheduled)
        await service.set_state(
            automation.id, AutomationState.PAUSED, now=scheduled + timedelta(seconds=1)
        )
        resumed = await service.set_state(
            automation.id,
            AutomationState.ENABLED,
            now=scheduled + timedelta(seconds=2),
        )
        refired = await service.evaluate_due(now=scheduled + timedelta(minutes=5))

        assert resumed.next_evaluation_at is None
        assert refired == ()
        assert calls == 1

    asyncio.run(scenario())


def test_schedule_advancement_does_not_overwrite_concurrent_trigger_edit() -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        first = datetime(2026, 9, 3, 10, 0, tzinfo=UTC)
        replacement_start = datetime(2026, 9, 3, 20, 0, tzinfo=UTC)

        async def creator(*args: object) -> str:
            entered.set()
            await release.wait()
            return "task_00000000-0000-0000-0000-000000000221"

        service = AutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
        )
        automation = await service.create_automation(
            name="editable",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(
                type=TriggerType.RECURRING,
                at=first,
                interval_seconds=3600,
            ),
            task_template=_template(),
            now=first - timedelta(minutes=1),
        )

        evaluation = asyncio.create_task(service.evaluate_due(now=first))
        await entered.wait()
        updated = await service.update_automation(
            automation.id,
            trigger=TriggerDefinition(
                type=TriggerType.RECURRING,
                at=replacement_start,
                interval_seconds=7200,
            ),
            now=first + timedelta(seconds=1),
        )
        release.set()
        await evaluation
        refreshed = await service.get_automation(automation.id)

        assert refreshed.revision == updated.revision
        assert refreshed.trigger == updated.trigger
        assert refreshed.next_evaluation_at == replacement_start

    asyncio.run(scenario())
