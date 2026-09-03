"""Canonical automation domain for schedules, webhooks and platform-event triggers."""

from .hardened_service import (
    AutomationService,
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
from .service import AutomationEventSink, ReferenceScheduler, TaskCreator

__all__ = [
    "Automation",
    "AutomationEventSink",
    "AutomationRepository",
    "AutomationService",
    "AutomationState",
    "DeliveryStatus",
    "IdentityContext",
    "InMemoryAutomationRepository",
    "MissedSchedulePolicy",
    "OverlapPolicy",
    "ReferenceScheduler",
    "RetryPolicy",
    "SqliteAutomationRepository",
    "TaskCreator",
    "TaskTemplate",
    "TriggerDefinition",
    "TriggerDelivery",
    "TriggerType",
    "WebhookPayloadValidator",
    "automation_change_actor",
    "automation_creation_idempotency_key",
]
