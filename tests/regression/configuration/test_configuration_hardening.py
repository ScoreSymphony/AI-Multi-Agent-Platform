from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.automation import (
    AutomationService,
    AutomationState,
    DeliveryStatus,
    IdentityContext,
    InMemoryAutomationRepository,
    ReferenceScheduler,
    RetryPolicy,
    TaskTemplate,
    TriggerDefinition,
    TriggerType,
    automation_change_actor,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane import ActorContext, ControlPlane, RequestContext
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


def _identity() -> IdentityContext:
    return IdentityContext(
        principal_ref="user:issue18",
        owner_type="user",
        owner_id="issue18",
    )


def _template() -> TaskTemplate:
    return TaskTemplate(title="Automation hardening", objective="Exercise canonical task admission")


def _control_plane(*, event_sink: object) -> ControlPlane:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    return ControlPlane(
        kernel=kernel,
        events=repository,
        authorization=FakeAuthorizationProvider(allowed=True),
        automation_event_sink=event_sink,  # type: ignore[arg-type]
    )


def test_configuration_changes_and_schedule_delivery_emit_auditable_metadata() -> None:
    async def scenario() -> None:
        events: list[dict[str, JsonValue]] = []
        scheduled = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)

        async def sink(event: dict[str, JsonValue]) -> None:
            events.append(event)

        async def creator(*args: object) -> str:
            return "task_00000000-0000-0000-0000-000000000181"

        service = AutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
            event_sink=sink,
        )
        automation = await service.create_automation(
            name="audited",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(
                type=TriggerType.ONE_TIME,
                timezone="Europe/Berlin",
                at=scheduled,
            ),
            task_template=_template(),
            now=scheduled - timedelta(minutes=1),
        )
        with automation_change_actor("user:administrator"):
            updated = await service.update_automation(
                automation.id,
                description="changed",
                now=scheduled - timedelta(seconds=30),
            )
        paused = await service.set_state(
            automation.id,
            AutomationState.PAUSED,
            now=scheduled - timedelta(seconds=20),
        )
        await service.set_state(
            automation.id,
            AutomationState.ENABLED,
            now=scheduled - timedelta(seconds=10),
        )
        fired = await ReferenceScheduler(service).tick(now=scheduled)

        configuration = [event for event in events if event["type"] == "automation.configuration"]
        delivery = [event for event in events if event["type"] == "automation.delivery"]

        assert [event["action"] for event in configuration] == [
            "created",
            "updated",
            "state_changed",
            "state_changed",
        ]
        assert configuration[1]["automation_revision"] == updated.revision
        assert configuration[1]["changed_fields"] == ["description"]
        assert configuration[1]["automation_principal_ref"] == "user:issue18"
        assert configuration[1]["changed_by_principal_ref"] == "user:administrator"
        assert configuration[2]["previous_state"] == AutomationState.ENABLED.value
        assert configuration[2]["state"] == paused.state.value
        assert fired[0].status is DeliveryStatus.SUCCEEDED
        assert delivery[-1]["trigger_delivery_id"] == fired[0].id
        assert delivery[-1]["schedule_timezone"] == "Europe/Berlin"
        assert delivery[-1]["schedule_at"] == scheduled.isoformat()
        assert delivery[-1]["schedule_missed_policy"] == "coalesce"
        assert delivery[-1]["dedupe_outcome"] == "succeeded"

    asyncio.run(scenario())


def test_control_plane_preserves_authenticated_configuration_actor() -> None:
    async def scenario() -> None:
        events: list[dict[str, JsonValue]] = []

        async def sink(event: dict[str, JsonValue]) -> None:
            events.append(event)

        control_plane = _control_plane(event_sink=sink)
        creator = RequestContext(
            request_id="create-request",
            correlation_id="create-request",
            actor=ActorContext(
                principal_ref="user:owner",
                owner_type="user",
                owner_id="owner",
            ),
            idempotency_key="create-idempotency",
        )
        created = await control_plane.execute_command(
            creator,
            "automation.create",
            "automations",
            {
                "name": "actor audit",
                "trigger": {"type": "manual"},
                "task_template": {
                    "title": "Actor audit",
                    "objective": "Prove authenticated mutation actor",
                },
            },
        )
        automation_id = created["id"]
        assert isinstance(automation_id, str)

        administrator = RequestContext(
            request_id="update-request",
            correlation_id="update-request",
            actor=ActorContext(
                principal_ref="user:administrator",
                owner_type="user",
                owner_id="administrator",
            ),
            idempotency_key="update-idempotency",
        )
        await control_plane.execute_command(
            administrator,
            "automation.update",
            automation_id,
            {"description": "changed by administrator"},
        )

        configuration = [event for event in events if event["type"] == "automation.configuration"]
        assert configuration[0]["automation_principal_ref"] == "user:owner"
        assert configuration[0]["changed_by_principal_ref"] == "user:owner"
        assert configuration[1]["automation_principal_ref"] == "user:owner"
        assert configuration[1]["changed_by_principal_ref"] == "user:administrator"

    asyncio.run(scenario())


