"""Canonical runtime-enforcement models for per-Task autonomous execution budgets."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from importlib import import_module
from math import isfinite
from typing import Any
from uuid import uuid4

from ai_multi_agent_platform.contracts.types import JsonValue


def utc_now() -> datetime:
    return datetime.now(UTC)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4()}"


def _require_aware(value: datetime, name: str) -> None:
    if value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


class BudgetDimension(StrEnum):
    MODEL_TOKENS = "model_tokens"
    EXTERNAL_COST = "external_cost"
    MODEL_CALLS = "model_calls"
    TOOL_CALLS = "tool_calls"
    RUNTIME_SECONDS = "runtime_seconds"
    REPLANS = "replans"
    PARALLEL_STEPS = "parallel_steps"
    RETRIES = "retries"
    REPAIRS = "repairs"


class BudgetConsumptionSource(StrEnum):
    ACCOUNTING = "accounting"
    RUNTIME_COUNTER = "runtime_counter"
    CLOCK = "clock"


class BudgetExhaustionAction(StrEnum):
    BLOCK = "block"
    REQUIRE_APPROVAL = "require_approval"


class UnavailableMetricPolicy(StrEnum):
    BLOCK = "block"
    REQUIRE_APPROVAL = "require_approval"
    ALLOW = "allow"


class BudgetAdmissionOutcome(StrEnum):
    ALLOWED = "allowed"
    WARNING = "warning"
    BLOCKED = "blocked"
    EXHAUSTED_DURING_EXECUTION = "exhausted_during_execution"
    APPROVAL_REQUIRED = "approval_required"
    METRIC_UNAVAILABLE = "metric_unavailable"


class BudgetActionKind(StrEnum):
    MODEL_CALL = "model_call"
    TOOL_CALL = "tool_call"
    REPLAN = "replan"
    REPAIR = "repair"
    PARALLEL_STEP = "parallel_step"
    RETRY = "retry"
    OTHER = "other"


class ReservationState(StrEnum):
    ACTIVE = "active"
    RELEASED = "released"
    RECONCILED = "reconciled"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class TaskBudgetLimit:
    """One enforceable Task budget dimension and its source-of-truth semantics."""

    dimension: BudgetDimension
    limit: float
    source: BudgetConsumptionSource
    metric_type: str | None = None
    unit: str | None = None
    warning_fraction: float = 0.8
    include_estimated: bool = False
    exhaustion_action: BudgetExhaustionAction = BudgetExhaustionAction.BLOCK
    unavailable_policy: UnavailableMetricPolicy = UnavailableMetricPolicy.BLOCK

    def __post_init__(self) -> None:
        if not isfinite(self.limit) or self.limit <= 0:
            raise ValueError("budget limit must be finite and greater than zero")
        if not isfinite(self.warning_fraction) or not 0.0 < self.warning_fraction <= 1.0:
            raise ValueError("warning_fraction must be finite and within (0, 1]")
        if self.source is BudgetConsumptionSource.ACCOUNTING:
            if self.metric_type is None or self.unit is None:
                raise ValueError("accounting-backed budget requires metric_type and unit")
            if not self.metric_type.strip() or not self.unit.strip():
                raise ValueError("accounting metric_type and unit must not be blank")
        elif self.metric_type is not None or self.unit is not None:
            raise ValueError("only accounting-backed budgets may define metric_type/unit")
        if self.source is BudgetConsumptionSource.CLOCK:
            if self.dimension is not BudgetDimension.RUNTIME_SECONDS:
                raise ValueError("clock-backed budget is only valid for runtime_seconds")
        elif self.dimension is BudgetDimension.RUNTIME_SECONDS:
            raise ValueError("runtime_seconds budget must use the clock source")


@dataclass(frozen=True, slots=True)
class TaskBudgetPolicy:
    """Versioned immutable configured limits for one canonical Task."""

    task_id: str
    limits: tuple[TaskBudgetLimit, ...]
    started_at: datetime
    version: int = 1
    provenance: dict[str, JsonValue] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("task_id must not be blank")
        if not self.limits:
            raise ValueError("Task budget policy requires at least one limit")
        _require_aware(self.started_at, "started_at")
        _require_aware(self.updated_at, "updated_at")
        if self.version < 1:
            raise ValueError("budget policy version must be >= 1")
        dimensions = [limit.dimension for limit in self.limits]
        if len(set(dimensions)) != len(dimensions):
            raise ValueError("Task budget policy cannot define one dimension more than once")

    def limit_for(self, dimension: BudgetDimension) -> TaskBudgetLimit | None:
        return next((limit for limit in self.limits if limit.dimension is dimension), None)


@dataclass(frozen=True, slots=True)
class BudgetReservation:
    """One durable claim against remaining Task budget."""

    task_id: str
    dimension: BudgetDimension
    quantity: float
    action: BudgetActionKind
    id: str = field(default_factory=lambda: _new_id("budget_reservation"))
    state: ReservationState = ReservationState.ACTIVE
    run_id: str | None = None
    step_id: str | None = None
    agent_id: str | None = None
    agent_run_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    expires_at: datetime | None = None
    reconciled_quantity: float | None = None
    provenance: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("reservation task_id must not be blank")
        if not isfinite(self.quantity) or self.quantity <= 0:
            raise ValueError("reservation quantity must be finite and greater than zero")
        _require_aware(self.created_at, "reservation created_at")
        if self.expires_at is not None:
            _require_aware(self.expires_at, "reservation expires_at")
            if self.expires_at <= self.created_at:
                raise ValueError("reservation expires_at must be later than created_at")
        if self.reconciled_quantity is not None:
            if not isfinite(self.reconciled_quantity) or self.reconciled_quantity < 0:
                raise ValueError("reconciled_quantity must be finite and non-negative")
        for name in (
            "run_id",
            "step_id",
            "agent_id",
            "agent_run_id",
            "correlation_id",
            "causation_id",
        ):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} must not be blank when provided")


@dataclass(frozen=True, slots=True)
class BudgetConsumption:
    consumed: float
    source: BudgetConsumptionSource
    quality_counts: dict[Any, int] = field(default_factory=dict)
    unavailable_count: int = 0
    record_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BudgetDimensionSnapshot:
    limit: TaskBudgetLimit
    consumed: float
    reserved: float
    remaining: float
    quality_counts: dict[Any, int] = field(default_factory=dict)
    unavailable_count: int = 0

    @property
    def fraction(self) -> float:
        return (self.consumed + self.reserved) / self.limit.limit

    @property
    def warning(self) -> bool:
        return self.fraction >= self.limit.warning_fraction

    @property
    def exhausted(self) -> bool:
        return self.consumed + self.reserved >= self.limit.limit

    @property
    def overrun(self) -> bool:
        """Whether trustworthy consumed usage itself is already beyond the configured limit."""

        return self.consumed > self.limit.limit


@dataclass(frozen=True, slots=True)
class TaskBudgetSnapshot:
    task_id: str
    policy_version: int
    dimensions: tuple[BudgetDimensionSnapshot, ...]
    observed_at: datetime

    def for_dimension(self, dimension: BudgetDimension) -> BudgetDimensionSnapshot | None:
        return next((item for item in self.dimensions if item.limit.dimension is dimension), None)


@dataclass(frozen=True, slots=True)
class BudgetAdmissionDecision:
    task_id: str
    action: BudgetActionKind
    outcome: BudgetAdmissionOutcome
    reason: str
    reservations: tuple[BudgetReservation, ...] = ()
    blocking_dimension: BudgetDimension | None = None
    snapshot: TaskBudgetSnapshot | None = None

    @property
    def permitted(self) -> bool:
        return self.outcome in {BudgetAdmissionOutcome.ALLOWED, BudgetAdmissionOutcome.WARNING}


def __getattr__(name: str) -> Any:
    """Compatibility-load store-owned claims for dynamic deployment bindings."""

    if name != "ReservationClaim":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(".store", __package__), name)
    globals()[name] = value
    return value


__all__ = [
    "BudgetActionKind",
    "BudgetAdmissionDecision",
    "BudgetAdmissionOutcome",
    "BudgetConsumption",
    "BudgetConsumptionSource",
    "BudgetDimension",
    "BudgetDimensionSnapshot",
    "BudgetExhaustionAction",
    "BudgetReservation",
    "ReservationState",
    "TaskBudgetLimit",
    "TaskBudgetPolicy",
    "TaskBudgetSnapshot",
    "UnavailableMetricPolicy",
    "utc_now",
]