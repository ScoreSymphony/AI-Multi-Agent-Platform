"""Canonical notification and user-attention domain for Issue #75."""

from .delivery import (
    DeliveryAttempt,
    DeliveryAttemptRepository,
    DeliveryResult,
    DeliveryStatus,
    InMemoryDeliveryAttemptRepository,
    NotificationDeliveryChannel,
    NotificationDeliveryCoordinator,
    UnavailableDeliveryChannel,
)
from .delivery_sqlite import SqliteDeliveryAttemptRepository
from .events import NotificationProjectingEventProvider, ProjectionFailureSink
from .integrations import (
    approval_required_candidate,
    approval_resolved_candidate,
    budget_threshold_candidate,
    canonical_attention_candidate,
    membership_attention_candidate,
    verification_attention_candidate,
)
from .live import (
    NotificationLiveEvent,
    NotificationLiveHub,
    fanout_notification_event_sinks,
)
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
    external_delivery_allowed,
    preference_allows,
)
from .preferences_sqlite import SqliteNotificationPreferenceRepository
from .recipients import (
    AllowAllRecipientEligibilityGuard,
    EventOwnerRecipientResolver,
    RecipientEligibilityGuard,
    RecipientResolver,
    StaticRecipientEligibilityGuard,
    StaticRecipientResolver,
)
from .repository import InMemoryNotificationRepository, NotificationRepository
from .rules import NotificationRule, TaskTerminalNotificationRule
from .runtime import (
    InMemoryNotificationRuntimeState,
    NotificationRuntime,
    NotificationRuntimeState,
    NotificationRuntimeTick,
    SqliteNotificationRuntimeState,
)
from .service import NotificationEventSink, NotificationService
from .sqlite import SqliteNotificationRepository

__all__ = [
    "AllowAllRecipientEligibilityGuard",
    "DeliveryAttempt",
    "DeliveryAttemptRepository",
    "DeliveryResult",
    "DeliveryStatus",
    "EventOwnerRecipientResolver",
    "InMemoryDeliveryAttemptRepository",
    "InMemoryNotificationPreferenceRepository",
    "InMemoryNotificationRepository",
    "InMemoryNotificationRuntimeState",
    "Notification",
    "NotificationAction",
    "NotificationCandidate",
    "NotificationCategory",
    "NotificationDeliveryChannel",
    "NotificationDeliveryCoordinator",
    "NotificationEventSink",
    "NotificationLiveEvent",
    "NotificationLiveHub",
    "NotificationPreference",
    "NotificationPreferenceRepository",
    "NotificationProjectingEventProvider",
    "NotificationQuery",
    "NotificationRepository",
    "NotificationRule",
    "NotificationRuntime",
    "NotificationRuntimeState",
    "NotificationRuntimeTick",
    "NotificationService",
    "NotificationSeverity",
    "NotificationState",
    "ProjectionFailureSink",
    "RecipientEligibilityGuard",
    "RecipientRef",
    "RecipientResolver",
    "RecipientType",
    "SourceRef",
    "SqliteDeliveryAttemptRepository",
    "SqliteNotificationPreferenceRepository",
    "SqliteNotificationRepository",
    "SqliteNotificationRuntimeState",
    "StaticRecipientEligibilityGuard",
    "StaticRecipientResolver",
    "TaskTerminalNotificationRule",
    "UnavailableDeliveryChannel",
    "approval_required_candidate",
    "approval_resolved_candidate",
    "budget_threshold_candidate",
    "canonical_attention_candidate",
    "external_delivery_allowed",
    "fanout_notification_event_sinks",
    "membership_attention_candidate",
    "preference_allows",
    "verification_attention_candidate",
]
