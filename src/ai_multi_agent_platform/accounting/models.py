"""Canonical provider-neutral usage, resource accounting and budget value types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from uuid import UUID, uuid4

from ai_multi_agent_platform.contracts.types import JsonValue


def utc_now() -> datetime:
    return datetime.now(UTC)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4()}"


def _validate_accounting_id(value: str, prefix: str) -> None:
    expected = f"{prefix}_"
    if not value.startswith(expected):
        raise ValueError(f"expected canonical {prefix} id")
    try:
        UUID(value[len(expected) :])
    except ValueError as exc:
        raise ValueError(f"invalid canonical {prefix} id") from exc


def _require_aware(value: datetime, name: str) -> None:
    if value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


SUPPORTED_BUDGET_SCOPE_TYPES = frozenset(
    {
        "user",
        "organization",
        "team",
        "project",
        "workspace",
        "task",
        "run",
        "agent",
        "capability",
        "model_config",
        "model_provider",
        "worker",
        "node",
    }
)


class AggregationMode(StrEnum):
    """How repeated records of one canonical metric combine."""

    ADDITIVE = "additive"
    LATEST = "latest"


class MeasurementQuality(StrEnum):
    """How trustworthy the recorded quantity is and where it came from."""

    MEASURED = "measured"
    REPORTED = "reported"
    ESTIMATED = "estimated"
    UNAVAILABLE = "unavailable"


class BudgetKind(StrEnum):
    SOFT = "soft"
    HARD = "hard"


class BudgetWindowMode(StrEnum):
    """How a budget consumption window advances over time."""

    LIFETIME = "lifetime"
    ROLLING = "rolling"


class BudgetAction(StrEnum):
    RECORD_ONLY = "record_only"
    WARN = "warn"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"
    NOTIFY = "notify"


class ThresholdLevel(StrEnum):
    WARNING = "warning"
    EXCEEDED = "exceeded"


@dataclass(frozen=True, slots=True)
class UsageScope:
    """Attribution dimensions owned by the platform, all optional and composable."""

    project_id: str | None = None
    workspace_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    agent_id: str | None = None
    team_id: str | None = None
    capability_id: str | None = None
    model_config_id: str | None = None
    model_provider_id: str | None = None
    worker_id: str | None = None
    node_id: str | None = None
    owner_type: str | None = None
    owner_id: str | None = None
    organization_id: str | None = None

    def __post_init__(self) -> None:
        if (self.owner_type is None) != (self.owner_id is None):
            raise ValueError("owner_type and owner_id must both be set or both be omitted")
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} must not be blank when provided")

    def fields(self) -> dict[str, str]:
        return {
            name: value
            for name in self.__dataclass_fields__
            if (value := getattr(self, name)) is not None
        }


@dataclass(frozen=True, slots=True)
class UsageRecord:
    """One attributable accounting measurement independent of any telemetry backend."""

    metric_type: str
    unit: str
    quality: MeasurementQuality
    source: str
    quantity: float | None = None
    scope: UsageScope = field(default_factory=UsageScope)
    aggregation_mode: AggregationMode = AggregationMode.ADDITIVE
    id: str = field(default_factory=lambda: _new_id("usage"))
    timestamp: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    provider: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    cost_amount: float | None = None
    currency: str | None = None
    precision: float | None = None
    confidence: float | None = None
    provenance: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_accounting_id(self.id, "usage")
        for name in ("metric_type", "unit", "source"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be blank")
        if self.quality is MeasurementQuality.UNAVAILABLE:
            if self.quantity is not None:
                raise ValueError("unavailable usage must not contain a fabricated quantity")
        elif self.quantity is None:
            raise ValueError("available usage requires a quantity")
        elif not isfinite(self.quantity):
            raise ValueError("usage quantity must be finite")
        elif self.quantity < 0:
            raise ValueError("usage quantity must not be negative")
        _require_aware(self.timestamp, "timestamp")
        if self.started_at is not None:
            _require_aware(self.started_at, "started_at")
        if self.ended_at is not None:
            _require_aware(self.ended_at, "ended_at")
        if self.started_at is not None and self.ended_at is not None:
            if self.ended_at < self.started_at:
                raise ValueError("ended_at cannot precede started_at")
        if (self.cost_amount is None) != (self.currency is None):
            raise ValueError("cost_amount and currency must either both be set or both be omitted")
        if self.cost_amount is not None and not isfinite(self.cost_amount):
            raise ValueError("cost_amount must be finite")
        if self.cost_amount is not None and self.cost_amount < 0:
            raise ValueError("cost_amount must not be negative")
        if self.currency is not None:
            normalized = self.currency.upper()
            if len(normalized) != 3 or not normalized.isalpha():
                raise ValueError("currency must be a three-letter ISO currency code")
            object.__setattr__(self, "currency", normalized)
        for name in ("precision", "confidence"):
            value = getattr(self, name)
            if value is not None and (not isfinite(value) or not 0.0 <= value <= 1.0):
                raise ValueError(f"{name} must be finite and between 0 and 1")
        for name in ("provider", "correlation_id", "causation_id"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} must not be blank when provided")


@dataclass(frozen=True, slots=True)
class UsageQuery:
    metric_type: str | None = None
    unit: str | None = None
    scope: UsageScope = field(default_factory=UsageScope)
    quality: MeasurementQuality | None = None
    start: datetime | None = None
    end: datetime | None = None

    def __post_init__(self) -> None:
        if self.start is not None:
            _require_aware(self.start, "query start")
        if self.end is not None:
            _require_aware(self.end, "query end")
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("query end cannot precede start")


@dataclass(frozen=True, slots=True)
class UsageAggregate:
    metric_type: str
    unit: str
    total: float | None
    record_count: int
    unavailable_count: int
    quality_counts: dict[MeasurementQuality, int]
    aggregation_mode: AggregationMode = AggregationMode.ADDITIVE
    start: datetime | None = None
    end: datetime | None = None


@dataclass(frozen=True, slots=True)
class UsageBudget:
    metric_type: str
    unit: str
    scope_type: str
    scope_id: str
    limit: float
    kind: BudgetKind = BudgetKind.SOFT
    action: BudgetAction = BudgetAction.WARN
    warning_fraction: float = 0.8
    window_seconds: int | None = None
    include_estimated: bool = False
    owner_type: str | None = None
    owner_id: str | None = None
    provenance: dict[str, JsonValue] = field(default_factory=dict)
    id: str = field(default_factory=lambda: _new_id("budget"))
    created_at: datetime = field(default_factory=utc_now)
    version: int = 1

    def __post_init__(self) -> None:
        _validate_accounting_id(self.id, "budget")
        for name in ("metric_type", "unit", "scope_type", "scope_id"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be blank")
        if self.scope_type not in SUPPORTED_BUDGET_SCOPE_TYPES:
            raise ValueError(f"unsupported budget scope_type: {self.scope_type}")
        if not isfinite(self.limit) or self.limit <= 0:
            raise ValueError("budget limit must be finite and greater than zero")
        if not isfinite(self.warning_fraction) or not 0.0 < self.warning_fraction <= 1.0:
            raise ValueError("warning_fraction must be finite and within (0, 1]")
        if self.window_seconds is not None and self.window_seconds <= 0:
            raise ValueError("window_seconds must be greater than zero")
        if (self.owner_type is None) != (self.owner_id is None):
            raise ValueError("budget owner_type and owner_id must both be set or both be omitted")
        for name in ("owner_type", "owner_id"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} must not be blank when provided")
        _require_aware(self.created_at, "budget created_at")
        if self.version < 1:
            raise ValueError("budget version must be >= 1")

    @property
    def window_mode(self) -> BudgetWindowMode:
        return (
            BudgetWindowMode.LIFETIME if self.window_seconds is None else BudgetWindowMode.ROLLING
        )


@dataclass(frozen=True, slots=True)
class BudgetState:
    budget: UsageBudget
    consumed: float
    remaining: float
    fraction: float
    level: ThresholdLevel | None
    window_start: datetime | None = None
    window_end: datetime | None = None


@dataclass(frozen=True, slots=True)
class BudgetThresholdEvent:
    """Canonical accounting event consumed later by notification/admission integrations."""

    budget_id: str
    level: ThresholdLevel
    consumed: float
    limit: float
    metric_type: str
    unit: str
    scope_type: str
    scope_id: str
    action: BudgetAction
    budget_version: int
    id: str = field(default_factory=lambda: _new_id("accounting_event"))
    occurred_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _validate_accounting_id(self.id, "accounting_event")
        _validate_accounting_id(self.budget_id, "budget")
        if self.budget_version < 1:
            raise ValueError("budget_version must be >= 1")
