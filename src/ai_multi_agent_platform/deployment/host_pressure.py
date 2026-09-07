"""Opt-in deployment composition for issue-#500 host-pressure admission."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta

from ai_multi_agent_platform.distributed import (
    DistributedRuntime,
    DistributedTelemetry,
    PressureAdmissionPolicy,
    PressureSnapshotProvider,
    ProtectedHeadroom,
    RegistryPressureSnapshotProvider,
)
from ai_multi_agent_platform.observability import Telemetry


@dataclass(frozen=True, slots=True)
class HostPressureDeploymentConfig:
    """Deployment-owned safety settings; no machine/VPS class is canonical."""

    enabled: bool = False
    require_pressure_report: bool = False
    max_snapshot_age_seconds: float = 30.0
    protected_cpu_cores: float = 0.0
    protected_ram_bytes: int = 0
    protected_storage_bytes: int = 0

    def __post_init__(self) -> None:
        if self.max_snapshot_age_seconds <= 0:
            raise ValueError("host-pressure max snapshot age must be greater than zero")
        if self.protected_cpu_cores < 0:
            raise ValueError("host-pressure protected CPU cores must be non-negative")
        if self.protected_ram_bytes < 0 or self.protected_storage_bytes < 0:
            raise ValueError("host-pressure protected byte headroom must be non-negative")

    def policy(self) -> PressureAdmissionPolicy:
        return PressureAdmissionPolicy(
            protected_headroom=ProtectedHeadroom(
                cpu_cores=self.protected_cpu_cores,
                ram_bytes=self.protected_ram_bytes,
                storage_bytes=self.protected_storage_bytes,
            ),
            max_snapshot_age=timedelta(seconds=self.max_snapshot_age_seconds),
            require_pressure_report=self.require_pressure_report,
        )

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> HostPressureDeploymentConfig:
        """Resolve the explicit #39/#240 pressure opt-in without hidden host tuning."""

        return cls(
            enabled=_bool(environ.get("PLATFORM_HOST_PRESSURE_ENABLED"), default=False),
            require_pressure_report=_bool(
                environ.get("PLATFORM_HOST_PRESSURE_REQUIRE_REPORT"),
                default=False,
            ),
            max_snapshot_age_seconds=_float(
                environ.get("PLATFORM_HOST_PRESSURE_MAX_AGE_SECONDS"),
                default=30.0,
                name="PLATFORM_HOST_PRESSURE_MAX_AGE_SECONDS",
            ),
            protected_cpu_cores=_float(
                environ.get("PLATFORM_HOST_PRESSURE_HEADROOM_CPU_CORES"),
                default=0.0,
                name="PLATFORM_HOST_PRESSURE_HEADROOM_CPU_CORES",
            ),
            protected_ram_bytes=_int(
                environ.get("PLATFORM_HOST_PRESSURE_HEADROOM_RAM_BYTES"),
                default=0,
                name="PLATFORM_HOST_PRESSURE_HEADROOM_RAM_BYTES",
            ),
            protected_storage_bytes=_int(
                environ.get("PLATFORM_HOST_PRESSURE_HEADROOM_STORAGE_BYTES"),
                default=0,
                name="PLATFORM_HOST_PRESSURE_HEADROOM_STORAGE_BYTES",
            ),
        )


def configure_distributed_host_pressure(
    runtime: DistributedRuntime,
    telemetry: Telemetry,
    config: HostPressureDeploymentConfig,
    *,
    provider: PressureSnapshotProvider | None = None,
) -> PressureSnapshotProvider | None:
    """Opt one canonical distributed runtime into pressure admission and #16 telemetry."""

    if not config.enabled:
        return None

    effective_provider = provider or RegistryPressureSnapshotProvider(runtime.registry)
    distributed_telemetry = DistributedTelemetry(telemetry)
    runtime.telemetry = distributed_telemetry
    runtime.scheduler.telemetry = distributed_telemetry
    runtime.scheduler.pressure_provider = effective_provider
    runtime.scheduler.pressure_policy = config.policy()
    return effective_provider


def _bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("host-pressure boolean environment value must be true/false")


def _float(value: str | None, *, default: float, name: str) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if parsed < 0:
        raise ValueError(f"{name} must be non-negative")
    return parsed


def _int(value: str | None, *, default: int, name: str) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed < 0:
        raise ValueError(f"{name} must be non-negative")
    return parsed


__all__ = [
    "HostPressureDeploymentConfig",
    "configure_distributed_host_pressure",
]
