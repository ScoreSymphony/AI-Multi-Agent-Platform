"""Canonical Automation service: triggers only create work through a Task creator port."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from time import perf_counter

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

TaskCreator = Callable[[Automation, TriggerDelivery, dict[str, JsonValue], str], Awaitable[str]]
AutomationEventSink = Callable[[dict[str, JsonValue]], Awaitable[None]]

DEFAULT_MAX_WEBHOOK_PAYLOAD_BYTES = 256 * 1024
DEFAULT_MAX_WEBHOOK_EVENT_ID_LENGTH = 512
DEFAULT_MAX_WEBHOOK_SOURCE_LENGTH = 128


class AutomationService:
    """Owns automation state and delivery processing, never execution itself."""

    def __init__(
        self,
        *,
        repository: AutomationRepository,
        task_creator: TaskCreator,
        event_sink: AutomationEventSink | None = None,
        max_webhook_payload_bytes: int = DEFAULT_MAX_WEBHOOK_PAYLOAD_BYTES,
        max_webhook_event_id_length: int = DEFAULT_MAX_WEBHOOK_EVENT_ID_LENGTH,
        max_webhook_source_length: int = DEFAULT_MAX_WEBHOOK_SOURCE_LENGTH,
    ) -> None:
        if max_webhook_payload_bytes < 1:
            raise ValueError("max_webhook_payload_bytes must be positive")
        if max_webhook_event_id_length < 1:
            raise ValueError("max_webhook_event_id_length must be positive")
        if max_webhook_source_length < 1:
            raise ValueError("max_webhook_source_length must be positive")
        self._repository = repository
        self._task_creator = task_creator
        self._event_sink = event_sink
        self._max_webhook_payload_bytes = max_webhook_payload_bytes
        self._max_webhook_event_id_length = max_webhook_event_id_length
        self._max_webhook_source_length = max_webhook_source_length
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def repository(self) -> AutomationRepository:
        return self._repository

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
        automation = Automation.create(
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
        return await self._repository.save_automation(automation)

    async def get_automation(self, automation_id: str) -> Automation:
        return await self._repository.get_automation(automation_id)

    async def list_automations(self) -> tuple[Automation, ...]:
        return await self._repository.list_automations()

    async def list_deliveries(
        self, automation_id: str | None = None
    ) -> tuple[TriggerDelivery, ...]:
        return await self._repository.list_deliveries(automation_id)

    async def get_delivery(self, delivery_id: str) -> TriggerDelivery:
        return await self._repository.get_delivery(delivery_id)

    async def set_state(
        self,
        automation_id: str,
        state: AutomationState,
        *,
        now: datetime | None = None,
    ) -> Automation:
        automation = await self._repository.get_automation(automation_id)
        current = require_aware(now or utc_now(), "now")
        updated = automation.with_state(state, current)
        return await self._repository.save_automation(updated)

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
        automation = await self._repository.get_automation(automation_id)
        current = require_aware(now or utc_now(), "now")
        next_trigger = trigger or automation.trigger
        updated = replace(
            automation,
            name=automation.name if name is None else name,
            description=automation.description if description is None else description,
            trigger=next_trigger,
            task_template=automation.task_template if task_template is None else task_template,
            deduplication_strategy=(
                automation.deduplication_strategy
                if deduplication_strategy is None
                else deduplication_strategy
            ),
            retry_policy=automation.retry_policy if retry_policy is None else retry_policy,
            overlap_policy=automation.overlap_policy if overlap_policy is None else overlap_policy,
            updated_at=current,
            revision=automation.revision + 1,
            next_evaluation_at=(
                next_trigger.initial_next_fire_at(current)
                if trigger is not None
                else automation.next_evaluation_at
            ),
        )
        return await self._repository.save_automation(updated)

    async def evaluate_due(self, *, now: datetime | None = None) -> tuple[TriggerDelivery, ...]:
        current = require_aware(now or utc_now(), "now").astimezone(UTC)
        fired: list[TriggerDelivery] = []
        for automation in await self._repository.list_automations():
            if automation.state is not AutomationState.ENABLED:
                continue
            if automation.trigger.type not in {TriggerType.ONE_TIME, TriggerType.RECURRING}:
                continue
            occurrence = automation.next_evaluation_at
            if occurrence is None or occurrence > current:
                continue
            if (
                automation.trigger.type is TriggerType.RECURRING
                and automation.trigger.missed_schedule_policy is MissedSchedulePolicy.SKIP
                and occurrence < current
            ):
                advanced = replace(
                    automation,
                    last_evaluated_at=current,
                    next_evaluation_at=automation.trigger.next_after(occurrence, current),
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
            advanced = replace(
                latest,
                last_evaluated_at=current,
                next_evaluation_at=latest.trigger.next_after(occurrence, current),
            )
            await self._repository.save_automation(advanced)
        return tuple(fired)

    def validate_webhook_input(
        self,
        *,
        event_id: str,
        payload: dict[str, JsonValue],
        source: str,
    ) -> None:
        if not event_id.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "webhook event_id must not be blank")
        if not source.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "webhook source must not be blank")
        if len(event_id) > self._max_webhook_event_id_length:
            raise ContractError(
                ErrorCode.INPUT_TOO_LARGE,
                "webhook event_id exceeds configured length limit",
                details={"max_length": self._max_webhook_event_id_length},
            )
        if len(source) > self._max_webhook_source_length:
            raise ContractError(
                ErrorCode.INPUT_TOO_LARGE,
                "webhook source exceeds configured length limit",
                details={"max_length": self._max_webhook_source_length},
            )
        try:
            encoded = json.dumps(
                payload,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "webhook payload must be canonical JSON",
            ) from exc
        if len(encoded) > self._max_webhook_payload_bytes:
            raise ContractError(
                ErrorCode.INPUT_TOO_LARGE,
                "webhook payload exceeds configured byte limit",
                details={
                    "max_bytes": self._max_webhook_payload_bytes,
                    "actual_bytes": len(encoded),
                },
            )

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
        self.validate_webhook_input(event_id=event_id, payload=payload, source=source)
        automation = await self._repository.get_automation(automation_id)
        if automation.trigger.type is not TriggerType.WEBHOOK:
            raise ContractError(ErrorCode.INVALID_REQUEST, "automation is not webhook-triggered")
        occurred = require_aware(fired_at or utc_now(), "fired_at")
        dedupe_key = f"webhook:{source}:{event_id}"
        if not verified:
            existing = await self._repository.find_delivery_by_dedupe(automation.id, dedupe_key)
            if existing is not None:
                await self._emit(automation, existing, "deduplicated")
                return existing
            rejected = replace(
                TriggerDelivery.create(
                    automation_id=automation.id,
                    trigger_type=TriggerType.WEBHOOK,
                    source=source,
                    dedupe_key=dedupe_key,
                    fired_at=occurred,
                    payload=payload,
                ),
                status=DeliveryStatus.REJECTED,
                error_code="webhook_verification_failed",
                error_message="webhook authenticity verification failed",
            )
            await self._repository.save_delivery(rejected)
            await self._emit(automation, rejected, "rejected")
            raise ContractError(ErrorCode.FORBIDDEN, "webhook authenticity verification failed")
        if automation.trigger.webhook_source != source:
            raise ContractError(ErrorCode.FORBIDDEN, "webhook source does not match trigger")
        return await self._deliver(
            automation,
            trigger_type=TriggerType.WEBHOOK,
            source=source,
            dedupe_key=dedupe_key,
            fired_at=occurred,
            payload=payload,
        )

    async def deliver_platform_event(
        self,
        *,
        event_id: str,
        event_type: str,
        payload: dict[str, JsonValue],
        fired_at: datetime | None = None,
    ) -> tuple[TriggerDelivery, ...]:
        occurred = require_aware(fired_at or utc_now(), "fired_at")
        deliveries: list[TriggerDelivery] = []
        for automation in await self._repository.list_automations():
            trigger = automation.trigger
            if automation.state is not AutomationState.ENABLED:
                continue
            if trigger.type is not TriggerType.PLATFORM_EVENT or trigger.event_type != event_type:
                continue
            if not _matches(trigger.filters, payload):
                continue
            deliveries.append(
                await self._deliver(
                    automation,
                    trigger_type=TriggerType.PLATFORM_EVENT,
                    source="platform-event",
                    dedupe_key=f"event:{event_type}:{event_id}",
                    fired_at=occurred,
                    payload=payload,
                )
            )
        return tuple(deliveries)

    async def test_trigger(
        self,
        automation_id: str,
        *,
        occurrence_id: str,
        payload: dict[str, JsonValue] | None = None,
        fired_at: datetime | None = None,
    ) -> TriggerDelivery:
        automation = await self._repository.get_automation(automation_id)
        return await self._deliver(
            automation,
            trigger_type=TriggerType.MANUAL,
            source="manual-test",
            dedupe_key=f"manual:{occurrence_id}",
            fired_at=require_aware(fired_at or utc_now(), "fired_at"),
            payload=dict(payload or {}),
        )

    async def retry_delivery(self, delivery_id: str) -> TriggerDelivery:
        delivery = await self._repository.get_delivery(delivery_id)
        if delivery.status is not DeliveryStatus.FAILED:
            return delivery
        automation = await self._repository.get_automation(delivery.automation_id)
        if delivery.attempt >= automation.retry_policy.max_attempts:
            await self._emit(automation, delivery, "retry-exhausted")
            return delivery
        return await self._process(automation, delivery)

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
        if automation.state is not AutomationState.ENABLED:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"automation is not enabled: {automation.state.value}",
            )
        existing = await self._repository.find_delivery_by_dedupe(automation.id, dedupe_key)
        if existing is not None:
            await self._emit(automation, existing, "deduplicated")
            return existing

        if automation.overlap_policy is OverlapPolicy.ALLOW:
            return await self._persist_and_process(
                automation,
                trigger_type=trigger_type,
                source=source,
                dedupe_key=dedupe_key,
                fired_at=fired_at,
                payload=payload,
            )

        lock = self._locks.setdefault(automation.id, asyncio.Lock())
        if lock.locked():
            skipped = replace(
                TriggerDelivery.create(
                    automation_id=automation.id,
                    trigger_type=trigger_type,
                    source=source,
                    dedupe_key=dedupe_key,
                    fired_at=fired_at,
                    payload=payload,
                ),
                status=DeliveryStatus.REJECTED,
                error_code="overlap_skipped",
                error_message="automation delivery skipped while another delivery is processing",
            )
            await self._repository.save_delivery(skipped)
            await self._emit(automation, skipped, "overlap-skipped")
            return skipped

        async with lock:
            return await self._persist_and_process(
                automation,
                trigger_type=trigger_type,
                source=source,
                dedupe_key=dedupe_key,
                fired_at=fired_at,
                payload=payload,
            )

    async def _persist_and_process(
        self,
        automation: Automation,
        *,
        trigger_type: TriggerType,
        source: str,
        dedupe_key: str,
        fired_at: datetime,
        payload: dict[str, JsonValue],
    ) -> TriggerDelivery:
        raced = await self._repository.find_delivery_by_dedupe(automation.id, dedupe_key)
        if raced is not None:
            await self._emit(automation, raced, "deduplicated")
            return raced
        pending = TriggerDelivery.create(
            automation_id=automation.id,
            trigger_type=trigger_type,
            source=source,
            dedupe_key=dedupe_key,
            fired_at=fired_at,
            payload=payload,
        )
        persisted = await self._repository.save_delivery(pending)
        if persisted.id != pending.id:
            await self._emit(automation, persisted, "deduplicated")
            return persisted
        return await self._process(automation, pending)

    async def _process(self, automation: Automation, delivery: TriggerDelivery) -> TriggerDelivery:
        started = perf_counter()
        processing = replace(
            delivery,
            status=DeliveryStatus.PROCESSING,
            attempt=delivery.attempt + 1,
            error_code=None,
            error_message=None,
        )
        await self._repository.save_delivery(processing)
        rendered = automation.task_template.render(
            automation_id=automation.id,
            delivery_id=processing.id,
            identity=automation.identity,
        )
        idempotency_key = f"automation:{automation.id}:{processing.dedupe_key}"
        try:
            task_id = await self._task_creator(automation, processing, rendered, idempotency_key)
        except ContractError as exc:
            failed = replace(
                processing,
                status=DeliveryStatus.FAILED,
                error_code=exc.code.value,
                error_message=exc.message,
                processing_duration_ms=(perf_counter() - started) * 1000,
            )
            await self._repository.save_delivery(failed)
            await self._emit(automation, failed, "failed")
            return failed
        except Exception as exc:
            failed = replace(
                processing,
                status=DeliveryStatus.FAILED,
                error_code="automation_task_creation_failed",
                error_message=str(exc),
                processing_duration_ms=(perf_counter() - started) * 1000,
            )
            await self._repository.save_delivery(failed)
            await self._emit(automation, failed, "failed")
            return failed

        succeeded = replace(
            processing,
            status=DeliveryStatus.SUCCEEDED,
            generated_task_id=task_id,
            processing_duration_ms=(perf_counter() - started) * 1000,
        )
        await self._repository.save_delivery(succeeded)
        await self._emit(automation, succeeded, "succeeded")
        return succeeded

    async def _emit(self, automation: Automation, delivery: TriggerDelivery, outcome: str) -> None:
        if self._event_sink is None:
            return
        await self._event_sink(
            {
                "type": "automation.delivery",
                "automation_id": automation.id,
                "automation_revision": automation.revision,
                "trigger_delivery_id": delivery.id,
                "generated_task_id": delivery.generated_task_id,
                "trigger_type": delivery.trigger_type.value,
                "fired_at": delivery.fired_at.isoformat(),
                "processing_duration_ms": delivery.processing_duration_ms,
                "dedupe_key": delivery.dedupe_key,
                "outcome": outcome,
                "error_code": delivery.error_code,
            }
        )


class ReferenceScheduler:
    """Replaceable deterministic in-process scheduler with no broker dependency."""

    def __init__(self, service: AutomationService) -> None:
        self._service = service

    async def tick(self, *, now: datetime | None = None) -> tuple[TriggerDelivery, ...]:
        return await self._service.evaluate_due(now=now)

    async def next_wakeup(self) -> datetime | None:
        candidates = [
            automation.next_evaluation_at
            for automation in await self._service.list_automations()
            if automation.state is AutomationState.ENABLED
            and automation.next_evaluation_at is not None
        ]
        return None if not candidates else min(candidates)


def _matches(filters: dict[str, JsonValue], payload: dict[str, JsonValue]) -> bool:
    return all(payload.get(key) == expected for key, expected in filters.items())
