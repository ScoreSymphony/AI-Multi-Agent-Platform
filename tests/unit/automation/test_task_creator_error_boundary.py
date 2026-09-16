from __future__ import annotations

import asyncio

from ai_multi_agent_platform.automation import (
    DeliveryStatus,
    IdentityContext,
    TaskTemplate,
    TriggerDefinition,
    TriggerType,
)
from ai_multi_agent_platform.automation.repository import InMemoryAutomationRepository
from ai_multi_agent_platform.automation.service import AutomationService


def test_unexpected_task_creator_failure_is_redacted_and_terminal() -> None:
    async def scenario() -> None:
        secret = "task-creator-secret-token"

        async def creator(*args: object) -> str:
            del args
            raise RuntimeError(secret)

        service = AutomationService(
            repository=InMemoryAutomationRepository(),
            task_creator=creator,
        )
        automation = await service.create_automation(
            name="TaskCreator boundary",
            description="Exercise unexpected TaskCreator containment",
            identity=IdentityContext(
                principal_ref="user:task-creator-boundary",
                owner_type="user",
                owner_id="task-creator-boundary",
            ),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=TaskTemplate(
                title="Create canonical task",
                objective="Exercise unexpected TaskCreator failure containment",
            ),
        )

        delivery = await service.test_trigger(
            automation.id,
            occurrence_id="unexpected-task-creator-failure",
        )

        assert delivery.status is DeliveryStatus.FAILED
        assert delivery.error_code == "automation_task_creation_failed"
        assert delivery.retryable is False
        assert delivery.next_retry_at is None
        assert delivery.error_message == "automation task creation failed (RuntimeError)"
        assert secret not in (delivery.error_message or "")

    asyncio.run(scenario())
