"""Canonical automation domain for schedules, webhooks and platform-event triggers."""

from .hardened_service import (
    WebhookPayloadValidator,
    automation_change_actor,
    automation_creation_idempotency_key,
)
from .models import (
    Automation,
    AutomationState,
    DeliveryStatus,
    IdentityContext,
    MissedSchedulePolicy,
    OverlapPolicy,
    RetryPolicy,
    TaskTemplate,
    TriggerDefinition,
    TriggerDelivery,
    TriggerType,
)
from .repository import (
    AutomationRepository,
    InMemoryAutomationRepository,
    SqliteAutomationRepository,
)
from .runtime import (
    AutomationCommandRecord,
    AutomationRuntime,
    AutomationRuntimeState,
    AutomationRuntimeTick,
    InMemoryAutomationRuntimeState,
    SqliteAutomationRuntimeState,
)
from .runtime_service import AutomationService
from .service import AutomationEventSink, ReferenceScheduler, TaskCreator

__all__ = [
    "Automation",
    "AutomationCommandRecord",
    "AutomationEventSink",
    "AutomationRepository",
    "AutomationRuntime",
    "AutomationRuntimeState",
    "AutomationRuntimeTick",
    "AutomationService",
    "AutomationState",
    "DeliveryStatus",
    "IdentityContext",
    "InMemoryAutomationRepository",
    "InMemoryAutomationRuntimeState",
    "MissedSchedulePolicy",
    "OverlapPolicy",
    "ReferenceScheduler",
    "RetryPolicy",
    "SqliteAutomationRepository",
    "SqliteAutomationRuntimeState",
    "TaskCreator",
    "TaskTemplate",
    "TriggerDefinition",
    "TriggerDelivery",
    "TriggerType",
    "WebhookPayloadValidator",
    "automation_change_actor",
    "automation_creation_idempotency_key",
]
