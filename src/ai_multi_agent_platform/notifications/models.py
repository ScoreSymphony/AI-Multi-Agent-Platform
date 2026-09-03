"""Canonical notification value types for issue #75."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import cast

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import new_id, validate_id
from ai_multi_agent_platform.security.redaction import redact_sensitive


def utc_now() -> datetime:
    return datetime.now(UTC)


class NotificationSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class NotificationCategory(StrEnum):
    TASK = "task"
    APPROVAL = "approval"
    VERIFICATION = "verification"
    AGENT_INPUT = "agent_input"
    DEADLINE = "deadline"
    ASSIGNMENT = "assignment"
    DEPENDENCY = "dependency"
    WORKER = "worker"
    AUTOMATION = "automation"
    SECURITY = "security"
    RESOURCE = "resource"
    CONNECTOR = "connector"
    MEMBERSHIP = "membership"
    GENERAL = "general"


class RecipientType(StrEnum):
    USER = "user"
    TEAM = "team"
    ORGANIZATION = "organization"


class NotificationState(StrEnum):
    UNREAD = "unread"
    READ = "read"
    ACKNOWLEDGED = "acknowledged"
    DISMISSED = "dismissed"
    ARCHIVED = "archived"


@dataclass(frozen=True, slots=True)
class RecipientRef:
    type: RecipientType
    id: str

    def __post_init__(self) -> None:
        validate_id(self.id, self.type.value)


@dataclass(frozen=True, slots=True)
class SourceRef:
    resource_type: str
    resource_id: str

    def __post_init__(self) -> None:
        if not self.resource_type.strip() or not self.resource_id.strip():
            raise ValueError("source resource type/id must not be blank")


@dataclass(frozen=True, slots=True)
class NotificationAction:
    action_id: str
    label: str
    command: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    href: str | None = None

    def __post_init__(self) -> None:
        if not self.action_id.strip() or not self.label.strip():
            raise ValueError("notification action id/label must not be blank")
        if self.command is not None and not self.command.strip():
            raise ValueError("notification action command must not be blank")
        if self.href is not None and not self.href.strip():
            raise ValueError("notification action href must not be blank")
        if (self.resource_type is None) != (self.resource_id is None):
            raise ValueError("resource_type and resource_id must be provided together")


@dataclass(frozen=True, slots=True)
class Notification:
    category: NotificationCategory
    severity: NotificationSeverity
    title: str
    summary: Mapping[str, JsonValue]
    recipient: RecipientRef
    source: SourceRef
    id: str = field(default_factory=lambda: new_id("notification"))
    state: NotificationState = NotificationState.UNREAD
    project_id: str | None = None
    workspace_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    approval_id: str | None = None
    verification_id: str | None = None
    node_id: str | None = None
    automation_id: str | None = None
    membership_id: str | None = None
    resource_ref: SourceRef | None = None
    actions: tuple[NotificationAction, ...] = ()
    aggregation_key: str | None = None
    occurrence_count: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    read_at: datetime | None = None
    acknowledged_at: datetime | None = None
    dismissed_at: datetime | None = None
    archived_at: datetime | None = None
    expires_at: datetime | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    delivery_metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.id, "notification")
        if not self.title.strip():
            raise ValueError("notification title must not be blank")
        if self.occurrence_count < 1:
            raise ValueError("occurrence_count must be at least one")
        if self.aggregation_key is not None and not self.aggregation_key.strip():
            raise ValueError("aggregation_key must not be blank when provided")
        _validate_optional_id(self.project_id, "project")
        _validate_optional_id(self.workspace_id, "workspace")
        _validate_optional_id(self.task_id, "task")
        _validate_optional_id(self.run_id, "run")
        _validate_optional_id(self.approval_id, "approval")
        _validate_optional_id(self.verification_id, "verification")
        _validate_optional_id(self.node_id, "node")
        _validate_optional_id(self.automation_id, "automation")
        _validate_optional_id(self.membership_id, "membership")
        for name in ("correlation_id", "causation_id"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} must not be blank when provided")
        for name in (
            "created_at",
            "updated_at",
            "read_at",
            "acknowledged_at",
            "dismissed_at",
            "archived_at",
            "expires_at",
        ):
            value = getattr(self, name)
            if value is not None and value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        safe_summary = redact_sensitive(dict(self.summary))
        safe_delivery = redact_sensitive(dict(self.delivery_metadata))
        if not isinstance(safe_summary, dict) or not isinstance(safe_delivery, dict):
            raise TypeError("notification metadata must serialize as JSON objects")
        object.__setattr__(self, "actions", tuple(self.actions))
        object.__setattr__(
            self,
            "summary",
            MappingProxyType(cast(dict[str, JsonValue], safe_summary)),
        )
        object.__setattr__(
            self,
            "delivery_metadata",
            MappingProxyType(cast(dict[str, JsonValue], safe_delivery)),
        )

    @property
    def unread(self) -> bool:
        return self.state is NotificationState.UNREAD


@dataclass(frozen=True, slots=True)
class NotificationPreference:
    recipient: RecipientRef
    enabled_categories: frozenset[NotificationCategory] = frozenset(NotificationCategory)
    minimum_severity: NotificationSeverity = NotificationSeverity.INFO
    project_ids: frozenset[str] = frozenset()
    muted: bool = False
    in_app_enabled: bool = True
    external_channels: frozenset[str] = frozenset()
    aggregate_duplicates: bool = True

    def __post_init__(self) -> None:
        for project_id in self.project_ids:
            validate_id(project_id, "project")
        if any(not value.strip() for value in self.external_channels):
            raise ValueError("external_channels must not contain blank values")


@dataclass(frozen=True, slots=True)
class NotificationQuery:
    recipient: RecipientRef
    category: NotificationCategory | None = None
    severity: NotificationSeverity | None = None
    project_id: str | None = None
    unread_only: bool = False
    include_archived: bool = False
    limit: int | None = None
    offset: int = 0

    def __post_init__(self) -> None:
        _validate_optional_id(self.project_id, "project")
        if self.limit is not None and self.limit < 1:
            raise ValueError("limit must be positive when provided")
        if self.offset < 0:
            raise ValueError("offset must not be negative")


@dataclass(frozen=True, slots=True)
class NotificationCandidate:
    category: NotificationCategory
    severity: NotificationSeverity
    title: str
    summary: Mapping[str, JsonValue]
    recipient: RecipientRef
    source: SourceRef
    project_id: str | None = None
    workspace_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    approval_id: str | None = None
    verification_id: str | None = None
    node_id: str | None = None
    automation_id: str | None = None
    membership_id: str | None = None
    resource_ref: SourceRef | None = None
    actions: tuple[NotificationAction, ...] = ()
    aggregation_key: str | None = None
    expires_at: datetime | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    delivery_metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("notification candidate title must not be blank")
        if self.aggregation_key is not None and not self.aggregation_key.strip():
            raise ValueError("aggregation_key must not be blank when provided")
        _validate_optional_id(self.project_id, "project")
        _validate_optional_id(self.workspace_id, "workspace")
        _validate_optional_id(self.task_id, "task")
        _validate_optional_id(self.run_id, "run")
        _validate_optional_id(self.approval_id, "approval")
        _validate_optional_id(self.verification_id, "verification")
        _validate_optional_id(self.node_id, "node")
        _validate_optional_id(self.automation_id, "automation")
        _validate_optional_id(self.membership_id, "membership")
        if self.expires_at is not None and self.expires_at.utcoffset() is None:
            raise ValueError("expires_at must be timezone-aware")
        safe_summary = redact_sensitive(dict(self.summary))
        safe_delivery = redact_sensitive(dict(self.delivery_metadata))
        if not isinstance(safe_summary, dict) or not isinstance(safe_delivery, dict):
            raise TypeError("notification candidate metadata must serialize as JSON objects")
        object.__setattr__(self, "actions", tuple(self.actions))
        object.__setattr__(
            self,
            "summary",
            MappingProxyType(cast(dict[str, JsonValue], safe_summary)),
        )
        object.__setattr__(
            self,
            "delivery_metadata",
            MappingProxyType(cast(dict[str, JsonValue], safe_delivery)),
        )


def _validate_optional_id(value: str | None, prefix: str) -> None:
    if value is not None:
        validate_id(value, prefix)
