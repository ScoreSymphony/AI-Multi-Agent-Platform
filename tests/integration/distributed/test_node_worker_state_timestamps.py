from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.distributed import (
    DistributedRegistry,
    DistributedRuntime,
    Heartbeat,
    JsonDistributedStateStore,
    NodeRecord,
    NodeStatus,
    RegistrationRequest,
    RegistryError,
    ResourceSnapshot,
    WorkerRecord,
    WorkerStatus,
)
from ai_multi_agent_platform.domain import new_id

T0 = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=1)
T2 = T0 + timedelta(minutes=2)
T3 = T0 + timedelta(minutes=3)
T4 = T0 + timedelta(minutes=4)


def _records() -> tuple[NodeRecord, WorkerRecord]:
    node = NodeRecord(
        node_id=new_id("node"),
        display_name="Issue 414 node",
        resources=ResourceSnapshot(
            cpu_cores_total=8,
            cpu_cores_available=8,
            ram_total_bytes=16_000,
            ram_available_bytes=16_000,
            storage_total_bytes=100_000,
            storage_available_bytes=100_000,
        ),
        supported_runtimes=("python",),
        capability_refs=("capability.code",),
    )
    worker = WorkerRecord(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        supported_executors=("reference",),
        capability_refs=("capability.code",),
        supported_runtimes=("python",),
        concurrency_limit=2,
        worker_version="414.1",
    )
    return node, worker


def _registered_registry() -> tuple[DistributedRegistry, NodeRecord, WorkerRecord]:
    registry = DistributedRegistry(heartbeat_timeout=timedelta(seconds=30))
    node, worker = _records()
    registry.register(RegistrationRequest(node=node, workers=(worker,)), now=T0)
    return registry, registry.get_node(node.node_id), registry.get_worker(worker.worker_id)


def test_registration_and_reregistration_set_canonical_state_timestamp() -> None:
    registry, node, worker = _registered_registry()
    for record in (node, worker):
        assert record.registered_at == T0
        assert record.last_heartbeat_at == T0
        assert record.updated_at == T0

    registry.register(RegistrationRequest(node=node, workers=(worker,)), now=T1)
    reregistered_node = registry.get_node(node.node_id)
    reregistered_worker = registry.get_worker(worker.worker_id)
    for record in (reregistered_node, reregistered_worker):
        assert record.registered_at == T1
        assert record.last_heartbeat_at == T1
        assert record.updated_at == T1


def test_admin_mutations_change_state_time_without_fabricating_heartbeat() -> None:
    registry, node, worker = _registered_registry()

    maintenance = registry.set_node_maintenance(node.node_id, maintenance=True, now=T1)
    assert maintenance.updated_at == T1
    assert maintenance.last_heartbeat_at == T0

    draining_node = registry.set_node_draining(node.node_id, draining=True, now=T2)
    assert draining_node.updated_at == T2
    assert draining_node.last_heartbeat_at == T0

    draining_worker = registry.set_worker_draining(worker.worker_id, draining=True, now=T3)
    assert draining_worker.updated_at == T3
    assert draining_worker.last_heartbeat_at == T0

    assert registry.set_node_draining(node.node_id, draining=True, now=T4).updated_at == T2
    assert registry.set_worker_draining(worker.worker_id, draining=True, now=T4).updated_at == T3


def test_pure_heartbeat_advances_liveness_only_and_state_change_advances_both() -> None:
    registry, node, worker = _registered_registry()

    registry.heartbeat(
        Heartbeat(
            node_id=node.node_id,
            observed_at=T1,
            sequence=1,
            workers=(worker,),
        )
    )
    refreshed_node = registry.get_node(node.node_id)
    refreshed_worker = registry.get_worker(worker.worker_id)
    assert refreshed_node.last_heartbeat_at == T1
    assert refreshed_worker.last_heartbeat_at == T1
    assert refreshed_node.updated_at == T0
    assert refreshed_worker.updated_at == T0

    changed_resources = replace(node.resources, cpu_cores_available=7)
    changed_worker = replace(worker, active_jobs=1)
    registry.heartbeat(
        Heartbeat(
            node_id=node.node_id,
            observed_at=T2,
            sequence=2,
            resources=changed_resources,
            workers=(changed_worker,),
        )
    )
    changed_node = registry.get_node(node.node_id)
    changed_worker_record = registry.get_worker(worker.worker_id)
    assert changed_node.last_heartbeat_at == T2
    assert changed_worker_record.last_heartbeat_at == T2
    assert changed_node.updated_at == T2
    assert changed_worker_record.updated_at == T2


def test_heartbeat_expiry_changes_state_time_without_changing_last_heartbeat() -> None:
    registry, node, worker = _registered_registry()
    expiry = T0 + timedelta(seconds=31)

    assert registry.expire_heartbeats(now=expiry) == (node.node_id,)
    expired_node = registry.get_node(node.node_id)
    expired_worker = registry.get_worker(worker.worker_id)
    assert expired_node.status is NodeStatus.OFFLINE
    assert expired_worker.status is WorkerStatus.OFFLINE
    assert expired_node.last_heartbeat_at == T0
    assert expired_worker.last_heartbeat_at == T0
    assert expired_node.updated_at == expiry
    assert expired_worker.updated_at == expiry


