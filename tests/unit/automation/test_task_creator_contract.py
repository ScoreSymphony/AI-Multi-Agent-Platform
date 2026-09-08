from __future__ import annotations

import asyncio
from typing import Any

from ai_multi_agent_platform.automation import (
    Automation,
    DeliveryStatus,
    IdentityContext,
    NO_TASK_REQUIRED,
    TaskTemplate,
    TriggerDefinition,
    TriggerType,
)
from ai_multi_agent_platform.automation.repository import InMemoryAutomationRepository
from ai_multi_agent_platform.automation.service import AutomationService
from ai_multi_agent_platform.contracts import ErrorCode


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _automation(service: AutomationService) -> Automation:
    return await service.create_automation(
        name="Task creator contract",
        description="Exercise the canonical Automation TaskCreator boundary",
        identity=IdentityContext(
            principal_ref="user:automation-contract",
            owner_type="user",
            owner_id="automation-contract",
        ),
        trigger=TriggerDefinition(type=TriggerType.MANUAL),
        task_template=TaskTemplate(
            title="Create canonical work",
            objective="Create exactly one canonical Task when work is required",
        ),
    )


def test_ordinary_task_creator_cannot_succeed_with_none() -> None:
    async def scenario() -> None:
        async def invalid_creator(*args: object) -> Any:
            del args
            return None

        service = AutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=invalid_creator,
        )
        automation = await _automation(service)

        delivery = await service.test_trigger(
            automation.id,
            occurrence_id="ordinary-none",
        )

        assert delivery.status is DeliveryStatus.FAILED
        assert delivery.generated_task_id is None
        assert delivery.error_code == ErrorCode.CONTRACT_VIOLATION.value

    _run(scenario())


def test_explicit_no_task_signal_succeeds_without_fabricating_task() -> None:
    async def scenario() -> None:
        async def no_task_required(*args: object) -> str:
            del args
            return NO_TASK_REQUIRED

        service = AutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=no_task_required,
        )
        automation = await _automation(service)

        delivery = await service.test_trigger(
            automation.id,
            occurrence_id="explicit-no-task",
        )

        assert delivery.status is DeliveryStatus.SUCCEEDED
        assert delivery.generated_task_id is None
        assert delivery.error_code is None

    _run(scenario())
