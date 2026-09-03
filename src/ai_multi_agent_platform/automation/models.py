"""Canonical Automation, Trigger and TriggerDelivery value types for issue #18."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import new_id, validate_id


def utc_now() -> datetime:
    return datetime.now(UTC)


def require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


class AutomationState(StrEnum):
    ENABLED = "enabled"
    PAUSED = "paused"
    DISABLED = "disabled"
    INVALID = "invalid"


class TriggerType(StrEnum):
    ONE_TIME = "one_time"
    RECURRING = "recurring"
    WEBHOOK = "webhook"
    PLATFORM_EVENT = "platform_event"
    MANUAL = "manual"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    DEDUPLICATED = "deduplicated"
    FAILED = "failed"
    REJECTED = "rejected"


class OverlapPolicy(StrEnum):
    ALLOW = "allow"
    SKIP_WHILE_PROCESSING = "skip_while_processing"


class MissedSchedulePolicy(StrEnum):
    COALESCE = "coalesce"
    SKIP = "skip"


@dataclass(frozen=True, slots=True)
class IdentityContext:
    principal_ref: str
    owner_type: str
    owner_id: str

    def __post_init__(self) -> None:
        if not self.principal_ref.strip():
            raise ValueError("principal_ref must not be blank")
        if self.owner_type not in {"user", "organization", "team", "service"}:
            raise ValueError("owner_type must be user, organization, team or service")
        if not self.owner_id.strip():
            raise ValueError("owner_id must not be blank")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_backoff_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.base_backoff_seconds < 0:
            raise ValueError("base_backoff_seconds must not be negative")


@dataclass(frozen=True, slots=True)
class TriggerDefinition:
    type: TriggerType
    timezone: str = "UTC"
    at: datetime | None = None
    interval_seconds: float | None = None
    event_type: str | None = None
    filters: dict[str, JsonValue] = field(default_factory=dict)
    webhook_source: str | None = None
    verification_ref: str | None = None
    missed_schedule_policy: MissedSchedulePolicy = MissedSchedulePolicy.COALESCE

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {self.timezone}") from exc

        if self.type is TriggerType.ONE_TIME:
            if self.at is None:
                raise ValueError("one-time trigger requires at")
            require_aware(self.at, "trigger.at")
        elif self.type is TriggerType.RECURRING:
            if self.at is None:
                raise ValueError("recurring trigger requires at")
            require_aware(self.at, "trigger.at")
            if self.interval_seconds is None or self.interval_seconds <= 0:
                raise ValueError("recurring trigger requires positive interval_seconds")
            try:
                step = timedelta(seconds=self.interval_seconds)
            except OverflowError as exc:
                raise ValueError("recurring interval exceeds datetime range") from exc
            if step <= timedelta(0):
                raise ValueError("recurring interval must be at least datetime resolution")
        elif self.type is TriggerType.WEBHOOK:
            if self.webhook_source is None or not self.webhook_source.strip():
                raise ValueError("webhook trigger requires webhook_source")
        elif self.type is TriggerType.PLATFORM_EVENT:
            if self.event_type is None or not self.event_type.strip():
                raise ValueError("platform-event trigger requires event_type")

        if self.type not in {TriggerType.ONE_TIME, TriggerType.RECURRING}:
            if self.at is not None or self.interval_seconds is not None:
                raise ValueError("non-schedule trigger cannot define schedule fields")

    def initial_next_fire_at(self, now: datetime) -> datetime | None:
        require_aware(now, "now")
        if self.type not in {TriggerType.ONE_TIME, TriggerType.RECURRING}:
            return None
        assert self.at is not None
        return self.at.astimezone(UTC)

    def next_after(self, occurrence: datetime, now: datetime) -> datetime | None:
        require_aware(occurrence, "occurrence")
        require_aware(now, "now")
        if self.type is TriggerType.ONE_TIME:
            return None
        if self.type is not TriggerType.RECURRING:
            return None
        assert self.interval_seconds is not None
        step = timedelta(seconds=self.interval_seconds)
        if step <= timedelta(0):
            raise ValueError("recurring interval must be at least datetime resolution")
        candidate = occurrence.astimezone(UTC) + step
        if candidate <= now:
            skips = (now - candidate) // step + 1
            candidate += step * skips
        return candidate


@dataclass(frozen=True, slots=True)
class TaskTemplate:
    title: str
    objective: str
    project_id: str | None = None
    workspace_id: str | None = None
    payload: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("task template title must not be blank")
        if not self.objective.strip():
            raise ValueError("task template objective must not be blank")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if self.workspace_id is not None:
            validate_id(self.workspace_id, "workspace")

    def render(
        self,
        *,
        automation_id: str,
        delivery_id: str,
        identity: IdentityContext,
    ) -> dict[str, JsonValue]:
        labels: list[JsonValue] = []
        raw_labels = self.payload.get("labels")
        if isinstance(raw_labels, list):
            labels.extend(raw_labels)
        labels.extend([f"automation:{automation_id}", f"delivery:{delivery_id}"])
        rendered: dict[str, JsonValue] = dict(self.payload)
        rendered.update(
            {
                "title": self.title,
                "objective": self.objective,
                "owner_type": identity.owner_type,
                "owner_id": identity.owner_id,
                "labels": labels,
            }
        )
        if self.project_id is not None:
            rendered["project_id"] = self.project_id
        if self.workspace_id is not None:
            rendered["workspace_id"] = self.workspace_id
        return rendered


@dataclass(frozen=True, slots=True)
class Automation:
    id: str
    name: str
    description: str
    identity: IdentityContext
    trigger: TriggerDefinition
    task_template: TaskTemplate
    project_id: str | None = None
    workspace_id: str | None = None
    state: AutomationState = AutomationState.ENABLED
    deduplication_strategy: str = "delivery_key"
    retry_policy: RetryPolicy = RetryPolicy()
    overlap_policy: OverlapPolicy = OverlapPolicy.SKIP_WHILE_PROCESSING
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    revision: int = 1
    last_evaluated_at: datetime | None = None
    next_evaluation_at: datetime | None = None

    def __post_init__(self) -> None:
        validate_id(self.id, "automation")
        if not self.name.strip():
            raise ValueError("automation name must not be blank")
        if not self.deduplication_strategy.strip():
            raise ValueError("deduplication_strategy must not be blank")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if self.workspace_id is not None:
            validate_id(self.workspace_id, "workspace")
        if self.revision < 1:
            raise ValueError("automation revision must be at least 1")
        require_aware(self.created_at, "created_at")
        require_aware(self.updated_at, "updated_at")
        if self.last_evaluated_at is not None:
            require_aware(self.last_evaluated_at, "last_evaluated_at")
        if self.next_evaluation_at is not None:
            require_aware(self.next_evaluation_at, "next_evaluation_at")

    @classmethod
    def create(
        cls,
        *,
        name: str,
        description: str,
        identity: IdentityContext,
        trigger: TriggerDefinition,
        task_template: TaskTemplate,
        project_id: str | None = None,
        workspace_id: str | None = None,
        deduplication_strategy: str = "delivery_key",
        retry_policy: RetryPolicy | None = None,
        overlap_policy: OverlapPolicy = OverlapPolicy.SKIP_WHILE_PROCESSING,
        now: datetime | None = None,
    ) -> Automation:
        current = require_aware(now or utc_now(), "now")
        return cls(
            id=new_id("automation"),
            name=name,
            description=description,
            identity=identity,
            trigger=trigger,
            task_template=task_template,
            project_id=project_id,
            workspace_id=workspace_id,
            deduplication_strategy=deduplication_strategy,
            retry_policy=retry_policy or RetryPolicy(),
            overlap_policy=overlap_policy,
            created_at=current,
            updated_at=current,
            next_evaluation_at=trigger.initial_next_fire_at(current),
        )

    def with_state(self, state: AutomationState, now: datetime) -> Automation:
        current = require_aware(now, "now")
        next_at = self.next_evaluation_at
        if state is AutomationState.ENABLED and self.state is not AutomationState.ENABLED:
            completed_one_time = (
                self.trigger.type is TriggerType.ONE_TIME
                and self.last_evaluated_at is not None
                and self.next_evaluation_at is None
            )
            if not completed_one_time:
                next_at = self.trigger.initial_next_fire_at(current)
        return replace(
            self,
            state=state,
            updated_at=current,
            revision=self.revision + 1,
            next_evaluation_at=next_at,
        )


@dataclass(frozen=True, slots=True)
class TriggerDelivery:
    id: str
    automation_id: str
    trigger_type: TriggerType
    source: str
    dedupe_key: str
    fired_at: datetime
    payload: dict[str, JsonValue] = field(default_factory=dict)
    status: DeliveryStatus = DeliveryStatus.PENDING
    received_at: datetime = field(default_factory=utc_now)
    attempt: int = 0
    generated_task_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    processing_duration_ms: float | None = None

    def __post_init__(self) -> None:
        validate_id(self.id, "trigger_delivery")
        validate_id(self.automation_id, "automation")
        if not self.source.strip():
            raise ValueError("delivery source must not be blank")
        if not self.dedupe_key.strip():
            raise ValueError("delivery dedupe_key must not be blank")
        require_aware(self.fired_at, "fired_at")
        require_aware(self.received_at, "received_at")
        if self.attempt < 0:
            raise ValueError("delivery attempt must not be negative")
        if self.generated_task_id is not None:
            validate_id(self.generated_task_id, "task")

    @classmethod
    def create(
        cls,
        *,
        automation_id: str,
        trigger_type: TriggerType,
        source: str,
        dedupe_key: str,
        fired_at: datetime,
        payload: dict[str, JsonValue] | None = None,
    ) -> TriggerDelivery:
        return cls(
            id=new_id("trigger_delivery"),
            automation_id=automation_id,
            trigger_type=trigger_type,
            source=source,
            dedupe_key=dedupe_key,
            fired_at=require_aware(fired_at, "fired_at"),
            payload=dict(payload or {}),
        )
