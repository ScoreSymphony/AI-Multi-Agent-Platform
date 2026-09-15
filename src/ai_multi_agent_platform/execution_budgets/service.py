"""Platform-owned admission and reservation enforcement for autonomous Task execution."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime, timedelta
from math import isfinite
from typing import Protocol

from ai_multi_agent_platform.accounting import (
    AccountingService,
    AsyncAccountingService,
    MeasurementQuality,
    UsageQuery,
    UsageScope,
    aggregate_usage_records,
    runtime_accounting_service,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .models import (
    BudgetActionKind,
    BudgetAdmissionDecision,
    BudgetAdmissionOutcome,
    BudgetConsumption,
    BudgetConsumptionSource,
    BudgetDimension,
    BudgetDimensionSnapshot,
    BudgetExhaustionAction,
    BudgetReservation,
    ReservationState,
    TaskBudgetLimit,
    TaskBudgetPolicy,
    TaskBudgetSnapshot,
    UnavailableMetricPolicy,
    utc_now,
)
from .store import ReservationClaim, TaskBudgetStore

AccountingRuntime = AccountingService | AsyncAccountingService

_ACTION_DIMENSIONS: dict[BudgetActionKind, frozenset[BudgetDimension]] = {
    BudgetActionKind.MODEL_CALL: frozenset(
        {
            BudgetDimension.MODEL_CALLS,
            BudgetDimension.MODEL_TOKENS,
            BudgetDimension.EXTERNAL_COST,
            BudgetDimension.RUNTIME_SECONDS,
        }
    ),
    BudgetActionKind.TOOL_CALL: frozenset(
        {
            BudgetDimension.TOOL_CALLS,
            BudgetDimension.EXTERNAL_COST,
            BudgetDimension.RUNTIME_SECONDS,
        }
    ),
    BudgetActionKind.REPLAN: frozenset(
        {BudgetDimension.REPLANS, BudgetDimension.RUNTIME_SECONDS}
    ),
    BudgetActionKind.REPAIR: frozenset(
        {BudgetDimension.REPAIRS, BudgetDimension.RUNTIME_SECONDS}
    ),
    BudgetActionKind.PARALLEL_STEP: frozenset(
        {BudgetDimension.PARALLEL_STEPS, BudgetDimension.RUNTIME_SECONDS}
    ),
    BudgetActionKind.RETRY: frozenset({BudgetDimension.RUNTIME_SECONDS}),
    BudgetActionKind.OTHER: frozenset({BudgetDimension.RUNTIME_SECONDS}),
}


class TaskBudgetAdmission(Protocol):
    """Narrow runtime seam used by autonomous model/tool/planning execution paths."""

    async def admit(
        self,
        *,
        task_id: str,
        action: BudgetActionKind,
        quantities: Mapping[BudgetDimension, float] | None = None,
        run_id: str | None = None,
        step_id: str | None = None,
        agent_id: str | None = None,
        agent_run_id: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        provenance: Mapping[str, JsonValue] | None = None,
    ) -> BudgetAdmissionDecision: ...

    async def reconcile(
        self,
        decision: BudgetAdmissionDecision,
        *,
        actual_quantities: Mapping[BudgetDimension, float] | None = None,
    ) -> None: ...

    async def release(self, decision: BudgetAdmissionDecision) -> None: ...


class TaskBudgetEnforcementService(TaskBudgetAdmission):
    """Enforce Task limits without becoming a second #76 accounting ledger."""

    def __init__(
        self,
        store: TaskBudgetStore,
        accounting: AccountingRuntime,
        *,
        reservation_ttl_seconds: int = 300,
    ) -> None:
        if reservation_ttl_seconds <= 0:
            raise ValueError("reservation_ttl_seconds must be greater than zero")
        self._store = store
        self._accounting = runtime_accounting_service(accounting)
        self._reservation_ttl_seconds = reservation_ttl_seconds

    async def put_policy(self, policy: TaskBudgetPolicy) -> TaskBudgetSnapshot:
        await asyncio.to_thread(self._store.put_policy, policy)
        return await self.snapshot(policy.task_id)

    async def policy(self, task_id: str) -> TaskBudgetPolicy | None:
        return await asyncio.to_thread(self._store.get_policy, task_id)

    async def policy_history(self, task_id: str) -> tuple[TaskBudgetPolicy, ...]:
        return await asyncio.to_thread(self._store.list_policy_versions, task_id)

    async def snapshot(
        self,
        task_id: str,
        *,
        observed_at: datetime | None = None,
    ) -> TaskBudgetSnapshot:
        now = observed_at or utc_now()
        policy = await asyncio.to_thread(self._store.get_policy, task_id)
        if policy is None:
            raise KeyError(task_id)
        await asyncio.to_thread(self._store.expire_reservations, now)
        reservations = await asyncio.to_thread(self._store.list_reservations, task_id)
        dimensions: list[BudgetDimensionSnapshot] = []
        for limit in policy.limits:
            consumption = await self._consumption(policy, limit, observed_at=now)
            reserved = sum(
                reservation.quantity
                for reservation in reservations
                if reservation.dimension is limit.dimension
                and reservation.state is ReservationState.ACTIVE
            )
            dimensions.append(
                BudgetDimensionSnapshot(
                    limit=limit,
                    consumed=consumption.consumed,
                    reserved=reserved,
                    remaining=max(0.0, limit.limit - consumption.consumed - reserved),
                    quality_counts=dict(consumption.quality_counts),
                    unavailable_count=consumption.unavailable_count,
                )
            )
        return TaskBudgetSnapshot(
            task_id=task_id,
            policy_version=policy.version,
            dimensions=tuple(dimensions),
            observed_at=now,
        )

    async def admit(
        self,
        *,
        task_id: str,
        action: BudgetActionKind,
        quantities: Mapping[BudgetDimension, float] | None = None,
        run_id: str | None = None,
        step_id: str | None = None,
        agent_id: str | None = None,
        agent_run_id: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        provenance: Mapping[str, JsonValue] | None = None,
    ) -> BudgetAdmissionDecision:
        policy = await asyncio.to_thread(self._store.get_policy, task_id)
        if policy is None:
            return BudgetAdmissionDecision(
                task_id=task_id,
                action=action,
                outcome=BudgetAdmissionOutcome.ALLOWED,
                reason="no Task execution budget policy configured",
            )

        requested = dict(quantities or {})
        relevant = _relevant_dimensions(action, requested)
        now = utc_now()
        snapshot = await self.snapshot(task_id, observed_at=now)
        unavailable = _first_unavailable(snapshot, relevant)
        if unavailable is not None:
            limit = unavailable.limit
            if limit.unavailable_policy is UnavailableMetricPolicy.REQUIRE_APPROVAL:
                return _decision(
                    task_id,
                    action,
                    BudgetAdmissionOutcome.APPROVAL_REQUIRED,
                    "configured hard budget metric is unavailable and requires Approval",
                    snapshot,
                    limit.dimension,
                )
            if limit.unavailable_policy is UnavailableMetricPolicy.BLOCK:
                return _decision(
                    task_id,
                    action,
                    BudgetAdmissionOutcome.METRIC_UNAVAILABLE,
                    "configured hard budget metric is unavailable",
                    snapshot,
                    limit.dimension,
                )

        exhausted = next(
            (
                item
                for item in snapshot.dimensions
                if item.limit.dimension in relevant and item.exhausted
            ),
            None,
        )
        if exhausted is not None:
            return _exhausted_decision(task_id, action, snapshot, exhausted.limit)

        claims: list[ReservationClaim] = []
        reservations: list[BudgetReservation] = []
        for dimension, quantity in requested.items():
            if not isfinite(quantity) or quantity <= 0:
                raise ValueError("requested budget reservation quantity must be positive and finite")
            limit = policy.limit_for(dimension)
            if limit is None:
                continue
            if limit.source is BudgetConsumptionSource.CLOCK:
                raise ValueError("clock-backed runtime budget cannot be explicitly reserved")
            state = snapshot.for_dimension(dimension)
            assert state is not None
            if state.consumed + state.reserved + quantity > limit.limit:
                return _exhausted_decision(task_id, action, snapshot, limit)
            reservation = BudgetReservation(
                task_id=task_id,
                dimension=dimension,
                quantity=quantity,
                action=action,
                run_id=run_id,
                step_id=step_id,
                agent_id=agent_id,
                agent_run_id=agent_run_id,
                correlation_id=correlation_id,
                causation_id=causation_id,
                created_at=now,
                expires_at=now + timedelta(seconds=self._reservation_ttl_seconds),
                provenance=dict(provenance or {}),
            )
            reservations.append(reservation)
            claims.append(
                ReservationClaim(
                    reservation=reservation,
                    limit=limit.limit,
                    external_consumed=(
                        state.consumed
                        if limit.source is BudgetConsumptionSource.ACCOUNTING
                        else 0.0
                    ),
                    include_runtime_counter=(
                        limit.source is BudgetConsumptionSource.RUNTIME_COUNTER
                    ),
                )
            )

        if claims and not await asyncio.to_thread(self._store.try_reserve_many, tuple(claims)):
            current = await self.snapshot(task_id)
            blocking = _blocking_requested_dimension(current, requested)
            if blocking is not None:
                return _exhausted_decision(task_id, action, current, blocking.limit)
            return _decision(
                task_id,
                action,
                BudgetAdmissionOutcome.BLOCKED,
                "Task budget reservation lost an atomic admission race",
                current,
                claims[0].reservation.dimension,
            )

        admitted = await self.snapshot(task_id)
        warning = next(
            (
                item
                for item in admitted.dimensions
                if item.limit.dimension in relevant and item.warning
            ),
            None,
        )
        outcome = (
            BudgetAdmissionOutcome.WARNING
            if warning is not None
            else BudgetAdmissionOutcome.ALLOWED
        )
        return BudgetAdmissionDecision(
            task_id=task_id,
            action=action,
            outcome=outcome,
            reason=(
                "Task budget warning threshold reached"
                if warning is not None
                else "Task budget admission allowed"
            ),
            reservations=tuple(reservations),
            blocking_dimension=None if warning is None else warning.limit.dimension,
            snapshot=admitted,
        )

    async def reconcile(
        self,
        decision: BudgetAdmissionDecision,
        *,
        actual_quantities: Mapping[BudgetDimension, float] | None = None,
    ) -> None:
        if not decision.permitted:
            raise ValueError("cannot reconcile a denied Task budget admission")
        quantities = dict(actual_quantities or {})
        policy = await asyncio.to_thread(self._store.get_policy, decision.task_id)
        if policy is None and decision.reservations:
            raise ValueError("Task budget policy disappeared while reservations were active")
        for reservation in decision.reservations:
            assert policy is not None
            limit = policy.limit_for(reservation.dimension)
            if limit is None:
                raise ValueError("Task budget dimension disappeared while reservation was active")
            quantity = quantities.get(reservation.dimension, reservation.quantity)
            if not isfinite(quantity) or quantity < 0:
                raise ValueError("actual budget consumption must be finite and non-negative")
            await asyncio.to_thread(
                self._store.reconcile_reservation,
                reservation.id,
                consumed_quantity=quantity,
                add_to_runtime_counter=(
                    limit.source is BudgetConsumptionSource.RUNTIME_COUNTER
                ),
            )

    async def release(self, decision: BudgetAdmissionDecision) -> None:
        for reservation in decision.reservations:
            await asyncio.to_thread(self._store.release_reservation, reservation.id)

    async def require_permitted(self, decision: BudgetAdmissionDecision) -> None:
        if decision.permitted:
            return
        raise ContractError(
            ErrorCode.RESOURCE_EXHAUSTED,
            decision.reason,
            details={
                "budget_outcome": decision.outcome.value,
                "budget_action": decision.action.value,
                "task_id": decision.task_id,
                "blocking_dimension": (
                    None
                    if decision.blocking_dimension is None
                    else decision.blocking_dimension.value
                ),
            },
        )

    async def _consumption(
        self,
        policy: TaskBudgetPolicy,
        limit: TaskBudgetLimit,
        *,
        observed_at: datetime,
    ) -> BudgetConsumption:
        if limit.source is BudgetConsumptionSource.CLOCK:
            elapsed = max(0.0, (observed_at - policy.started_at).total_seconds())
            return BudgetConsumption(consumed=elapsed, source=limit.source)
        if limit.source is BudgetConsumptionSource.RUNTIME_COUNTER:
            counter = await asyncio.to_thread(
                self._store.runtime_counter,
                policy.task_id,
                limit.dimension,
            )
            return BudgetConsumption(consumed=counter, source=limit.source)
        assert limit.metric_type is not None
        assert limit.unit is not None
        records = await self._accounting.query(
            UsageQuery(
                metric_type=limit.metric_type,
                unit=limit.unit,
                scope=UsageScope(task_id=policy.task_id),
            )
        )
        quality_counts = {quality: 0 for quality in MeasurementQuality}
        for record in records:
            quality_counts[record.quality] += 1
        included = tuple(
            record
            for record in records
            if record.quantity is not None
            and (record.quality is not MeasurementQuality.ESTIMATED or limit.include_estimated)
        )
        aggregate = aggregate_usage_records(
            included,
            metric_type=limit.metric_type,
            unit=limit.unit,
        )
        unavailable_count = quality_counts[MeasurementQuality.UNAVAILABLE]
        if not records and limit.dimension is BudgetDimension.EXTERNAL_COST:
            unavailable_count = 1
        return BudgetConsumption(
            consumed=0.0 if aggregate.total is None else aggregate.total,
            source=limit.source,
            quality_counts=quality_counts,
            unavailable_count=unavailable_count,
            record_ids=tuple(record.id for record in records),
        )