def test_restore_health_normalization_is_a_state_change_but_not_a_heartbeat() -> None:
    registry, node, worker = _registered_registry()
    snapshot = registry.snapshot()

    restored = DistributedRegistry()
    restored.restore_snapshot(snapshot, now=T1)
    restored_node = restored.get_node(node.node_id)
    restored_worker = restored.get_worker(worker.worker_id)
    assert restored_node.status is NodeStatus.OFFLINE
    assert restored_worker.status is WorkerStatus.OFFLINE
    assert restored_node.last_heartbeat_at == T0
    assert restored_worker.last_heartbeat_at == T0
    assert restored_node.updated_at == T1
    assert restored_worker.updated_at == T1

    already_offline = DistributedRegistry()
    already_offline.restore_snapshot(restored.snapshot(), now=T2)
    assert already_offline.get_node(node.node_id).updated_at == T1
    assert already_offline.get_worker(worker.worker_id).updated_at == T1


def test_node_worker_and_heartbeat_timestamps_require_timezone_awareness() -> None:
    node, worker = _records()
    naive = datetime(2026, 9, 6, 8, 0)

    with pytest.raises(ValueError, match="node updated_at must be timezone-aware"):
        replace(node, updated_at=naive)
    with pytest.raises(ValueError, match="worker updated_at must be timezone-aware"):
        replace(worker, updated_at=naive)
    with pytest.raises(ValueError, match="heartbeat observed_at must be timezone-aware"):
        Heartbeat(node_id=node.node_id, observed_at=naive)


def test_reregistration_never_moves_state_timestamp_backwards() -> None:
    registry, node, worker = _registered_registry()
    registry.set_node_draining(node.node_id, draining=True, now=T3)
    registry.set_worker_draining(worker.worker_id, draining=True, now=T3)

    current_node = registry.get_node(node.node_id)
    current_worker = registry.get_worker(worker.worker_id)
    registry.register(
        RegistrationRequest(node=current_node, workers=(current_worker,)),
        now=T2,
    )

    reregistered_node = registry.get_node(node.node_id)
    reregistered_worker = registry.get_worker(worker.worker_id)
    assert reregistered_node.registered_at == T2
    assert reregistered_node.last_heartbeat_at == T2
    assert reregistered_node.updated_at == T3
    assert reregistered_worker.registered_at == T2
    assert reregistered_worker.last_heartbeat_at == T2
    assert reregistered_worker.updated_at == T3


@pytest.mark.parametrize("schema_version", ["1", "2"])
def test_legacy_snapshot_without_updated_at_derives_conservative_state_time(
    tmp_path: Path,
    schema_version: str,
) -> None:
    state_path = tmp_path / f"distributed-v{schema_version}-without-updated-at.json"
    registry, node, worker = _registered_registry()
    registry.heartbeat(
        Heartbeat(
            node_id=node.node_id,
            observed_at=T1,
            sequence=1,
            workers=(worker,),
        )
    )
    expiry = T1 + timedelta(seconds=31)
    registry.expire_heartbeats(now=expiry)

    store = JsonDistributedStateStore(state_path)
    store.save(registry, DistributedRuntime(registry))
    document = json.loads(state_path.read_text(encoding="utf-8"))
    document["schema_version"] = schema_version
    for record in document["registry"]["nodes"]:
        record.pop("updated_at", None)
    for record in document["registry"]["workers"]:
        record.pop("updated_at", None)
    if schema_version == "1":
        for record in document["dispatch_records"]:
            record.pop("result", None)
    state_path.write_text(json.dumps(document), encoding="utf-8")

    restored_registry = DistributedRegistry()
    restored_runtime = DistributedRuntime(restored_registry)
    assert store.restore(restored_registry, restored_runtime) is True

    restored_node = restored_registry.get_node(node.node_id)
    restored_worker = restored_registry.get_worker(worker.worker_id)
    assert restored_node.status is NodeStatus.OFFLINE
    assert restored_worker.status is WorkerStatus.OFFLINE
    assert restored_node.last_heartbeat_at == T1
    assert restored_worker.last_heartbeat_at == T1
    assert restored_node.updated_at == T1
    assert restored_worker.updated_at == T1


def test_worker_deregistration_updates_parent_node_state_time_only() -> None:
    registry, node, worker = _registered_registry()

    registry.deregister_worker(worker.worker_id, now=T1)
    parent = registry.get_node(node.node_id)
    assert worker.worker_id not in parent.worker_refs
    assert parent.last_heartbeat_at == T0
    assert parent.updated_at == T1
    with pytest.raises(RegistryError, match="unknown worker"):
        registry.get_worker(worker.worker_id)


def test_node_deregistration_removes_node_and_owned_workers() -> None:
    registry, node, worker = _registered_registry()

    registry.deregister_node(node.node_id, now=T1)
    with pytest.raises(RegistryError, match="unknown node"):
        registry.get_node(node.node_id)
    with pytest.raises(RegistryError, match="unknown worker"):
        registry.get_worker(worker.worker_id)
