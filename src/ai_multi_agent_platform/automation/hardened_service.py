"""Hardening layer for canonical Automation service semantics required by issue #18."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import NAMESPACE_URL, uuid4, uuid5

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

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
    require_aware,
    utc_now,
)
from .repository import AutomationRepository
from .service import (
    DEFAULT_MAX_WEBHOOK_EVENT_ID_LENGTH,
    DEFAULT_MAX_WEBHOOK_PAYLOAD_BYTES,
    DEFAULT_MAX_WEBHOOK_SOURCE_LENGTH,
    AutomationEventSink,
    TaskCreator,
)
from .service import AutomationService as _BaseAutomationService

WebhookPayloadValidator = Callable[[Automation, dict[str, JsonValue]], Awaitable[None]]
WebhookRateClock = Callable[[], float]

DEFAULT_MAX_WEBHOOK_DELIVERIES_PER_WINDOW = 120
DEFAULT_WEBHOOK_RATE_WINDOW_SECONDS = 60.0

_configuration_actor: ContextVar[str | None] = ContextVar(
    "automation_configuration_actor",
    default=None,
)
_creation_idempotency_key: ContextVar[str | None] = ContextVar(
    "automation_creation_idempotency_key",
    default=None,
)


@contextmanager
def automation_change_actor(principal_ref: str) -> Iterator[None]:
    """Bind the authenticated actor responsible for a configuration mutation."""

    if not principal_ref.strip():
        raise ValueError("principal_ref must be non-blank")
    token = _configuration_actor.set(principal_ref)
    try:
        yield
    finally:
        _configuration_actor.reset(token)


@contextmanager
def automation_creation_idempotency_key(idempotency_key: str) -> Iterator[None]:
    """Bind the canonical command idempotency key for automation creation."""

    if not idempotency_key.strip():
        raise ValueError("idempotency_key must be non-blank")
    token = _creation_idempotency_key.set(idempotency_key)
    try:
        yield
    finally:
        _creation_idempotency_key.reset(token)


class AutomationService(_BaseAutomationService):
    """Issue #18 service with audit, recovery and bounded webhook admission."""

    def __init__(
        self,
        *,
        repository: AutomationRepository,
        task_creator: TaskCreator,
        event_sink: AutomationEventSink | None = None,
        max_webhook_payload_bytes: int = DEFAULT_MAX_WEBHOOK_PAYLOAD_BYTES,
        max_webhook_event_id_length: int = DEFAULT_MAX_WEBHOOK_EVENT_ID_LENGTH,
        max_webhook_source_length: int = DEFAULT_MAX_WEBHOOK_SOURCE_LENGTH,
        webhook_payload_validator: WebhookPayloadValidator | None = None,
        max_webhook_deliveries_per_window: int = DEFAULT_MAX_WEBHOOK_DELIVERIES_PER_WINDOW,
        webhook_rate_window_seconds: float = DEFAULT_WEBHOOK_RATE_WINDOW_SECONDS,
        webhook_rate_clock: WebhookRateClock = monotonic,
    ) -> None:
        super().__init__(
            repository=repository,
            task_creator=task_creator,
            event_sink=event_sink,
            max_webhook_payload_bytes=max_webhook_payload_bytes,
            max_webhook_event_id_length=max_webhook_event_id_length,
            max_webhook_source_length=max_webhook_source_length,
        )
        if max_webhook_deliveries_per_window < 1:
            raise ValueError("max_webhook_deliveries_per_window must be positive")
        if webhook_rate_window_seconds <= 0:
            raise ValueError("webhook_rate_window_seconds must be positive")
        self._webhook_payload_validator = webhook_payload_validator
        self._max_webhook_deliveries_per_window = max_webhook_deliveries_per_window
        self._webhook_rate_window_seconds = webhook_rate_window_seconds
        self._webhook_rate_clock = webhook_rate_clock
        self._webhook_windows: dict[tuple[str, str], tuple[float, int]] = {}
        self._active_processing: set[str] = set()
        self._creation_locks: dict[str, asyncio.Lock] = {}

    async def create_automation(
        self,
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
        _validate_schedule_resolution(trigger)
        command_key = _creation_idempotency_key.get()
        if command_key is None:
            automation = await super().create_automation(
                name=name,
                description=description,
                identity=identity,
                trigger=trigger,
                task_template=task_template,
                project_id=project_id,
                workspace_id=workspace_id,
                deduplication_strategy=deduplication_strategy,
                retry_policy=retry_policy,
                overlap_policy=overlap_policy,
                now=now,
            )
            await self._emit_created(automation)
            return automation

        current = require_aware(now or utc_now(), "now")
        resolved_retry = retry_policy or RetryPolicy()
        seed = f"ai-multi-agent-platform:automation:create:{identity.principal_ref}:{command_key}"
        automation_id = f"automation_{uuid5(NAMESPACE_URL, seed)}"
        lock = self._creation_locks.setdefault(automation_id, asyncio.Lock())
        async with lock:
            try:
                existing = await self._repository.get_automation(automation_id)
            except ContractError as exc:
                if exc.code is not ErrorCode.NOT_FOUND:
                    raise
            else:
                if not _creation_matches(
                    existing,
                    name=name,
                    description=description,
                    identity=identity,
                    trigger=trigger,
                    task_template=task_template,
                    project_id=project_id,
                    workspace_id=workspace_id,
                    deduplication_strategy=deduplication_strategy,
                    retry_policy=resolved_retry,
                    overlap_policy=overlap_policy,
                ):
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "automation.create idempotency key was reused with a different payload",
                    )
                return existing

            automation = Automation(
                id=automation_id,
                name=name,
                description=description,
                identity=identity,
                trigger=trigger,
                task_template=task_template,
                project_id=project_id,
                workspace_id=workspace_id,
                deduplication_strategy=deduplication_strategy,
                retry_policy=resolved_retry,
                overlap_policy=overlap_policy,
                created_at=current,
                updated_at=current,
                next_evaluation_at=trigger.initial_next_fire_at(current),
            )
            persisted = await self._repository.save_automation(automation)
            await self._emit_created(persisted)
            return persisted

    async def _emit_created(self, automation: Automation) -> None:
        await self._emit_configuration(
            automation,
            action="created",
            changed_fields=(
                "identity",
                "trigger",
                "task_template",
                "project_id",
                "workspace_id",
                "deduplication_strategy",
                "retry_policy",
                "overlap_policy",
                "state",
            ),
        )

    async def set_state(
        self,
        automation_id: str,
        state: AutomationState,
        *,
        now: datetime | None = None,
    ) -> Automation:
        previous = await self._repository.get_automation(automation_id)
        current = require_aware(now or utc_now(), "now")
        updated = previous.with_state(state, current)
        if (
            state is AutomationState.ENABLED
            and previous.trigger.type is TriggerType.ONE_TIME
            and await self._one_time_completed(previous)
        ):
            updated = replace(updated, next_evaluation_at=None)
        updated = await self._repository.save_automation(updated)
        await self._emit_configuration(
            updated,
            action="state_changed",
            changed_fields=("state",),
            previous_state=previous.state,
        )
        return updated

    async def _one_time_completed(self, automation: Automation) -> bool:
        if automation.trigger.type is not TriggerType.ONE_TIME or automation.trigger.at is None:
            return False
        scheduled_for = automation.trigger.at.astimezone(UTC)
        return any(
            delivery.trigger_type is TriggerType.ONE_TIME
            and delivery.source == "schedule"
            and delivery.status is DeliveryStatus.SUCCEEDED
            and delivery.fired_at.astimezone(UTC) == scheduled_for
            for delivery in await self._repository.list_deliveries(automation.id)
        )

    async def update_automation(
        self,
        automation_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        trigger: TriggerDefinition | None = None,
        task_template: TaskTemplate | None = None,
        deduplication_strategy: str | None = None,
        retry_policy: RetryPolicy | None = None,
        overlap_policy: OverlapPolicy | None = None,
        now: datetime | None = None,
    ) -> Automation:
        if trigger is not None:
            _validate_schedule_resolution(trigger)
        updated = await super().update_automation(
            automation_id,
            name=name,
            description=description,
            trigger=trigger,
            task_template=task_template,
            deduplication_strategy=deduplication_strategy,
            retry_policy=retry_policy,
            overlap_policy=overlap_policy,
            now=now,
        )
        changed_fields = tuple(
            field_name
            for field_name, value in (
                ("name", name),
                ("description", description),
                ("trigger", trigger),
                ("task_template", task_template),
                ("deduplication_strategy", deduplication_strategy),
                ("retry_policy", retry_policy),
                ("overlap_policy", overlap_policy),
            )
            if value is not None
        )
        await self._emit_configuration(
            updated,
            action="updated",
            changed_fields=changed_fields,
        )
        return updated

    async def evaluate_due(self, *, now: datetime | None = None) -> tuple[TriggerDelivery, ...]:
        """Evaluate persisted schedules without overwriting newer revisions."""

        current = require_aware(now or utc_now(), "now").astimezone(UTC)
        fired: list[TriggerDelivery] = []
        for automation in await self._repository.list_automations():
            if automation.state is not AutomationState.ENABLED:
                continue
            if automation.trigger.type not in {TriggerType.ONE_TIME, TriggerType.RECURRING}:
                continue
            _validate_schedule_resolution(automation.trigger)
            occurrence = automation.next_evaluation_at
            if occurrence is None or occurrence > current:
                continue

            if (
                automation.trigger.type is TriggerType.RECURRING
                and automation.trigger.missed_schedule_policy is MissedSchedulePolicy.SKIP
                and occurrence < current
            ):
                latest = await self._repository.get_automation(automation.id)
                if latest.revision != automation.revision:
                    continue
                advanced = replace(
                    latest,
                    last_evaluated_at=current,
                    next_evaluation_at=_next_schedule_after(latest.trigger, occurrence, current),
                )
                await self._repository.save_automation(advanced)
                continue

            delivery = await self._deliver(
                automation,
                trigger_type=automation.trigger.type,
                source="schedule",
                dedupe_key=f"schedule:{automation.revision}:{occurrence.isoformat()}",
                fired_at=occurrence,
                payload={"scheduled_for": occurrence.isoformat()},
            )
            fired.append(delivery)

            latest = await self._repository.get_automation(automation.id)
            if latest.revision != automation.revision:
                continue
            advanced = replace(
                latest,
                last_evaluated_at=current,
                next_evaluation_at=_next_schedule_after(latest.trigger, occurrence, current),
            )
            await self._repository.save_automation(advanced)
        return tuple(fired)

    async def deliver_webhook(
        self,
        automation_id: str,
        *,
        event_id: str,
        payload: dict[str, JsonValue],
        source: str,
        verified: bool,
        fired_at: datetime | None = None,
    ) -> TriggerDelivery:
        """Admit authenticated webhooks without letting rejected attempts poison dedupe."""

        self.validate_webhook_input(event_id=event_id, payload=payload, source=source)
        automation = await self._repository.get_automation(automation_id)
        if automation.trigger.type is not TriggerType.WEBHOOK:
            raise ContractError(ErrorCode.INVALID_REQUEST, "automation is not webhook-triggered")

        occurred = require_aware(fired_at or utc_now(), "fired_at")
        accepted_dedupe_key = f"webhook:{source}:{event_id}"

        if automation.trigger.webhook_source != source:
            await self._reject_webhook(
                automation,
                source=source,
                dedupe_key=accepted_dedupe_key,
                fired_at=occurred,
                payload=payload,
                error_code="webhook_source_mismatch",
                error_message="webhook source does not match trigger",
            )
            raise ContractError(ErrorCode.FORBIDDEN, "webhook source does not match trigger")

        if not verified:
            await self._reject_webhook(
                automation,
                source=source,
                dedupe_key=accepted_dedupe_key,
                fired_at=occurred,
                payload=payload,
                error_code="webhook_verification_failed",
                error_message="webhook authenticity verification failed",
            )
            raise ContractError(ErrorCode.FORBIDDEN, "webhook authenticity verification failed")

        existing = await self._repository.find_delivery_by_dedupe(
            automation.id, accepted_dedupe_key
        )
        if existing is not None:
            await self._emit(automation, existing, "deduplicated")
            return existing

        if not self._consume_webhook_rate(automation.id, source):
            await self._reject_webhook(
                automation,
                source=source,
                dedupe_key=accepted_dedupe_key,
                fired_at=occurred,
                payload=payload,
                error_code="webhook_rate_limited",
                error_message="webhook delivery rate limit exceeded",
            )
            raise ContractError(
                ErrorCode.RATE_LIMITED,
                "webhook delivery rate limit exceeded",
                retryable=True,
                details={
                    "max_deliveries": self._max_webhook_deliveries_per_window,
                    "window_seconds": self._webhook_rate_window_seconds,
                },
            )

        if self._webhook_payload_validator is not None:
            try:
                await self._webhook_payload_validator(automation, payload)
            except ContractError as exc:
                await self._reject_webhook(
                    automation,
                    source=source,
                    dedupe_key=accepted_dedupe_key,
                    fired_at=occurred,
                    payload=payload,
                    error_code=exc.code.value,
                    error_message=exc.message,
                )
                raise

        return await self._deliver(
            automation,
            trigger_type=TriggerType.WEBHOOK,
            source=source,
            dedupe_key=accepted_dedupe_key,
            fired_at=occurred,
            payload=payload,
        )

    async def _reject_webhook(
        self,
        automation: Automation,
        *,
        source: str,
        dedupe_key: str,
        fired_at: datetime,
        payload: dict[str, JsonValue],
        error_code: str,
        error_message: str,
    ) -> TriggerDelivery:
        rejection_key = f"rejected:{error_code}:{dedupe_key}:{uuid4()}"
        rejected = replace(
            TriggerDelivery.create(
                automation_id=automation.id,
                trigger_type=TriggerType.WEBHOOK,
                source=source,
                dedupe_key=rejection_key,
                fired_at=fired_at,
                payload=payload,
            ),
            status=DeliveryStatus.REJECTED,
            error_code=error_code,
            error_message=error_message,
        )
        persisted = await self._repository.save_delivery(rejected)
        await self._emit(automation, persisted, "rejected")
        return persisted

    def _consume_webhook_rate(self, automation_id: str, source: str) -> bool:
        now = self._webhook_rate_clock()
        key = (automation_id, source)
        window_start, count = self._webhook_windows.get(key, (now, 0))
        if now - window_start >= self._webhook_rate_window_seconds:
            window_start, count = now, 0
        if count >= self._max_webhook_deliveries_per_window:
            return False
        self._webhook_windows[key] = (window_start, count + 1)
        return True

    async def _deliver(
        self,
        automation: Automation,
        *,
        trigger_type: TriggerType,
        source: str,
        dedupe_key: str,
        fired_at: datetime,
        payload: dict[str, JsonValue],
    ) -> TriggerDelivery:
        """Recover durable nonterminal deliveries after process restart."""

        if automation.state is not AutomationState.ENABLED:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"automation is not enabled: {automation.state.value}",
            )
        existing = await self._repository.find_delivery_by_dedupe(automation.id, dedupe_key)
        if existing is not None:
            if (
                existing.status in {DeliveryStatus.PENDING, DeliveryStatus.PROCESSING}
                and existing.id not in self._active_processing
            ):
                resumed = existing
                if existing.status is DeliveryStatus.PROCESSING:
                    resumed = replace(
                        existing,
                        status=DeliveryStatus.PENDING,
                        attempt=max(0, existing.attempt - 1),
                    )
                await self._emit(automation, existing, "recovered")
                return await self._process(automation, resumed)
            await self._emit(automation, existing, "deduplicated")
            return existing
        return await super()._deliver(
            automation,
            trigger_type=trigger_type,
            source=source,
            dedupe_key=dedupe_key,
            fired_at=fired_at,
            payload=payload,
        )

    async def retry_delivery(self, delivery_id: str) -> TriggerDelivery:
        delivery = await self._repository.get_delivery(delivery_id)
        automation = await self._repository.get_automation(delivery.automation_id)
        if delivery.id in self._active_processing:
            return delivery
        if delivery.status in {DeliveryStatus.PENDING, DeliveryStatus.PROCESSING}:
            resumed = delivery
            if delivery.status is DeliveryStatus.PROCESSING:
                resumed = replace(
                    delivery,
                    status=DeliveryStatus.PENDING,
                    attempt=max(0, delivery.attempt - 1),
                )
            return await self._process(automation, resumed)
        return await super().retry_delivery(delivery_id)

    async def _process(self, automation: Automation, delivery: TriggerDelivery) -> TriggerDelivery:
        self._active_processing.add(delivery.id)
        try:
            return await super()._process(automation, delivery)
        finally:
            self._active_processing.discard(delivery.id)

    async def _emit_configuration(
        self,
        automation: Automation,
        *,
        action: str,
        changed_fields: tuple[str, ...],
        previous_state: AutomationState | None = None,
    ) -> None:
        if self._event_sink is None:
            return
        changed_by = _configuration_actor.get() or automation.identity.principal_ref
        await self._event_sink(
            {
                "type": "automation.configuration",
                "automation_id": automation.id,
                "automation_revision": automation.revision,
                "action": action,
                "changed_fields": list(changed_fields),
                "state": automation.state.value,
                "previous_state": None if previous_state is None else previous_state.value,
                "principal_ref": automation.identity.principal_ref,
                "automation_principal_ref": automation.identity.principal_ref,
                "changed_by_principal_ref": changed_by,
                "updated_at": automation.updated_at.isoformat(),
            }
        )

    async def _emit(self, automation: Automation, delivery: TriggerDelivery, outcome: str) -> None:
        if self._event_sink is None:
            return
        is_schedule = delivery.trigger_type in {TriggerType.ONE_TIME, TriggerType.RECURRING}
        await self._event_sink(
            {
                "type": "automation.delivery",
                "automation_id": automation.id,
                "automation_revision": automation.revision,
                "trigger_delivery_id": delivery.id,
                "generated_task_id": delivery.generated_task_id,
                "trigger_type": delivery.trigger_type.value,
                "source": delivery.source,
                "fired_at": delivery.fired_at.isoformat(),
                "received_at": delivery.received_at.isoformat(),
                "processing_duration_ms": delivery.processing_duration_ms,
                "dedupe_key": delivery.dedupe_key,
                "dedupe_outcome": outcome,
                "outcome": outcome,
                "attempt": delivery.attempt,
                "error_code": delivery.error_code,
                "schedule_timezone": automation.trigger.timezone if is_schedule else None,
                "schedule_at": (
                    automation.trigger.at.isoformat()
                    if is_schedule and automation.trigger.at is not None
                    else None
                ),
                "schedule_interval_seconds": (
                    automation.trigger.interval_seconds if is_schedule else None
                ),
                "schedule_missed_policy": (
                    automation.trigger.missed_schedule_policy.value if is_schedule else None
                ),
                "schedule_next_evaluation_at": (
                    automation.next_evaluation_at.isoformat()
                    if is_schedule and automation.next_evaluation_at is not None
                    else None
                ),
            }
        )


