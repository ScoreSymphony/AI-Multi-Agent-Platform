from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

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
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import new_id


async def _task_creator(*args: object) -> str:
    del args
    return new_id("task")


def _identity() -> IdentityContext:
    return IdentityContext(
        principal_ref="user:issue-241-revalidation",
        owner_type="user",
        owner_id="issue-241-revalidation",
    )


def _template() -> TaskTemplate:
    return TaskTemplate(title="Revalidate", objective="Exercise revalidation failure paths")


class _TransientRevalidationService(AutomationService):
    async def _validate_configuration_for_revalidation(self, automation: Automation) -> None:
        del automation
        raise ContractError(ErrorCode.BACKEND_ERROR, "temporary validator outage", retryable=True)


class _PermanentRevalidationService(AutomationService):
    async def _validate_configuration_for_revalidation(self, automation: Automation) -> None:
        del automation
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, "configuration still invalid")


def test_transient_revalidation_failure_keeps_original_invalid_metadata_and_defers() -> None:
    async def scenario() -> None:
        events: list[dict[str, JsonValue]] = []

        async def sink(event: dict[str, JsonValue]) -> None:
            events.append(event)

        repository = InMemoryAutomationRepository()
        service = _TransientRevalidationService(
            repository=repository,
            task_creator=_task_creator,
            event_sink=sink,
        )
        now = datetime(2026, 9, 4, 11, 0, tzinfo=UTC)
        automation = await service.create_automation(
            name="transient revalidation",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=_template(),
            now=now,
        )
        invalidated = await service.invalidate_automation(
            automation.id,
            reason_code="provider_reference_missing",
            now=now,
        )

        with pytest.raises(ContractError) as deferred:
            await service.revalidate_automation(automation.id, now=now)
        assert deferred.value.code is ErrorCode.BACKEND_ERROR
        assert deferred.value.retryable is True

        retained = await service.get_automation(automation.id)
        assert retained == invalidated
        assert retained.state is AutomationState.INVALID
        assert any(
            event.get("type") == "automation.lifecycle"
            and event.get("action") == "revalidation_deferred"
            for event in events
        )

    asyncio.run(scenario())


def test_permanent_revalidation_failure_keeps_invalid_and_updates_safe_reason() -> None:
    async def scenario() -> None:
        events: list[dict[str, JsonValue]] = []

        async def sink(event: dict[str, JsonValue]) -> None:
            events.append(event)

        repository = InMemoryAutomationRepository()
        service = _PermanentRevalidationService(
            repository=repository,
            task_creator=_task_creator,
            event_sink=sink,
        )
        now = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
        automation = await service.create_automation(
            name="permanent revalidation",
            description="",
            identity=_identity(),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=_template(),
            now=now,
        )
        invalidated = await service.invalidate_automation(
            automation.id,
            reason_code="provider_reference_missing",
            now=now,
        )

        with pytest.raises(ContractError) as failed:
            await service.revalidate_automation(automation.id, now=now)
        assert failed.value.code is ErrorCode.INVALID_CONFIGURATION
        assert failed.value.retryable is False

        retained = await service.get_automation(automation.id)
        assert retained.state is AutomationState.INVALID
        assert retained.state_before_invalid is AutomationState.ENABLED
        assert retained.invalidated_at == invalidated.invalidated_at
        assert retained.invalidation_reason_code == "revalidation_invalid_configuration"
        assert any(
            event.get("type") == "automation.lifecycle"
            and event.get("action") == "revalidation_failed"
            and event.get("invalidation_reason_code") == "revalidation_invalid_configuration"
            for event in events
        )

    asyncio.run(scenario())
