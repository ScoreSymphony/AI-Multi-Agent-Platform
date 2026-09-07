"""Portable authenticated remote pressure-report metadata for issue #500."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

from ai_multi_agent_platform.contracts.types import AdapterMetadata, JsonValue

from .models import WorkerRecord
from .pressure import HostPressureSnapshot, PressureKind, PressureSignal, PressureState
from .registry import DistributedRegistry

PRESSURE_REPORT_NAMESPACE = "platform.host-pressure-report.v1"
PRESSURE_PROVENANCE_NAMESPACE = "platform.host-pressure-provenance.v1"
_MAX_FUTURE_REPORT_SKEW = timedelta(seconds=5)


def pressure_report_metadata(snapshot: HostPressureSnapshot) -> AdapterMetadata:
    """Encode only portable Worker-reported pressure evidence for the authenticated heartbeat."""

    signals: list[JsonValue] = []
    for signal in snapshot.signals:
        item: dict[str, JsonValue] = {
            "kind": signal.kind.value,
            "state": signal.state.value,
            "value": signal.value,
            "unit": signal.unit,
        }
        signals.append(item)
    return AdapterMetadata(
        namespace=PRESSURE_REPORT_NAMESPACE,
        values={
            "state": snapshot.state.value,
            "observed_at": snapshot.observed_at.astimezone(UTC).isoformat(),
            "signals": signals,
        },
    )


def attach_pressure_report(worker: WorkerRecord, snapshot: HostPressureSnapshot) -> WorkerRecord:
    """Replace the reporter's previous portable report without forwarding provenance claims."""

    metadata = tuple(
        item
        for item in worker.adapter_metadata
        if item.namespace not in {PRESSURE_REPORT_NAMESPACE, PRESSURE_PROVENANCE_NAMESPACE}
    )
    return replace(
        worker,
        adapter_metadata=(*metadata, pressure_report_metadata(snapshot)),
    )


def authenticate_pressure_report(
    worker: WorkerRecord,
    *,
    node_id: str,
    reporter_worker_id: str,
    accepted_at: datetime,
) -> WorkerRecord:
    """Replace any remote provenance claim with service-owned authenticated provenance."""

    metadata = tuple(
        item for item in worker.adapter_metadata if item.namespace != PRESSURE_PROVENANCE_NAMESPACE
    )
    if worker.worker_id != reporter_worker_id:
        return replace(
            worker,
            adapter_metadata=tuple(
                item for item in metadata if item.namespace != PRESSURE_REPORT_NAMESPACE
            ),
        )
    if _metadata(metadata, PRESSURE_REPORT_NAMESPACE) is None:
        return replace(worker, adapter_metadata=metadata)
    provenance = AdapterMetadata(
        namespace=PRESSURE_PROVENANCE_NAMESPACE,
        values={
            "node_id": node_id,
            "reporter_worker_id": reporter_worker_id,
            "accepted_at": accepted_at.astimezone(UTC).isoformat(),
            "authentication": "worker_protocol",
        },
    )
    return replace(worker, adapter_metadata=(*metadata, provenance))


class RegistryPressureSnapshotProvider:
    """Resolve authenticated remote pressure evidence from the canonical Worker registry."""

    def __init__(self, registry: DistributedRegistry) -> None:
        self.registry = registry

    def snapshot_for_node(self, node_id: str) -> HostPressureSnapshot | None:
        candidates: list[tuple[datetime, WorkerRecord, AdapterMetadata]] = []
        for worker in self.registry.list_workers():
            if worker.node_id != node_id:
                continue
            report = _metadata(worker.adapter_metadata, PRESSURE_REPORT_NAMESPACE)
            provenance = _metadata(worker.adapter_metadata, PRESSURE_PROVENANCE_NAMESPACE)
            if report is None or provenance is None:
                continue
            accepted_at = _authenticated_at(
                provenance,
                node_id=node_id,
                reporter_worker_id=worker.worker_id,
            )
            if accepted_at is not None:
                candidates.append((accepted_at, worker, report))
        if not candidates:
            return None
        accepted_at, worker, report = max(candidates, key=lambda item: item[0])
        return _decode_report(
            report,
            worker_id=worker.worker_id,
            accepted_at=accepted_at,
        )


def _metadata(
    metadata: tuple[AdapterMetadata, ...],
    namespace: str,
) -> AdapterMetadata | None:
    return next((item for item in metadata if item.namespace == namespace), None)


def _authenticated_at(
    provenance: AdapterMetadata,
    *,
    node_id: str,
    reporter_worker_id: str,
) -> datetime | None:
    values = provenance.values
    if values.get("node_id") != node_id:
        return None
    if values.get("reporter_worker_id") != reporter_worker_id:
        return None
    if values.get("authentication") != "worker_protocol":
        return None
    return _datetime(values.get("accepted_at"))


def _decode_report(
    report: AdapterMetadata,
    *,
    worker_id: str,
    accepted_at: datetime,
) -> HostPressureSnapshot | None:
    values = report.values
    state_raw = values.get("state")
    observed_at = _datetime(values.get("observed_at"))
    signals_raw = values.get("signals")
    if not isinstance(state_raw, str) or observed_at is None or not isinstance(signals_raw, list):
        return None
    if observed_at > accepted_at + _MAX_FUTURE_REPORT_SKEW:
        return None
    try:
        state = PressureState(state_raw)
        signals = tuple(_decode_signal(item) for item in signals_raw)
        if any(signal is None for signal in signals):
            return None
        return HostPressureSnapshot(
            state=state,
            observed_at=observed_at,
            signals=cast(tuple[PressureSignal, ...], signals),
            source_ref=f"worker:{worker_id}",
            trusted=True,
        )
    except ValueError:
        return None


def _decode_signal(value: JsonValue) -> PressureSignal | None:
    if not isinstance(value, dict):
        return None
    kind_raw = value.get("kind")
    state_raw = value.get("state")
    signal_value = value.get("value")
    unit_raw = value.get("unit")
    if not isinstance(kind_raw, str) or not isinstance(state_raw, str):
        return None
    if signal_value is not None and not isinstance(signal_value, int | float):
        return None
    if unit_raw is not None and not isinstance(unit_raw, str):
        return None
    try:
        return PressureSignal(
            kind=PressureKind(kind_raw),
            state=PressureState(state_raw),
            value=None if signal_value is None else float(signal_value),
            unit=unit_raw,
        )
    except ValueError:
        return None


def _datetime(value: JsonValue | None) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


__all__ = [
    "PRESSURE_PROVENANCE_NAMESPACE",
    "PRESSURE_REPORT_NAMESPACE",
    "RegistryPressureSnapshotProvider",
    "attach_pressure_report",
    "authenticate_pressure_report",
    "pressure_report_metadata",
]