def _validate_schedule_resolution(trigger: TriggerDefinition) -> None:
    if trigger.type is not TriggerType.RECURRING:
        return
    assert trigger.interval_seconds is not None
    try:
        step = timedelta(seconds=trigger.interval_seconds)
    except OverflowError as exc:
        raise ValueError("recurring interval exceeds datetime range") from exc
    if step <= timedelta(0):
        raise ValueError("recurring interval must be at least datetime resolution")


def _next_schedule_after(
    trigger: TriggerDefinition,
    occurrence: datetime,
    now: datetime,
) -> datetime | None:
    if trigger.type is TriggerType.ONE_TIME:
        return None
    if trigger.type is not TriggerType.RECURRING:
        return None
    _validate_schedule_resolution(trigger)
    assert trigger.interval_seconds is not None
    step = timedelta(seconds=trigger.interval_seconds)
    candidate = occurrence.astimezone(UTC) + step
    if candidate <= now:
        skips = (now - candidate) // step + 1
        candidate += step * skips
    return candidate


def _creation_matches(
    automation: Automation,
    *,
    name: str,
    description: str,
    identity: IdentityContext,
    trigger: TriggerDefinition,
    task_template: TaskTemplate,
    project_id: str | None,
    workspace_id: str | None,
    deduplication_strategy: str,
    retry_policy: RetryPolicy,
    overlap_policy: OverlapPolicy,
) -> bool:
    return (
        automation.name == name
        and automation.description == description
        and automation.identity == identity
        and automation.trigger == trigger
        and automation.task_template == task_template
        and automation.project_id == project_id
        and automation.workspace_id == workspace_id
        and automation.deduplication_strategy == deduplication_strategy
        and automation.retry_policy == retry_policy
        and automation.overlap_policy is overlap_policy
    )
