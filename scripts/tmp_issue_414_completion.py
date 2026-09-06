from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"expected snippet not found in {path}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def append_once(path: str, marker: str, addition: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if marker in text:
        return
    target.write_text(text.rstrip() + "\n\n" + addition.strip() + "\n", encoding="utf-8")


replace_once(
    "src/ai_multi_agent_platform/distributed/registry.py",
    """        self._assert_worker_protocol_versions(request.workers)\n        node = replace(\n            request.node,\n            registered_at=timestamp,\n            last_heartbeat_at=timestamp,\n            updated_at=timestamp,\n            status=NodeStatus.MAINTENANCE if request.node.maintenance else NodeStatus.ONLINE,\n            worker_refs=tuple(sorted(worker.worker_id for worker in request.workers)),\n        )\n""",
    """        self._assert_worker_protocol_versions(request.workers)\n        existing_node = self._nodes.get(request.node.node_id)\n        node_updated_at = (\n            timestamp\n            if existing_node is None\n            else _state_timestamp(existing_node.updated_at, timestamp)\n        )\n        node = replace(\n            request.node,\n            registered_at=timestamp,\n            last_heartbeat_at=timestamp,\n            updated_at=node_updated_at,\n            status=NodeStatus.MAINTENANCE if request.node.maintenance else NodeStatus.ONLINE,\n            worker_refs=tuple(sorted(worker.worker_id for worker in request.workers)),\n        )\n""",
)

replace_once(
    "src/ai_multi_agent_platform/distributed/registry.py",
    """        for worker in request.workers:\n            self._workers[worker.worker_id] = replace(\n                worker,\n                registered_at=timestamp,\n                last_heartbeat_at=timestamp,\n                updated_at=timestamp,\n            )\n""",
    """        for worker in request.workers:\n            existing_worker = self._workers.get(worker.worker_id)\n            worker_updated_at = (\n                timestamp\n                if existing_worker is None\n                else _state_timestamp(existing_worker.updated_at, timestamp)\n            )\n            self._workers[worker.worker_id] = replace(\n                worker,\n                registered_at=timestamp,\n                last_heartbeat_at=timestamp,\n                updated_at=worker_updated_at,\n            )\n""",
)

replace_once(
    "tests/test_issue_414_node_worker_state_timestamps.py",
    """from dataclasses import replace\nfrom datetime import UTC, datetime, timedelta\n\nimport pytest\n\nfrom ai_multi_agent_platform.distributed import (\n    DistributedRegistry,\n    Heartbeat,\n    NodeRecord,\n    NodeStatus,\n    RegistrationRequest,\n    ResourceSnapshot,\n    WorkerRecord,\n    WorkerStatus,\n)\n""",
    """import json\nfrom dataclasses import replace\nfrom datetime import UTC, datetime, timedelta\nfrom pathlib import Path\n\nimport pytest\n\nfrom ai_multi_agent_platform.distributed import (\n    DistributedRegistry,\n    DistributedRuntime,\n    Heartbeat,\n    JsonDistributedStateStore,\n    NodeRecord,\n    NodeStatus,\n    RegistrationRequest,\n    RegistryError,\n    ResourceSnapshot,\n    WorkerRecord,\n    WorkerStatus,\n)\n""",
)

append_once(
    "tests/test_issue_414_node_worker_state_timestamps.py",
    "test_reregistration_never_moves_state_timestamp_backwards",
    r'''
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
''',
)

