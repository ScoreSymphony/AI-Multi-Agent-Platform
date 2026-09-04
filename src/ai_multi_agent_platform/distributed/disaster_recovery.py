"""Disaster-recovery helpers for distributed runtime state."""

from __future__ import annotations

from dataclasses import replace

from .models import NodeStatus, WorkerStatus
from .registry import RegistrySnapshot


def prepare_registry_disaster_recovery(snapshot: RegistrySnapshot) -> RegistrySnapshot:
    """Return a registry snapshot safe to restore after control-plane or host loss.

    Stable node/worker identities and operator configuration are preserved, but liveness evidence,
    worker active-job counters, heartbeat sequences and capacity reservations are deliberately
    discarded. Workers must re-register before they become schedulable again.
    """

    return RegistrySnapshot(
        nodes=tuple(replace(node, status=NodeStatus.OFFLINE) for node in snapshot.nodes),
        workers=tuple(
            replace(worker, status=WorkerStatus.OFFLINE, active_jobs=0)
            for worker in snapshot.workers
        ),
        heartbeat_sequences=tuple((node.node_id, 0) for node in snapshot.nodes),
        reservations=(),
    )