def _relevant_dimensions(
    action: BudgetActionKind,
    requested: Mapping[BudgetDimension, float],
) -> frozenset[BudgetDimension]:
    return _ACTION_DIMENSIONS[action] | frozenset(requested)


def _first_unavailable(
    snapshot: TaskBudgetSnapshot,
    relevant: frozenset[BudgetDimension],
) -> BudgetDimensionSnapshot | None:
    for item in snapshot.dimensions:
        if (
            item.limit.dimension in relevant
            and item.unavailable_count > 0
            and item.limit.unavailable_policy is not UnavailableMetricPolicy.ALLOW
        ):
            return item
    return None


def _blocking_requested_dimension(
    snapshot: TaskBudgetSnapshot,
    requested: Mapping[BudgetDimension, float],
) -> BudgetDimensionSnapshot | None:
    for dimension, quantity in requested.items():
        item = snapshot.for_dimension(dimension)
        if item is not None and item.consumed + item.reserved + quantity > item.limit.limit:
            return item
    return None


def _exhausted_decision(
    task_id: str,
    action: BudgetActionKind,
    snapshot: TaskBudgetSnapshot,
    limit: TaskBudgetLimit,
) -> BudgetAdmissionDecision:
    if limit.exhaustion_action is BudgetExhaustionAction.REQUIRE_APPROVAL:
        outcome = BudgetAdmissionOutcome.APPROVAL_REQUIRED
        reason = f"Task budget exhausted for {limit.dimension.value}; Approval required"
    else:
        outcome = BudgetAdmissionOutcome.BLOCKED
        reason = f"Task budget exhausted for {limit.dimension.value}"
    return _decision(task_id, action, outcome, reason, snapshot, limit.dimension)


def _decision(
    task_id: str,
    action: BudgetActionKind,
    outcome: BudgetAdmissionOutcome,
    reason: str,
    snapshot: TaskBudgetSnapshot,
    blocking_dimension: BudgetDimension,
) -> BudgetAdmissionDecision:
    return BudgetAdmissionDecision(
        task_id=task_id,
        action=action,
        outcome=outcome,
        reason=reason,
        blocking_dimension=blocking_dimension,
        snapshot=snapshot,
    )


__all__ = ["TaskBudgetAdmission", "TaskBudgetEnforcementService"]