append_once(
    "tests/test_issue_288_node_worker_search.py",
    "test_search_updated_at_sorting_and_worker_filters_use_canonical_state_time",
    r'''
def test_search_updated_at_sorting_and_worker_filters_use_canonical_state_time() -> None:
    async def scenario() -> None:
        _, http, runtime, node, worker = _stack()
        node_mutation_at = NOW + timedelta(minutes=5)
        worker_mutation_at = NOW + timedelta(minutes=10)
        runtime.registry.set_node_draining(
            node.node_id,
            draining=True,
            now=node_mutation_at,
        )
        runtime.registry.set_worker_draining(
            worker.worker_id,
            draining=True,
            now=worker_mutation_at,
        )

        worker_exact = await _search(http, {"id": worker.worker_id, "type": "worker"})
        assert _items(worker_exact)[0]["updated_at"] == worker_mutation_at.isoformat()

        worker_after = await _search(
            http,
            {"type": "worker", "updated_after": worker_mutation_at.isoformat()},
        )
        assert {item["resource_id"] for item in _items(worker_after)} == {worker.worker_id}

        ascending = await _search(
            http,
            {"type": "node,worker", "sort": "updated_at", "direction": "asc"},
        )
        assert [item["resource_id"] for item in _items(ascending)] == [
            node.node_id,
            worker.worker_id,
        ]

        descending = await _search(
            http,
            {"type": "node,worker", "sort": "updated_at", "direction": "desc"},
        )
        assert [item["resource_id"] for item in _items(descending)] == [
            worker.worker_id,
            node.node_id,
        ]

    asyncio.run(scenario())
''',
)

replace_once(
    "docs/adr/0010-node-worker-state-change-timestamps.md",
    """State timestamps advance monotonically within the registry with `max(previous_updated_at, event_time)`, so delayed or regressed event clocks cannot move modification history backwards. All canonical runtime timestamps must be timezone-aware.\n\nWorker heartbeat payload timestamps are not trusted as Control-Plane modification truth.""",
    """State timestamps advance monotonically within the registry with `max(previous_updated_at, event_time)`, including re-registration, so delayed or regressed event clocks cannot move modification history backwards. Registration still establishes fresh `registered_at` and liveness evidence at the accepted registration time; only canonical state-change history is protected from regression. All canonical runtime timestamps must be timezone-aware.\n\nWorker heartbeat payload timestamps are not trusted as Control-Plane modification truth.""",
)

replace_once(
    "docs/adr/0010-node-worker-state-change-timestamps.md",
    """On Control-Plane restart, persisted health is normalized to offline because persisted health is not fresh liveness evidence. When that normalization changes visible state, restore time advances `updated_at` while preserving `last_heartbeat_at`. Already-offline restored records retain their prior `updated_at`.\n\nDistributed-state schema v3 persists the new field.""",
    """On Control-Plane restart, persisted health is normalized to offline because persisted health is not fresh liveness evidence. When that normalization changes visible state, restore time advances `updated_at` while preserving `last_heartbeat_at`. Already-offline restored records retain their prior `updated_at`.\n\nDeregistration removes the deregistered resource from canonical runtime state, so that removed Node or Worker no longer has an externally visible `updated_at`. Deregistering a Worker also changes its surviving parent Node's canonical `worker_refs`; that parent Node therefore advances `updated_at` monotonically without changing `last_heartbeat_at`. Deregistering a Node removes the Node and its owned Workers rather than fabricating terminal timestamps for resources that no longer exist.\n\nDistributed-state schema v3 persists the new field.""",
)

replace_once(
    "docs/DISTRIBUTED_RUNTIME.md",
    """- no-op administrative mutations preserve the previous state-change timestamp;\n- state-change time is monotonic within the registry and all runtime timestamps are timezone-aware;\n- restart health normalization to offline is a canonical state change when the persisted record was not already offline, but it is not a heartbeat.\n""",
    """- no-op administrative mutations preserve the previous state-change timestamp;\n- state-change time is monotonic within the registry, including re-registration, and all runtime timestamps are timezone-aware;\n- deregistering a Worker advances the surviving parent Node's `updated_at` when `worker_refs` changes, without changing heartbeat evidence; deregistered resources themselves leave canonical state;\n- restart health normalization to offline is a canonical state change when the persisted record was not already offline, but it is not a heartbeat.\n""",
)
