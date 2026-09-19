"""Backend-neutral liveness/readiness/degradation semantics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ReadinessState(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    RECONCILING = "reconciling"
    UNAVAILABLE = "unavailable"
    OPERATOR_INTERVENTION_REQUIRED = "operator_intervention_required"
    DRAINING = "draining"


@dataclass(frozen=True, slots=True)
class DependencyHealth:
    name: str
    state: ReadinessState
    required: bool = True
    detail: str | None = None
    error_code: str | None = None
    attempts: int = 1
    retry_count: int = 0
    last_retry_error_code: str | None = None
    probe_duration_seconds: float = 0.0
    degraded_duration_seconds: float | None = None
    failure_count: int = 0
    recovery_count: int = 0
    operator_action: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("dependency name must not be blank")
        if self.attempts < 1:
            raise ValueError("dependency health attempts must be positive")
        if self.retry_count < 0:
            raise ValueError("dependency health retry_count must not be negative")
        if self.probe_duration_seconds < 0:
            raise ValueError("dependency health probe duration must not be negative")
        if self.degraded_duration_seconds is not None and self.degraded_duration_seconds < 0:
            raise ValueError("dependency degraded duration must not be negative")
        if self.failure_count < 0 or self.recovery_count < 0:
            raise ValueError("dependency health transition counts must not be negative")


@dataclass(frozen=True, slots=True)
class ServiceHealth:
    alive: bool
    readiness: ReadinessState
    dependencies: tuple[DependencyHealth, ...] = ()

    @property
    def ready(self) -> bool:
        return self.alive and self.readiness in {
            ReadinessState.READY,
            ReadinessState.DEGRADED,
        }


def aggregate_health(
    dependencies: tuple[DependencyHealth, ...] = (),
    *,
    alive: bool = True,
    draining: bool = False,
    reconciling: bool = False,
    operator_intervention_required: bool = False,
) -> ServiceHealth:
    """Aggregate dependency state without making optional integrations fatal."""

    if not alive:
        return ServiceHealth(
            alive=False,
            readiness=ReadinessState.UNAVAILABLE,
            dependencies=dependencies,
        )
    if draining or any(
        dependency.required and dependency.state is ReadinessState.DRAINING
        for dependency in dependencies
    ):
        return ServiceHealth(
            alive=True,
            readiness=ReadinessState.DRAINING,
            dependencies=dependencies,
        )
    if operator_intervention_required or any(
        dependency.required and dependency.state is ReadinessState.OPERATOR_INTERVENTION_REQUIRED
        for dependency in dependencies
    ):
        return ServiceHealth(
            alive=True,
            readiness=ReadinessState.OPERATOR_INTERVENTION_REQUIRED,
            dependencies=dependencies,
        )
    if reconciling or any(
        dependency.required and dependency.state is ReadinessState.RECONCILING
        for dependency in dependencies
    ):
        return ServiceHealth(
            alive=True,
            readiness=ReadinessState.RECONCILING,
            dependencies=dependencies,
        )

    required_unavailable = any(
        dependency.required and dependency.state is ReadinessState.UNAVAILABLE
        for dependency in dependencies
    )
    if required_unavailable:
        readiness = ReadinessState.UNAVAILABLE
    elif any(dependency.state is not ReadinessState.READY for dependency in dependencies):
        readiness = ReadinessState.DEGRADED
    else:
        readiness = ReadinessState.READY
    return ServiceHealth(alive=True, readiness=readiness, dependencies=dependencies)
