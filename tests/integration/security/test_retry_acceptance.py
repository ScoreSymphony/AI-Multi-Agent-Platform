from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

from ai_multi_agent_platform.automation import (
    DeliveryStatus,
    IdentityContext,
    InMemoryAutomationRepository,
    RetryPolicy,
    TaskTemplate,
    TriggerDefinition,
    TriggerDelivery,
    TriggerType,
)
from ai_multi_agent_platform.automation.service import AutomationService as BaseAutomationService
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane import ActorContext, ControlPlane, RequestContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


def _identity() -> IdentityContext:
    return IdentityContext(
        principal_ref="user:issue-241",
        owner_type="user",
        owner_id="issue-241",
    )


def _template() -> TaskTemplate:
    return TaskTemplate(
        title="Retry acceptance",
        objective="Verify issue 241 acceptance behavior",
    )


def test_retry_reenters_task_creator_after_authorization_change() -> None:
    async def scenario() -> None:
        now = datetime(2026, 9, 4, 4, 0, tzinfo=UTC)
        authorization_allowed = True
        calls = 0

        async def creator(*args: object) -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ContractError(ErrorCode.BACKEND_ERROR, "temporary", retryable=True)
            if not authorization_allowed:
                raise ContractError(ErrorCode.FORBIDDEN, "authorization changed")
            return new_id("task")

        service = BaseAutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
            clock=lambda: now,
        )
        automation = await service.create_automation(
            name="authorization recheck",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=_template(),
            retry_policy=RetryPolicy(max_attempts=3, base_backoff_seconds=0),
            now=now,
        )
        failed = await service.test_trigger(
            automation.id,
            occurrence_id="authorization-change",
            fired_at=now,
        )
        assert failed.retryable is True
        assert failed.next_retry_at == now

        authorization_allowed = False
        retried = await service.retry_due_deliveries(now=now)

        assert len(retried) == 1
        denied = retried[0]
        assert denied.id == failed.id
        assert denied.attempt == 2
        assert denied.status is DeliveryStatus.FAILED
        assert denied.error_code == ErrorCode.FORBIDDEN.value
        assert denied.retryable is False
        assert denied.next_retry_at is None
        assert calls == 2
        assert await service.retry_due_deliveries(now=now) == ()

    asyncio.run(scenario())


def test_retry_exhaustion_metadata_is_queryable_through_authorized_control_plane() -> None:
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
        now = datetime(2026, 9, 4, 4, 15, tzinfo=UTC)
        automation = await control_plane.automation_service.create_automation(
            name="queryable exhaustion",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=_template(),
            retry_policy=RetryPolicy(max_attempts=3, base_backoff_seconds=1),
            now=now,
        )
        delivery = replace(
            TriggerDelivery.create(
                automation_id=automation.id,
                trigger_type=TriggerType.MANUAL,
                source="manual-test",
                dedupe_key="manual:queryable-exhaustion",
                fired_at=now,
            ),
            status=DeliveryStatus.FAILED,
            attempt=3,
            error_code=ErrorCode.TIMEOUT.value,
            error_message="safe timeout category",
            retryable=True,
            last_failed_at=now,
            retry_exhausted_at=now,
        )
        await control_plane.automation_service.repository.save_delivery(delivery)

        context = RequestContext(
            request_id="request-issue-241-query",
            correlation_id="correlation-issue-241-query",
            actor=ActorContext(
                principal_ref="user:issue-241",
                owner_type="user",
                owner_id="issue-241",
            ),
        )
        resource = await control_plane.get_extension_resource(
            context,
            "automation-deliveries",
            delivery.id,
        )

        assert resource["id"] == delivery.id
        assert resource["retryable"] is True
        assert resource["last_failed_at"] == now.isoformat()
        assert resource["next_retry_at"] is None
        assert resource["retry_exhausted_at"] == now.isoformat()
        assert resource["owner_ref"] == {"type": "user", "id": "issue-241"}

    asyncio.run(scenario())