def test_webhook_rate_limit_preserves_duplicate_idempotency_and_records_rejection() -> None:
    async def scenario() -> None:
        calls = 0
        clock = 100.0

        def rate_clock() -> float:
            return clock

        async def creator(*args: object) -> str:
            nonlocal calls
            calls += 1
            return "task_00000000-0000-0000-0000-000000000182"

        service = AutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
            max_webhook_deliveries_per_window=1,
            webhook_rate_window_seconds=60,
            webhook_rate_clock=rate_clock,
        )
        automation = await service.create_automation(
            name="rate limited",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.WEBHOOK, webhook_source="github"),
            task_template=_template(),
        )
        first = await service.deliver_webhook(
            automation.id,
            event_id="delivery-1",
            payload={"ref": "main"},
            source="github",
            verified=True,
        )
        duplicate = await service.deliver_webhook(
            automation.id,
            event_id="delivery-1",
            payload={"ref": "main"},
            source="github",
            verified=True,
        )

        try:
            await service.deliver_webhook(
                automation.id,
                event_id="delivery-2",
                payload={"ref": "other"},
                source="github",
                verified=True,
            )
        except ContractError as exc:
            assert exc.code is ErrorCode.RATE_LIMITED
            assert exc.retryable is True
        else:
            raise AssertionError("second unique webhook inside the window must be rate limited")

        history = await service.list_deliveries(automation.id)
        rejected = [item for item in history if item.status is DeliveryStatus.REJECTED]
        assert duplicate.id == first.id
        assert calls == 1
        assert len(rejected) == 1
        assert rejected[0].error_code == "webhook_rate_limited"

    asyncio.run(scenario())


def test_webhook_payload_validator_rejection_is_queryable_and_never_creates_task() -> None:
    async def scenario() -> None:
        calls = 0

        async def creator(*args: object) -> str:
            nonlocal calls
            calls += 1
            return "task_00000000-0000-0000-0000-000000000183"

        async def validator(
            automation: object,
            payload: dict[str, JsonValue],
        ) -> None:
            del automation
            if "required" not in payload:
                raise ContractError(ErrorCode.INVALID_REQUEST, "required field missing")

        service = AutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
            webhook_payload_validator=validator,
        )
        automation = await service.create_automation(
            name="schema checked",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.WEBHOOK, webhook_source="github"),
            task_template=_template(),
        )

        try:
            await service.deliver_webhook(
                automation.id,
                event_id="invalid-payload",
                payload={"other": "value"},
                source="github",
                verified=True,
            )
        except ContractError as exc:
            assert exc.code is ErrorCode.INVALID_REQUEST
        else:
            raise AssertionError("payload validator failure must reject the webhook")

        history = await service.list_deliveries(automation.id)
        assert calls == 0
        assert len(history) == 1
        assert history[0].status is DeliveryStatus.REJECTED
        assert history[0].error_code == ErrorCode.INVALID_REQUEST.value

    asyncio.run(scenario())


def test_webhook_source_mismatch_is_recorded_as_rejected_delivery() -> None:
    async def scenario() -> None:
        async def creator(*args: object) -> str:
            raise AssertionError("source mismatch must never create a task")

        service = AutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
        )
        automation = await service.create_automation(
            name="source bound",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.WEBHOOK, webhook_source="github"),
            task_template=_template(),
        )

        try:
            await service.deliver_webhook(
                automation.id,
                event_id="wrong-source",
                payload={},
                source="gitlab",
                verified=True,
            )
        except ContractError as exc:
            assert exc.code is ErrorCode.FORBIDDEN
        else:
            raise AssertionError("unexpected webhook source must be forbidden")

        history = await service.list_deliveries(automation.id)
        assert len(history) == 1
        assert history[0].status is DeliveryStatus.REJECTED
        assert history[0].error_code == "webhook_source_mismatch"

    asyncio.run(scenario())


def test_disabled_automation_and_retry_exhaustion_need_no_frontend_or_broker() -> None:
    async def scenario() -> None:
        calls = 0

        async def creator(*args: object) -> str:
            nonlocal calls
            calls += 1
            raise ContractError(ErrorCode.UNAVAILABLE, "temporary", retryable=True)

        service = AutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
        )
        automation = await service.create_automation(
            name="standalone reference path",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=_template(),
            retry_policy=RetryPolicy(max_attempts=1, base_backoff_seconds=0),
        )
        failed = await service.test_trigger(automation.id, occurrence_id="failure")
        exhausted = await service.retry_delivery(failed.id)
        await service.set_state(automation.id, AutomationState.DISABLED)

        try:
            await service.test_trigger(automation.id, occurrence_id="disabled")
        except ContractError as exc:
            assert exc.code is ErrorCode.CONFLICT
        else:
            raise AssertionError("disabled automation must reject new manual delivery")

        assert failed.status is DeliveryStatus.FAILED
        assert exhausted.id == failed.id
        assert exhausted.attempt == 1
        assert calls == 1

    asyncio.run(scenario())
