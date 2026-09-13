from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from ai_multi_agent_platform.distributed import (
    DistributedRegistry,
    Heartbeat,
    NodeRecord,
    RegistrationRequest,
    RegistryError,
    ResourceSnapshot,
    WorkerRecord,
)
from ai_multi_agent_platform.domain import new_id

BASE_TIME = datetime(2026, 9, 4, 4, 0, tzinfo=UTC)


def _node() -> NodeRecord:
    return NodeRecord(
        node_id=new_id("node"),
        display_name="registry-completion",
        resources=ResourceSnapshot(
            cpu_cores_total=4,
            cpu_cores_available=4,
            ram_total_bytes=8_000,
            ram_available_bytes=8_000,
            storage_total_bytes=20_000,
            storage_available_bytes=20_000,
        ),
    )


def _worker(node: NodeRecord, *, protocol_version: str = "1.0") -> WorkerRecord:
    return WorkerRecord(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        protocol_version=protocol_version,
    )


def test_registration_rejects_worker_record_protocol_mismatch_before_mutation() -> None:
    registry = DistributedRegistry()
    node = _node()
    incompatible = _worker(node, protocol_version="2.0")

    with pytest.raises(RegistryError, match="worker protocol version mismatch"):
        registry.register(
            RegistrationRequest(node=node, workers=(incompatible,)),
            now=BASE_TIME,
        )

    assert registry.list_nodes() == ()
    assert registry.list_workers() == ()


def test_heartbeat_rejects_worker_record_protocol_mismatch_without_refreshing_state() -> None:
    registry = DistributedRegistry()
    node = _node()
    worker = _worker(node)
    registered = registry.register(
        RegistrationRequest(node=node, workers=(worker,)),
        now=BASE_TIME,
    )
    incompatible = replace(worker, protocol_version="2.0")

    with pytest.raises(RegistryError, match="worker protocol version mismatch"):
        registry.heartbeat(
            Heartbeat(
                node_id=node.node_id,
                sequence=1,
                observed_at=BASE_TIME + timedelta(seconds=5),
                workers=(incompatible,),
            )
        )

    assert registry.get_node(node.node_id) == registered
    assert registry.get_worker(worker.worker_id).last_heartbeat_at == BASE_TIME


def test_worker_deregistration_removes_node_worker_reference() -> None:
    registry = DistributedRegistry()
    node = _node()
    worker_a = _worker(node)
    worker_b = _worker(node)
    registry.register(
        RegistrationRequest(node=node, workers=(worker_a, worker_b)),
        now=BASE_TIME,
    )

    registry.deregister_worker(worker_a.worker_id)

    assert registry.get_node(node.node_id).worker_refs == (worker_b.worker_id,)
    remaining = registry.list_workers()
    assert len(remaining) == 1
    assert remaining[0].worker_id == worker_b.worker_id
    assert remaining[0].node_id == node.node_id
    with pytest.raises(RegistryError, match="unknown worker"):
        registry.get_worker(worker_a.worker_id)
