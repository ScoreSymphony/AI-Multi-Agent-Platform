"""Canonical notification and user-attention domain for Issue #75."""

from .delivery import (
    DeliveryResult,
    DeliveryStatus,
    NotificationDeliveryChannel,
    UnavailableDeliveryChannel,
)
from .events import NotificationProjectingEventProvider, ProjectionFailureSink
from .integrations import approval_required_candidate, budget_threshold_candidate
from .models import (
    Notification,
    NotificationAction,
    NotificationCandidate,
    NotificationCategory,
    NotificationPreference,
    NotificationQuery,
    NotificationSeverity,
    NotificationState,
    RecipientRef,
    RecipientType,
    SourceRef,
)
from .preferences import (
    InMemoryNotificationPreferenceRepository,
    NotificationPreferenceRepository,
    preference_allows,
)
from .recipients import EventOwnerRecipientResolver, RecipientResolver, StaticRecipientResolver
from .repository import InMemoryNotificationRepository, NotificationRepository
from .rules import NotificationRule, TaskTerminalNotificationRule
from .service import NotificationEventSink, NotificationService

__all__ = [
    "DeliveryResult",
    "DeliveryStatus",
    "EventOwnerRecipientResolver",
    "InMemoryNotificationPreferenceRepository",
    "InMemoryNotificationRepository",
    "Notification",
    "NotificationAction",
    "NotificationCandidate",
    "NotificationCategory",
    "NotificationDeliveryChannel",
    "NotificationEventSink",
    "NotificationPreference",
    "NotificationPreferenceRepository",
    "NotificationProjectingEventProvider",
    "NotificationQuery",
    "NotificationRepository",
    "NotificationRule",
    "NotificationService",
    "NotificationSeverity",
    "NotificationState",
    "ProjectionFailureSink",
    "RecipientRef",
    "RecipientResolver",
    "RecipientType",
    "SourceRef",
    "StaticRecipientResolver",
    "TaskTerminalNotificationRule",
    "UnavailableDeliveryChannel",
    "approval_required_candidate",
    "budget_threshold_candidate",
    "preference_allows",
]
