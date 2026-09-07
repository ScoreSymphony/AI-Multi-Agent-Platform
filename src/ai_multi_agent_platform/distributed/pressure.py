"""Portable host-pressure contracts and deterministic admission policy for issue #500.

The module deliberately owns no scheduling, reservation, transport or lifecycle state.  It
supplies read-only pressure evidence and an admission decision that the canonical #14 scheduler
may consult after ordinary eligibility checks and before reservation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from ai_multi_agent_platform.contracts.types import AdapterMetadata

from .models import JobRequirements, NodeRecord, ResourceSnapshot, WorkerRecord, utc_now


class PressureState(StrEnum):
    """Portable normalized pressure state independent from one OS/provider."""

    HEALTHY = "healthy"
    ELEVATED = "elevated"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class PressureKind(StrEnum):
    """Portable pressure dimensions; provider-native metric names stay in metadata."""

    CPU = "cpu"
    MEMORY = "memory"
    PAGING = "paging"
    STORAGE = "storage"
    IO = "io"
    PROCESS = "process"
    FILE_DESCRIPTOR = "file_descriptor"
    INODE = "inode"
    THROTTLING = "throttling"
    EXHAUSTION = "exhaustion"
    HEADROOM = "headroom"


class AdmissionAction(StrEnum):
    ADMIT = "admit"
    QUEUE = "queue"
    DENY_TEMPORARILY = "deny_temporarily"
    BLOCK_FOR_MAINTENANCE = "block_for_maintenance"


class AdmissionReasonCode(StrEnum):
    NODE_MAINTENANCE = "node_maintenance"
    WORKER_DRAINING = "worker_draining"
    PROTECTED_HEADROOM = "protected_headroom"
    REPORT_MISSING = "pressure_report_missing"
    REPORT_UNTRUSTED = "pressure_report_untrusted"
    REPORT_STALE = "pressure_report_stale"
    PRESSURE_CRITICAL = "pressure_critical"
    PRESSURE_ELEVATED = "pressure_elevated"
    PRESSURE_UNKNOWN = "pressure_unknown"


@dataclass(frozen=True, slots=True)
class PressureSignal:
    """One portable normalized pressure signal plus an optional meaningful measurement."""

    kind: PressureKind
    state: PressureState
    value: float | None = None
    unit: str | None = None

    def __post_init__(self) -> None:
        if self.value is not None and self.value < 0:
            raise ValueError("pressure signal value must be non-negative")
        if self.unit is not None and not self.unit.strip():
            raise ValueError("pressure signal unit must not be blank")


@dataclass(frozen=True, slots=True)
class HostPressureSnapshot:
    """Backend-neutral resource-pressure report for one Node observation."""

    state: PressureState
    observed_at: datetime = field(default_factory=utc_now)
    signals: tuple[PressureSignal, ...] = ()
    source_ref: str | None = None
    trusted: bool = True
    provider_metadata: tuple[AdapterMetadata, ...] = ()

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("pressure observed_at must be timezone-aware")
        if self.source_ref is not None and not self.source_ref.strip():
            raise ValueError("pressure source_ref must not be blank")
        kinds = [signal.kind for signal in self.signals]
        if len(kinds) != len(set(kinds)):
            raise ValueError("pressure snapshot cannot contain duplicate signal kinds")

    def signal(self, kind: PressureKind) -> PressureSignal | None:
        return next((signal for signal in self.signals if signal.kind is kind), None)


@dataclass(frozen=True, slots=True)
class ProtectedHeadroom:
    """Deployment-configured capacity that admission must leave available for recovery."""

    cpu_cores: float = 0.0
    ram_bytes: int = 0
    storage_bytes: int = 0

    def __post_init__(self) -> None:
        if self.cpu_cores < 0 or self.ram_bytes < 0 or self.storage_bytes < 0:
            raise ValueError("protected headroom values must be non-negative")


@dataclass(frozen=True, slots=True)
class AdmissionReason:
    code: AdmissionReasonCode
    message: str

    def __post_init__(self) -> None:
        if not self.message.strip():
            raise ValueError("admission reason message must not be blank")


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    """Deterministic answer consumed by the canonical scheduler before reservation."""

    action: AdmissionAction
    pressure_state: PressureState
    reasons: tuple[AdmissionReason, ...] = ()
    snapshot_age_seconds: float | None = None

    @property
    def admits(self) -> bool:
        return self.action is AdmissionAction.ADMIT


class PressureSnapshotProvider(Protocol):
    """Replaceable local/remote pressure evidence source."""

    def snapshot_for_node(self, node_id: str) -> HostPressureSnapshot | None: ...


class InMemoryPressureSnapshotProvider:
    """Deterministic provider for tests and reference single-process compositions."""

    def __init__(self) -> None:
        self._snapshots: dict[str, HostPressureSnapshot] = {}

    def put(self, node_id: str, snapshot: HostPressureSnapshot) -> None:
        if not node_id.strip():
            raise ValueError("node_id must not be blank")
        self._snapshots[node_id] = snapshot

    def remove(self, node_id: str) -> None:
        self._snapshots.pop(node_id, None)

    def snapshot_for_node(self, node_id: str) -> HostPressureSnapshot | None:
        return self._snapshots.get(node_id)


@dataclass(frozen=True, slots=True)
class PressureAdmissionPolicy:
    """Portable, deterministic admission policy over supplied pressure evidence."""

    protected_headroom: ProtectedHeadroom = field(default_factory=ProtectedHeadroom)
    max_snapshot_age: timedelta = timedelta(seconds=30)
    require_pressure_report: bool = False
    queue_elevated_workload_classes: frozenset[str] = frozenset(
        {"heavy", "exclusive", "infrastructure-heavy"}
    )

    def __post_init__(self) -> None:
        if self.max_snapshot_age <= timedelta(0):
            raise ValueError("max_snapshot_age must be positive")
        if any(not item.strip() for item in self.queue_elevated_workload_classes):
            raise ValueError("workload class names must not be blank")

    def decide(
        self,
        *,
        node: NodeRecord,
        worker: WorkerRecord,
        requirements: JobRequirements,
        available: ResourceSnapshot,
        snapshot: HostPressureSnapshot | None,
        now: datetime | None = None,
        workload_class: str | None = None,
    ) -> AdmissionDecision:
        """Return a decision without mutating scheduler, reservation or lifecycle state."""

        current = now or utc_now()
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("admission now must be timezone-aware")
        if workload_class is not None and not workload_class.strip():
            raise ValueError("workload_class must not be blank")

        if node.maintenance or node.draining:
            return self._decision(
                AdmissionAction.BLOCK_FOR_MAINTENANCE,
                snapshot,
                AdmissionReasonCode.NODE_MAINTENANCE,
                "node is in maintenance or draining",
                current,
            )
        if worker.draining:
            return self._decision(
                AdmissionAction.BLOCK_FOR_MAINTENANCE,
                snapshot,
                AdmissionReasonCode.WORKER_DRAINING,
                "worker is draining",
                current,
            )

        headroom_reason = self._headroom_reason(requirements=requirements, available=available)
        if headroom_reason is not None:
            return AdmissionDecision(
                action=AdmissionAction.QUEUE,
                pressure_state=snapshot.state if snapshot is not None else PressureState.UNKNOWN,
                reasons=(headroom_reason,),
                snapshot_age_seconds=self._age(snapshot, current),
            )

        if snapshot is None:
            return self._unknown(
                AdmissionReasonCode.REPORT_MISSING,
                "host pressure report is unavailable",
            )
        if not snapshot.trusted:
            return self._unknown(
                AdmissionReasonCode.REPORT_UNTRUSTED,
                "host pressure report is not trusted",
                snapshot=snapshot,
                now=current,
            )

        age = self._age(snapshot, current)
        assert age is not None
        if age > self.max_snapshot_age.total_seconds():
            return self._unknown(
                AdmissionReasonCode.REPORT_STALE,
                "host pressure report is stale",
                snapshot=snapshot,
                now=current,
            )

        if snapshot.state is PressureState.CRITICAL:
            return AdmissionDecision(
                action=AdmissionAction.DENY_TEMPORARILY,
                pressure_state=snapshot.state,
                reasons=(
                    AdmissionReason(
                        AdmissionReasonCode.PRESSURE_CRITICAL,
                        "host reports critical resource pressure",
                    ),
                ),
                snapshot_age_seconds=age,
            )

        if snapshot.state is PressureState.ELEVATED:
            if workload_class in self.queue_elevated_workload_classes:
                return AdmissionDecision(
                    action=AdmissionAction.QUEUE,
                    pressure_state=snapshot.state,
                    reasons=(
                        AdmissionReason(
                            AdmissionReasonCode.PRESSURE_ELEVATED,
                            "elevated pressure queues the configured workload class",
                        ),
                    ),
                    snapshot_age_seconds=age,
                )
            return AdmissionDecision(
                action=AdmissionAction.ADMIT,
                pressure_state=snapshot.state,
                reasons=(
                    AdmissionReason(
                        AdmissionReasonCode.PRESSURE_ELEVATED,
                        "elevated pressure is observable but policy still admits this workload",
                    ),
                ),
                snapshot_age_seconds=age,
            )

        if snapshot.state is PressureState.UNKNOWN:
            return self._unknown(
                AdmissionReasonCode.PRESSURE_UNKNOWN,
                "host pressure state is unknown",
                snapshot=snapshot,
                now=current,
            )

        return AdmissionDecision(
            action=AdmissionAction.ADMIT,
            pressure_state=PressureState.HEALTHY,
            snapshot_age_seconds=age,
        )

    def _unknown(
        self,
        code: AdmissionReasonCode,
        message: str,
        *,
        snapshot: HostPressureSnapshot | None = None,
        now: datetime | None = None,
    ) -> AdmissionDecision:
        current = now or utc_now()
        return AdmissionDecision(
            action=(
                AdmissionAction.DENY_TEMPORARILY
                if self.require_pressure_report
                else AdmissionAction.ADMIT
            ),
            pressure_state=PressureState.UNKNOWN,
            reasons=(AdmissionReason(code, message),),
            snapshot_age_seconds=self._age(snapshot, current),
        )

    def _headroom_reason(
        self,
        *,
        requirements: JobRequirements,
        available: ResourceSnapshot,
    ) -> AdmissionReason | None:
        remaining_cpu = available.cpu_cores_available - requirements.cpu_cores_min
        remaining_ram = available.ram_available_bytes - requirements.ram_min_bytes
        remaining_storage = available.storage_available_bytes - requirements.storage_min_bytes
        protected = self.protected_headroom
        if (
            remaining_cpu < protected.cpu_cores
            or remaining_ram < protected.ram_bytes
            or remaining_storage < protected.storage_bytes
        ):
            return AdmissionReason(
                AdmissionReasonCode.PROTECTED_HEADROOM,
                "job would consume deployment-protected recovery/control-plane headroom",
            )
        return None

    @staticmethod
    def _age(snapshot: HostPressureSnapshot | None, now: datetime) -> float | None:
        if snapshot is None:
            return None
        return max(0.0, (now - snapshot.observed_at).total_seconds())

    @staticmethod
    def _decision(
        action: AdmissionAction,
        snapshot: HostPressureSnapshot | None,
        code: AdmissionReasonCode,
        message: str,
        now: datetime,
    ) -> AdmissionDecision:
        return AdmissionDecision(
            action=action,
            pressure_state=snapshot.state if snapshot is not None else PressureState.UNKNOWN,
            reasons=(AdmissionReason(code, message),),
            snapshot_age_seconds=PressureAdmissionPolicy._age(snapshot, now),
        )


__all__ = [
    "AdmissionAction",
    "AdmissionDecision",
    "AdmissionReason",
    "AdmissionReasonCode",
    "HostPressureSnapshot",
    "InMemoryPressureSnapshotProvider",
    "PressureAdmissionPolicy",
    "PressureKind",
    "PressureSignal",
    "PressureSnapshotProvider",
    "PressureState",
    "ProtectedHeadroom",
]
