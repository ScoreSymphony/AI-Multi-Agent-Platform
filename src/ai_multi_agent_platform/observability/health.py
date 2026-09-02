"""Backend-neutral liveness/readiness/degradation semantics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ReadinessState(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    DRAINING = "draining"


@dataclass(frozen=True, slots=True)
class DependencyHealth:
    name: str
    state: ReadinessState
    required: bool = True
    detail: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("dependency name must not be blank")


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
) -> ServiceHealth:
    """Aggregate dependency state without making optional integrations fatal."""

    if not alive:
        return ServiceHealth(
            alive=False,
            readiness=ReadinessState.UNAVAILABLE,
            dependencies=dependencies,
        )
    if draining:
        return ServiceHealth(
            alive=True,
            readiness=ReadinessState.DRAINING,
            dependencies=dependencies,
        )

    required_unavailable = any(
        dependency.required and dependency.state is ReadinessState.UNAVAILABLE
        for dependency in dependencies
    )
    if required_unavailable:
        readiness = ReadinessState.UNAVAILABLE
    elif any(
        dependency.state in {ReadinessState.DEGRADED, ReadinessState.UNAVAILABLE}
        for dependency in dependencies
    ):
        readiness = ReadinessState.DEGRADED
    else:
        readiness = ReadinessState.READY
    return ServiceHealth(alive=True, readiness=readiness, dependencies=dependencies)
