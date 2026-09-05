"""Completed source-domain integrations for canonical Notifications (#75 hardening)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from queue import Empty, SimpleQueue
from typing import Any, cast

from ai_multi_agent_platform.accounting.models import BudgetThresholdEvent
from ai_multi_agent_platform.accounting.service import AccountingService
from ai_multi_agent_platform.automation import AutomationEventSink
from ai_multi_agent_platform.connectors.models import Connection
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.notifications import (
    Notification,
    NotificationAction,
    NotificationCandidate,
    NotificationCategory,
    NotificationQuery,
    NotificationSeverity,
    RecipientRef,
    RecipientType,
    SourceRef,
    approval_required_candidate,
    approval_resolved_candidate,
    budget_threshold_candidate,
    canonical_attention_candidate,
)
from ai_multi_agent_platform.security.approvals import ApprovalRecord
from ai_multi_agent_platform.security.enforcement import AuthorizationGate

from .notifications_runtime_composition import ControlPlane as _BaseControlPlane
from .notifications_runtime_composition import ControlPlaneHTTP, build_openapi

_AUTOMATION_FAILURE_OUTCOMES = frozenset({"failed", "rejected"})
type ApprovalRecipientResolver = Callable[
    [str, ApprovalRecord], Awaitable[tuple[RecipientRef, ...]]
]


class ControlPlane(_BaseControlPlane):
    """Notification runtime plus direct integration with completed canonical source domains."""

    def __init__(
        self,
        *args: Any,
        approval_gate: AuthorizationGate | None = None,
        approval_recipient_resolver: ApprovalRecipientResolver | None = None,
        accounting_service: AccountingService | None = None,
        **kwargs: Any,
    ) -> None:
        provided_sink = cast(AutomationEventSink | None, kwargs.get("automation_event_sink"))
        holder: list[ControlPlane] = []
        self._approval_recipient_resolver = approval_recipient_resolver
        self._accounting_service = accounting_service
        self._accounting_recovery_complete = accounting_service is None
        self._source_attention_queue: SimpleQueue[NotificationCandidate] = SimpleQueue()

        async def notification_automation_sink(event: dict[str, JsonValue]) -> None:
            if provided_sink is not None:
                await provided_sink(event)
            if holder:
                await holder[0]._project_automation_event(event)

        if kwargs.get("automation_service") is None:
            kwargs["automation_event_sink"] = notification_automation_sink

        if accounting_service is not None:
            provided_threshold_sink = accounting_service.threshold_event_sink

            def notification_accounting_sink(event: BudgetThresholdEvent) -> None:
                if provided_threshold_sink is not None:
                    provided_threshold_sink(event)
                self._enqueue_budget_threshold(accounting_service, event)

            accounting_service.threshold_event_sink = notification_accounting_sink

        super().__init__(*args, **kwargs)
        holder.append(self)
        if approval_gate is not None and approval_recipient_resolver is not None:
            approval_gate.add_approval_event_sink(self._project_approval_event)

    async def evaluate_task_attention_reminders(
        self,
        *,
        now: datetime | None = None,
        approaching_window: timedelta | None = None,
    ) -> tuple[Notification, ...]:
        """Evaluate #88 reminders and completed-domain attention projections."""

        created = list(
            await super().evaluate_task_attention_reminders(
                now=now,
                approaching_window=approaching_window,
            )
        )

        while True:
            try:
                candidate = self._source_attention_queue.get_nowait()
            except Empty:
                break
            try:
                notification = await self.notification_service.create_once(candidate, now=now)
            except Exception:
                continue
            if notification is not None:
                created.append(notification)

        accounting = self._accounting_service
        if accounting is not None and not self._accounting_recovery_complete:
            recovered, complete = await self._recover_persisted_budget_thresholds(
                accounting,
                now=now,
            )
            created.extend(recovered)
            self._accounting_recovery_complete = complete

        return tuple(created)

    async def _project_approval_event(self, event: str, approval: ApprovalRecord) -> None:
        """Project #15 Approval lifecycle after #15 has committed its authoritative state."""

        resolver = self._approval_recipient_resolver
        if resolver is None:
            return
        try:
            recipients = await resolver(event, approval)
            for recipient in tuple(dict.fromkeys(recipients)):
                candidate = (
                    approval_required_candidate(approval, recipient=recipient)
                    if event == "required"
                    else approval_resolved_candidate(approval, recipient=recipient)
                    if event == "resolved"
                    else None
                )
                if candidate is not None:
                    await self.notification_service.create_once(candidate)
        except Exception:
            # Approval remains authoritative. Attention projection cannot turn a successful
            # approval transition into an authorization failure.
            return

    def _enqueue_budget_threshold(
        self,
        accounting: AccountingService,
        event: BudgetThresholdEvent,
    ) -> None:
        """Queue a synchronous #76 threshold event for the autonomous Notification runtime."""

        try:
            budget = accounting.store.get_budget(event.budget_id)
            if budget is None or budget.owner_type is None or budget.owner_id is None:
                return
            recipient = RecipientRef(RecipientType(budget.owner_type), budget.owner_id)
            self._source_attention_queue.put(budget_threshold_candidate(event, recipient=recipient))
        except Exception:
            # Accounting already owns and committed the budget/usage state. Invalid or missing
            # recipient metadata must not make accounting ingestion fail.
            return

    async def _recover_persisted_budget_thresholds(
        self,
        accounting: AccountingService,
        *,
        now: datetime | None,
    ) -> tuple[tuple[Notification, ...], bool]:
        """Reconstruct lost #76 attention from durable budget/threshold state after restart.

        Accounting persists the threshold level before its synchronous observer runs. If a process
        dies in that gap, no in-memory queue item survives. The first Notification runtime pass
        therefore projects the currently persisted threshold state once. Existing historical
        attention with the same deterministic aggregation identity suppresses restart duplicates.
        Transient projection failures keep recovery pending for the next runtime tick.
        """

        created: list[Notification] = []
        retry_required = False
        for budget in accounting.store.list_budgets():
            level = accounting.store.get_threshold_level(budget.id)
            if level is None:
                continue
            if budget.owner_type is None or budget.owner_id is None:
                continue
            try:
                state = accounting.budget_state(budget.id)
                if state.level != level:
                    # Rolling/current usage no longer supports the persisted attention state.
                    # Notifications must not resurrect stale accounting truth.
                    continue
                recipient = RecipientRef(RecipientType(budget.owner_type), budget.owner_id)
                event = BudgetThresholdEvent(
                    budget_id=budget.id,
                    level=level,
                    consumed=state.consumed,
                    limit=budget.limit,
                    metric_type=budget.metric_type,
                    unit=budget.unit,
                    scope_type=budget.scope_type,
                    scope_id=budget.scope_id,
                    action=budget.action,
                    budget_version=budget.version,
                )
                candidate = budget_threshold_candidate(event, recipient=recipient)
                if await self._has_notification_history(candidate):
                    continue
                notification = await self.notification_service.create_once(candidate, now=now)
                if notification is not None:
                    created.append(notification)
            except Exception:
                retry_required = True
        return tuple(created), not retry_required

    async def _has_notification_history(self, candidate: NotificationCandidate) -> bool:
        aggregation_key = candidate.aggregation_key
        if aggregation_key is None:
            return False
        history = await self.notification_service.list(
            NotificationQuery(
                recipient=candidate.recipient,
                include_archived=True,
            )
        )
        return any(item.aggregation_key == aggregation_key for item in history)

    async def connector_health_event_sink(
        self,
        previous: Connection,
        current: Connection,
    ) -> None:
        """Project #44 degraded/error health after Connection state is committed."""

        del previous
        try:
            status = current.status.value
            if status not in {"degraded", "error"}:
                return
            recipient = RecipientRef(RecipientType(current.owner_type), current.owner_id)
            await self.notification_service.create_once(
                canonical_attention_candidate(
                    category=NotificationCategory.CONNECTOR,
                    recipient=recipient,
                    source=SourceRef("connector", current.id),
                    attention=f"health:{status}",
                    title=(
                        "Connector connection failed"
                        if status == "error"
                        else "Connector connection degraded"
                    ),
                    severity=(
                        NotificationSeverity.ERROR
                        if status == "error"
                        else NotificationSeverity.WARNING
                    ),
                    project_id=current.project_id,
                )
            )
        except Exception:
            # #44 has already committed the authoritative health transition. A Notification
            # projection failure must not alter the Connector result.
            return

    async def _project_automation_event(self, event: dict[str, JsonValue]) -> None:
        """Project #18 failures without allowing attention failure to fail Automation."""

        try:
            if event.get("type") != "automation.delivery":
                return
            outcome = event.get("outcome")
            if not isinstance(outcome, str) or outcome not in _AUTOMATION_FAILURE_OUTCOMES:
                return
            automation_id = event.get("automation_id")
            delivery_id = event.get("trigger_delivery_id")
            if not isinstance(automation_id, str) or not isinstance(delivery_id, str):
                return
            automation = await self.automation_service.get_automation(automation_id)
            try:
                recipient_type = RecipientType(automation.identity.owner_type)
                recipient = RecipientRef(recipient_type, automation.identity.owner_id)
            except ValueError:
                return
            generated_task_id = event.get("generated_task_id")
            task_id = generated_task_id if isinstance(generated_task_id, str) else None
            error_code = event.get("error_code")
            safe_error = error_code if isinstance(error_code, str) else None
            await self.notification_service.create_once(
                NotificationCandidate(
                    category=NotificationCategory.AUTOMATION,
                    severity=NotificationSeverity.ERROR,
                    title="Automation failed",
                    summary={
                        "automation_id": automation.id,
                        "trigger_delivery_id": delivery_id,
                        "outcome": outcome,
                        "error_code": safe_error,
                    },
                    recipient=recipient,
                    source=SourceRef("automation", automation.id),
                    project_id=automation.project_id,
                    workspace_id=automation.workspace_id,
                    task_id=task_id,
                    automation_id=automation.id,
                    resource_ref=SourceRef("automation", automation.id),
                    actions=(
                        NotificationAction(
                            action_id="open-automation",
                            label="Open automation",
                            resource_type="automation",
                            resource_id=automation.id,
                            href=f"/automations/{automation.id}",
                        ),
                    ),
                    aggregation_key=(
                        f"automation:{automation.id}:delivery:{delivery_id}:{outcome}"
                    ),
                )
            )
        except Exception:
            # #18 is already authoritative and committed when its event sink runs. Attention
            # projection is best-effort and must never falsify Automation lifecycle failure.
            return


__all__ = [
    "ApprovalRecipientResolver",
    "ControlPlane",
    "ControlPlaneHTTP",
    "build_openapi",
]
