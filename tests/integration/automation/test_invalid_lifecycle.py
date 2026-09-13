from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.automation import (
    AutomationService,
    AutomationState,
    DeliveryStatus,
    IdentityContext,
    InMemoryAutomationRepository,
    RetryPolicy,
    SqliteAutomationRepository,
    TaskTemplate,
    TriggerDefinition,
    TriggerType,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane import ActorContext, ControlPlane, RequestContext
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


def _identity(owner_id: str = "issue-241") -> IdentityContext:
    return IdentityContext(
        principal_ref=f"user:{owner_id}",
        owner_type="user",
        owner_id=owner_id,
    )


def _template() -> TaskTemplate:
    return TaskTemplate(
        title="Invalid lifecycle",
        objective="Verify issue 241 invalid lifecycle behavior",
    )


async def _task_creator(*args: object) -> str:
    del args
    from ai_multi_agent_platform.domain import new_id

    return new_id("task")


def _manual_service(
    repository: InMemoryAutomationRepository | SqliteAutomationRepository,
) -> AutomationService:
    return AutomationService(repository=repository, task_creator=_task_creator)


def test_invalidation_and_revalidation_preserve_schedule_position_and_prior_state() -> None:
    async def scenario() -> None:
        repository = InMemoryAutomationRepository()
        service = _manual_service(repository)
        created_at = datetime(2026, 9, 4, 4, 0, tzinfo=UTC)
        scheduled_at = created_at + timedelta(hours=1)
        automation = await service.create_automation(
            name="scheduled",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(
                type=TriggerType.RECURRING,
                at=scheduled_at,
                interval_seconds=3600,
            ),
            task_template=_template(),
            now=created_at,
        )
        paused = await service.set_state(
            automation.id,
            AutomationState.PAUSED,
            now=created_at + timedelta(minutes=5),
        )
        next_before = paused.next_evaluation_at
        invalidated = await service.invalidate_automation(
            automation.id,
            reason_code="provider_config_missing",
            now=created_at + timedelta(minutes=10),
        )

        assert invalidated.state is AutomationState.INVALID
        assert invalidated.state_before_invalid is AutomationState.PAUSED
        assert invalidated.invalidation_reason_code == "provider_config_missing"
        assert invalidated.invalidated_at == created_at + timedelta(minutes=10)
        assert invalidated.next_evaluation_at == next_before

        revalidated = await service.revalidate_automation(
            automation.id,
            now=created_at + timedelta(minutes=20),
        )
        assert revalidated.state is AutomationState.PAUSED
        assert revalidated.invalidation_reason_code is None
        assert revalidated.invalidated_at is None
        assert revalidated.state_before_invalid is None
        assert revalidated.next_evaluation_at == next_before

    asyncio.run(scenario())


def test_repeated_invalidation_keeps_original_prior_state_and_timestamp() -> None:
    async def scenario() -> None:
        service = _manual_service(InMemoryAutomationRepository())
        started = datetime(2026, 9, 4, 5, 0, tzinfo=UTC)
        automation = await service.create_automation(
            name="repeat invalid",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=_template(),
            now=started,
        )
        first = await service.invalidate_automation(
            automation.id,
            reason_code="configuration_missing",
            now=started + timedelta(minutes=1),
        )
        second = await service.invalidate_automation(
            automation.id,
            reason_code="configuration_still_missing",
            now=started + timedelta(minutes=2),
        )

        assert second.state_before_invalid is AutomationState.ENABLED
        assert second.invalidated_at == first.invalidated_at
        assert second.invalidation_reason_code == "configuration_still_missing"

    asyncio.run(scenario())


def test_invalid_state_requires_explicit_lifecycle_operations() -> None:
    async def scenario() -> None:
        service = _manual_service(InMemoryAutomationRepository())
        now = datetime(2026, 9, 4, 6, 0, tzinfo=UTC)
        automation = await service.create_automation(
            name="explicit invalid",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=_template(),
            now=now,
        )
        with pytest.raises(ContractError) as direct_invalid:
            await service.set_state(automation.id, AutomationState.INVALID, now=now)
        assert direct_invalid.value.code is ErrorCode.INVALID_REQUEST

        invalidated = await service.invalidate_automation(
            automation.id,
            reason_code="invalid_reference",
            now=now,
        )
        with pytest.raises(ContractError) as direct_enable:
            await service.set_state(invalidated.id, AutomationState.ENABLED, now=now)
        assert direct_enable.value.code is ErrorCode.CONFLICT

    asyncio.run(scenario())


def test_transient_delivery_failure_does_not_invalidate_automation() -> None:
    async def scenario() -> None:
        async def transient_creator(*args: object) -> str:
            del args
            raise ContractError(ErrorCode.BACKEND_ERROR, "temporary", retryable=True)

        repository = InMemoryAutomationRepository()
        service = AutomationService(repository=repository, task_creator=transient_creator)
        now = datetime(2026, 9, 4, 7, 0, tzinfo=UTC)
        automation = await service.create_automation(
            name="transient",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=_template(),
            retry_policy=RetryPolicy(max_attempts=3, base_backoff_seconds=1),
            now=now,
        )
        delivery = await service.test_trigger(
            automation.id,
            occurrence_id="transient",
            fired_at=now,
        )

        assert delivery.status is DeliveryStatus.FAILED
        assert delivery.retryable is True
        refreshed = await service.get_automation(automation.id)
        assert refreshed.state is AutomationState.ENABLED
        assert refreshed.invalidation_reason_code is None

    asyncio.run(scenario())


def test_stable_configuration_delivery_failure_auto_invalidates() -> None:
    async def scenario() -> None:
        async def invalid_creator(*args: object) -> str:
            del args
            raise ContractError(ErrorCode.INVALID_CONFIGURATION, "bad provider config")

        repository = InMemoryAutomationRepository()
        service = AutomationService(repository=repository, task_creator=invalid_creator)
        now = datetime(2026, 9, 4, 8, 0, tzinfo=UTC)
        automation = await service.create_automation(
            name="stable invalid",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=_template(),
            now=now,
        )
        delivery = await service.test_trigger(
            automation.id,
            occurrence_id="stable-invalid",
            fired_at=now,
        )

        assert delivery.status is DeliveryStatus.FAILED
        assert delivery.retryable is False
        refreshed = await service.get_automation(automation.id)
        assert refreshed.state is AutomationState.INVALID
        assert refreshed.state_before_invalid is AutomationState.ENABLED
        assert refreshed.invalidation_reason_code == "delivery_invalid_configuration"

    asyncio.run(scenario())


def test_invalid_webhook_rejects_before_delivery_admission() -> None:
    async def scenario() -> None:
        repository = InMemoryAutomationRepository()
        service = _manual_service(repository)
        now = datetime(2026, 9, 4, 9, 0, tzinfo=UTC)
        automation = await service.create_automation(
            name="invalid webhook",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(
                type=TriggerType.WEBHOOK,
                webhook_source="github",
            ),
            task_template=_template(),
            now=now,
        )
        await service.invalidate_automation(
            automation.id,
            reason_code="webhook_secret_missing",
            now=now,
        )

        with pytest.raises(ContractError) as rejected:
            await service.deliver_webhook(
                automation.id,
                event_id="event-1",
                payload={"kind": "push"},
                source="github",
                verified=True,
                fired_at=now,
            )
        assert rejected.value.code is ErrorCode.INVALID_CONFIGURATION
        assert await service.list_deliveries(automation.id) == ()

    asyncio.run(scenario())


def test_sqlite_roundtrip_preserves_invalid_lifecycle_and_legacy_invalid_is_recoverable(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        path = tmp_path / "automation.sqlite3"
        repository = SqliteAutomationRepository(path)
        service = _manual_service(repository)
        now = datetime(2026, 9, 4, 10, 0, tzinfo=UTC)
        automation = await service.create_automation(
            name="durable invalid",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=_template(),
            now=now,
        )
        invalidated = await service.invalidate_automation(
            automation.id,
            reason_code="missing_external_reference",
            now=now + timedelta(minutes=1),
        )

        restarted = _manual_service(SqliteAutomationRepository(path))
        loaded = await restarted.get_automation(automation.id)
        assert loaded == invalidated
        recovered = await restarted.revalidate_automation(
            automation.id,
            now=now + timedelta(minutes=2),
        )
        assert recovered.state is AutomationState.ENABLED

        legacy = await restarted.create_automation(
            name="legacy invalid",
            description="",
            identity=_identity("legacy"),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=_template(),
            now=now,
        )
        with sqlite3.connect(path) as connection:
            row = connection.execute(
                "SELECT payload FROM automations WHERE id = ?",
                (legacy.id,),
            ).fetchone()
            assert row is not None
            payload = json.loads(str(row[0]))
            payload["state"] = "invalid"
            payload.pop("invalidation_reason_code", None)
            payload.pop("invalidated_at", None)
            payload.pop("state_before_invalid", None)
            connection.execute(
                "UPDATE automations SET payload = ? WHERE id = ?",
                (json.dumps(payload, sort_keys=True, separators=(",", ":")), legacy.id),
            )

        migrated = await restarted.get_automation(legacy.id)
        assert migrated.state is AutomationState.INVALID
        assert migrated.invalidation_reason_code == "legacy_invalid_state"
        assert migrated.state_before_invalid is AutomationState.DISABLED
        legacy_revalidated = await restarted.revalidate_automation(
            legacy.id,
            now=now + timedelta(minutes=3),
        )
        assert legacy_revalidated.state is AutomationState.DISABLED

    asyncio.run(scenario())


def test_control_plane_exposes_and_authorizes_invalid_lifecycle_commands() -> None:
    async def scenario() -> None:
        repository = InMemoryKernelRepository()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=repository,
        )
        control_plane = ControlPlane(
            kernel=kernel,
            events=repository,
            authorization=FakeAuthorizationProvider(),
        )
        context = RequestContext(
            request_id="request-invalid-lifecycle",
            correlation_id="correlation-invalid-lifecycle",
            actor=ActorContext(
                principal_ref="user:issue-241",
                owner_type="user",
                owner_id="issue-241",
            ),
            idempotency_key="issue-241-invalid-lifecycle",
        )
        automation = await control_plane.automation_service.create_automation(
            name="control-plane invalid",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=_template(),
        )

        invalid = await control_plane.execute_command(
            context,
            "automation.invalidate",
            automation.id,
            {"reason_code": "operator_invalidated"},
        )
        assert invalid["state"] == "invalid"
        assert invalid["invalidation_reason_code"] == "operator_invalidated"
        assert invalid["state_before_invalid"] == "enabled"

        queried = await control_plane.get_extension_resource(
            context,
            "automations",
            automation.id,
        )
        assert queried["invalidation_reason_code"] == "operator_invalidated"

        revalidation_context = RequestContext(
            request_id="request-revalidate-lifecycle",
            correlation_id="correlation-invalid-lifecycle",
            actor=context.actor,
            idempotency_key="issue-241-revalidate-lifecycle",
        )
        recovered = await control_plane.execute_command(
            revalidation_context,
            "automation.revalidate",
            automation.id,
            {},
        )
        assert recovered["state"] == "enabled"
        assert recovered["invalidation_reason_code"] is None
        assert recovered["state_before_invalid"] is None

    asyncio.run(scenario())
