from __future__ import annotations

from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected exactly one match in {path}, found {count}: {old[:80]!r}")
    write(path, text.replace(old, new, 1))


def replace_between(path: str, start: str, end: str, replacement: str) -> None:
    text = read(path)
    start_index = text.index(start)
    end_index = text.index(end, start_index)
    write(path, text[:start_index] + replacement.rstrip() + "\n\n" + text[end_index:])


def patch_models() -> None:
    path = "src/ai_multi_agent_platform/distributed/models.py"
    replace_once(
        path,
        "def utc_now() -> datetime:\n    return datetime.now(UTC)\n\n\nclass NodeStatus",
        "def utc_now() -> datetime:\n    return datetime.now(UTC)\n\n\ndef _require_aware(value: datetime, field_name: str) -> None:\n    if value.tzinfo is None or value.utcoffset() is None:\n        raise ValueError(f\"{field_name} must be timezone-aware\")\n\n\nclass NodeStatus",
    )
    replace_once(
        path,
        "    registered_at: datetime = field(default_factory=utc_now)\n    last_heartbeat_at: datetime = field(default_factory=utc_now)\n    labels: tuple[str, ...] = ()",
        "    registered_at: datetime = field(default_factory=utc_now)\n    last_heartbeat_at: datetime = field(default_factory=utc_now)\n    updated_at: datetime = field(default_factory=utc_now)\n    labels: tuple[str, ...] = ()",
    )
    replace_once(
        path,
        "        if not self.trust_level.strip():\n            raise ValueError(\"node trust_level must not be blank\")\n\n\n@dataclass(frozen=True, slots=True)\nclass WorkerRecord",
        "        if not self.trust_level.strip():\n            raise ValueError(\"node trust_level must not be blank\")\n        _require_aware(self.registered_at, \"node registered_at\")\n        _require_aware(self.last_heartbeat_at, \"node last_heartbeat_at\")\n        _require_aware(self.updated_at, \"node updated_at\")\n\n\n@dataclass(frozen=True, slots=True)\nclass WorkerRecord",
    )
    replace_once(
        path,
        "    registered_at: datetime = field(default_factory=utc_now)\n    last_heartbeat_at: datetime = field(default_factory=utc_now)\n    draining: bool = False",
        "    registered_at: datetime = field(default_factory=utc_now)\n    last_heartbeat_at: datetime = field(default_factory=utc_now)\n    updated_at: datetime = field(default_factory=utc_now)\n    draining: bool = False",
    )
    replace_once(
        path,
        "        if not self.protocol_version.strip():\n            raise ValueError(\"worker protocol_version must not be blank\")\n\n\n@dataclass(frozen=True, slots=True)\nclass JobRequirements",
        "        if not self.protocol_version.strip():\n            raise ValueError(\"worker protocol_version must not be blank\")\n        _require_aware(self.registered_at, \"worker registered_at\")\n        _require_aware(self.last_heartbeat_at, \"worker last_heartbeat_at\")\n        _require_aware(self.updated_at, \"worker updated_at\")\n\n\n@dataclass(frozen=True, slots=True)\nclass JobRequirements",
    )
    replace_once(
        path,
        "        if self.sequence < 1:\n            raise ValueError(\"heartbeat sequence must be >= 1\")\n        if any(worker.node_id != self.node_id for worker in self.workers):",
        "        if self.sequence < 1:\n            raise ValueError(\"heartbeat sequence must be >= 1\")\n        _require_aware(self.observed_at, \"heartbeat observed_at\")\n        if any(worker.node_id != self.node_id for worker in self.workers):",
    )


def patch_registry() -> None:
    path = "src/ai_multi_agent_platform/distributed/registry.py"
    replace_once(
        path,
        'class RegistryError(RuntimeError):\n    """Raised when registration, liveness or reservation invariants are violated."""\n\n\n@dataclass',
        'class RegistryError(RuntimeError):\n    """Raised when registration, liveness or reservation invariants are violated."""\n\n\ndef _state_timestamp(previous: datetime, event_time: datetime) -> datetime:\n    """Advance canonical state time monotonically without changing heartbeat evidence."""\n\n    return max(previous, event_time)\n\n\ndef _worker_state_changed(previous: WorkerRecord, current: WorkerRecord) -> bool:\n    """Compare Worker state while excluding liveness and modification timestamps."""\n\n    normalized = replace(\n        previous,\n        registered_at=current.registered_at,\n        last_heartbeat_at=current.last_heartbeat_at,\n        updated_at=current.updated_at,\n    )\n    return normalized != current\n\n\n@dataclass',
    )
    replace_between(
        path,
        "    def restore_snapshot(",
        "    def register(",
        '''    def restore_snapshot(
        self,
        snapshot: RegistrySnapshot,
        *,
        now: datetime | None = None,
    ) -> None:
        """Restore a clean registry conservatively after control-side restart."""

        if self._nodes or self._workers or self._reservations or self._heartbeat_sequence:
            raise RegistryError("registry restore requires an empty registry")

        nodes = {node.node_id: node for node in snapshot.nodes}
        if len(nodes) != len(snapshot.nodes):
            raise RegistryError("registry snapshot contains duplicate node IDs")
        workers = {worker.worker_id: worker for worker in snapshot.workers}
        if len(workers) != len(snapshot.workers):
            raise RegistryError("registry snapshot contains duplicate worker IDs")
        if any(worker.node_id not in nodes for worker in workers.values()):
            raise RegistryError("registry snapshot contains worker for unknown node")

        sequence_map = dict(snapshot.heartbeat_sequences)
        if len(sequence_map) != len(snapshot.heartbeat_sequences):
            raise RegistryError("registry snapshot contains duplicate heartbeat sequence entries")
        if set(sequence_map) - set(nodes):
            raise RegistryError("registry snapshot contains heartbeat for unknown node")
        if any(sequence < 0 for sequence in sequence_map.values()):
            raise RegistryError("registry snapshot contains negative heartbeat sequence")

        reservations = {item.reservation_id: item for item in snapshot.reservations}
        if len(reservations) != len(snapshot.reservations):
            raise RegistryError("registry snapshot contains duplicate reservation IDs")
        job_reservations: dict[str, str] = {}
        for reservation in reservations.values():
            if reservation.status not in {ReservationStatus.RESERVED, ReservationStatus.ACTIVE}:
                raise RegistryError(
                    "registry snapshot must contain only capacity-claiming reservations"
                )
            worker = workers.get(reservation.worker_id)
            if worker is None or reservation.node_id not in nodes:
                raise RegistryError(
                    "registry snapshot reservation references unknown runtime identity"
                )
            if worker.node_id != reservation.node_id:
                raise RegistryError("registry snapshot reservation node/worker mismatch")
            if reservation.worker_job_id in job_reservations:
                raise RegistryError("worker job has multiple active reservations in snapshot")
            job_reservations[reservation.worker_job_id] = reservation.reservation_id

        timestamp = now or utc_now()

        def restored_node(node: NodeRecord) -> NodeRecord:
            if node.status is NodeStatus.OFFLINE:
                return node
            return replace(
                node,
                status=NodeStatus.OFFLINE,
                updated_at=_state_timestamp(node.updated_at, timestamp),
            )

        def restored_worker(worker: WorkerRecord) -> WorkerRecord:
            if worker.status is WorkerStatus.OFFLINE:
                return worker
            return replace(
                worker,
                status=WorkerStatus.OFFLINE,
                updated_at=_state_timestamp(worker.updated_at, timestamp),
            )

        # Persisted health is not fresh liveness evidence after a process restart.
        self._nodes = {node_id: restored_node(node) for node_id, node in nodes.items()}
        self._workers = {
            worker_id: restored_worker(worker) for worker_id, worker in workers.items()
        }
        self._heartbeat_sequence = {node_id: sequence_map.get(node_id, 0) for node_id in nodes}
        self._reservations = reservations
        self._job_reservation = job_reservations''',
    )
    replace_between(
        path,
        "    def register(",
        "    def heartbeat(",
        '''    def register(
        self,
        request: RegistrationRequest,
        *,
        now: datetime | None = None,
    ) -> NodeRecord:
        """Enroll or re-register a node and its workers using stable canonical IDs."""

        timestamp = now or utc_now()
        if request.protocol_version != WORKER_PROTOCOL_VERSION:
            raise RegistryError(
                "unsupported worker protocol version: "
                f"{request.protocol_version!r} != {WORKER_PROTOCOL_VERSION!r}"
            )
        self._assert_worker_protocol_versions(request.workers)
        node = replace(
            request.node,
            registered_at=timestamp,
            last_heartbeat_at=timestamp,
            updated_at=timestamp,
            status=NodeStatus.MAINTENANCE if request.node.maintenance else NodeStatus.ONLINE,
            worker_refs=tuple(sorted(worker.worker_id for worker in request.workers)),
        )
        self._nodes[node.node_id] = node
        self._heartbeat_sequence[node.node_id] = 0

        known_for_node = {
            worker_id
            for worker_id, worker in self._workers.items()
            if worker.node_id == node.node_id
        }
        incoming = {worker.worker_id for worker in request.workers}
        for stale_worker_id in known_for_node - incoming:
            stale = self._workers[stale_worker_id]
            if stale.status is WorkerStatus.OFFLINE and stale.draining:
                continue
            self._workers[stale_worker_id] = replace(
                stale,
                status=WorkerStatus.OFFLINE,
                draining=True,
                updated_at=_state_timestamp(stale.updated_at, timestamp),
            )
        for worker in request.workers:
            self._workers[worker.worker_id] = replace(
                worker,
                registered_at=timestamp,
                last_heartbeat_at=timestamp,
                updated_at=timestamp,
            )
        return node''',
    )
    replace_between(
        path,
        "    def heartbeat(",
        "    def list_nodes(",
        '''    def heartbeat(self, heartbeat: Heartbeat) -> NodeRecord:
        """Apply an idempotent monotonic heartbeat and refresh worker state."""

        if heartbeat.protocol_version != WORKER_PROTOCOL_VERSION:
            raise RegistryError("heartbeat protocol version mismatch")
        self._assert_worker_protocol_versions(heartbeat.workers)
        try:
            node = self._nodes[heartbeat.node_id]
        except KeyError as exc:
            raise RegistryError(f"unknown node: {heartbeat.node_id}") from exc

        previous_sequence = self._heartbeat_sequence.get(heartbeat.node_id, 0)
        if heartbeat.sequence < previous_sequence:
            raise RegistryError("stale heartbeat sequence")
        if heartbeat.sequence == previous_sequence and previous_sequence != 0:
            return node

        status = heartbeat.node_status or NodeStatus.ONLINE
        if node.maintenance:
            status = NodeStatus.MAINTENANCE
        resources = heartbeat.resources or node.resources
        node_state_changed = status != node.status or resources != node.resources
        updated = replace(
            node,
            last_heartbeat_at=heartbeat.observed_at,
            updated_at=(
                _state_timestamp(node.updated_at, heartbeat.observed_at)
                if node_state_changed
                else node.updated_at
            ),
            resources=resources,
            status=status,
        )
        self._nodes[node.node_id] = updated
        self._heartbeat_sequence[node.node_id] = heartbeat.sequence
        for worker in heartbeat.workers:
            existing = self._workers.get(worker.worker_id)
            if existing is not None and existing.node_id != node.node_id:
                raise RegistryError("worker cannot move between nodes during heartbeat")
            if existing is None:
                refreshed = replace(
                    worker,
                    registered_at=heartbeat.observed_at,
                    last_heartbeat_at=heartbeat.observed_at,
                    updated_at=heartbeat.observed_at,
                )
            else:
                refreshed = replace(
                    worker,
                    registered_at=existing.registered_at,
                    last_heartbeat_at=heartbeat.observed_at,
                    updated_at=existing.updated_at,
                )
                if _worker_state_changed(existing, refreshed):
                    refreshed = replace(
                        refreshed,
                        updated_at=_state_timestamp(existing.updated_at, heartbeat.observed_at),
                    )
            self._workers[worker.worker_id] = refreshed
            self._renew_active_reservations_for_worker(
                worker.worker_id,
                now=heartbeat.observed_at,
            )
        return updated''',
    )
    replace_between(
        path,
        "    def set_node_draining(",
        "    def active_reservations(",
        '''    def set_node_draining(
        self,
        node_id: str,
        *,
        draining: bool,
        now: datetime | None = None,
    ) -> NodeRecord:
        node = self.get_node(node_id)
        if node.draining is draining:
            return node
        timestamp = now or utc_now()
        updated = replace(
            node,
            draining=draining,
            updated_at=_state_timestamp(node.updated_at, timestamp),
        )
        self._nodes[node_id] = updated
        return updated

    def set_node_maintenance(
        self,
        node_id: str,
        *,
        maintenance: bool,
        now: datetime | None = None,
    ) -> NodeRecord:
        node = self.get_node(node_id)
        target_status = NodeStatus.MAINTENANCE if maintenance else NodeStatus.ONLINE
        if node.maintenance is maintenance and node.status is target_status:
            return node
        timestamp = now or utc_now()
        updated = replace(
            node,
            maintenance=maintenance,
            status=target_status,
            updated_at=_state_timestamp(node.updated_at, timestamp),
        )
        self._nodes[node_id] = updated
        return updated

    def set_worker_draining(
        self,
        worker_id: str,
        *,
        draining: bool,
        now: datetime | None = None,
    ) -> WorkerRecord:
        worker = self.get_worker(worker_id)
        if worker.draining is draining:
            return worker
        timestamp = now or utc_now()
        updated = replace(
            worker,
            draining=draining,
            updated_at=_state_timestamp(worker.updated_at, timestamp),
        )
        self._workers[worker_id] = updated
        return updated

    def deregister_worker(self, worker_id: str, *, now: datetime | None = None) -> None:
        timestamp = now or utc_now()
        worker = self.get_worker(worker_id)
        for reservation in tuple(self.active_reservations(worker_id=worker_id)):
            self.release_reservation(reservation.reservation_id)
        self._workers.pop(worker_id)
        node = self._nodes.get(worker.node_id)
        if node is not None:
            worker_refs = tuple(ref for ref in node.worker_refs if ref != worker_id)
            if worker_refs != node.worker_refs:
                self._nodes[worker.node_id] = replace(
                    node,
                    worker_refs=worker_refs,
                    updated_at=_state_timestamp(node.updated_at, timestamp),
                )

    def deregister_node(self, node_id: str, *, now: datetime | None = None) -> None:
        timestamp = now or utc_now()
        self.get_node(node_id)
        for worker in tuple(self.list_workers()):
            if worker.node_id == node_id:
                self.deregister_worker(worker.worker_id, now=timestamp)
        self._nodes.pop(node_id)
        self._heartbeat_sequence.pop(node_id, None)

    def expire_heartbeats(self, *, now: datetime | None = None) -> tuple[str, ...]:
        """Expire node and worker liveness independently after missed heartbeats."""

        timestamp = now or utc_now()
        expired_nodes: list[str] = []
        for node_id, node in tuple(self._nodes.items()):
            if timestamp - node.last_heartbeat_at <= self.heartbeat_timeout:
                continue
            if node.status is not NodeStatus.OFFLINE:
                self._nodes[node_id] = replace(
                    node,
                    status=NodeStatus.OFFLINE,
                    updated_at=_state_timestamp(node.updated_at, timestamp),
                )
                expired_nodes.append(node_id)

        for worker_id, worker in tuple(self._workers.items()):
            worker_node = self._nodes.get(worker.node_id)
            worker_stale = timestamp - worker.last_heartbeat_at > self.heartbeat_timeout
            node_offline = worker_node is None or worker_node.status is NodeStatus.OFFLINE
            if worker_stale or node_offline:
                if worker.status is not WorkerStatus.OFFLINE:
                    self._workers[worker_id] = replace(
                        worker,
                        status=WorkerStatus.OFFLINE,
                        updated_at=_state_timestamp(worker.updated_at, timestamp),
                    )

        return tuple(sorted(expired_nodes))''',
    )


def patch_persistence() -> None:
    path = "src/ai_multi_agent_platform/distributed/persistence.py"
    replace_once(
        path,
        'DISTRIBUTED_STATE_SCHEMA_VERSION = "2"\n_SUPPORTED_DISTRIBUTED_STATE_SCHEMA_VERSIONS = frozenset({"1", "2"})',
        'DISTRIBUTED_STATE_SCHEMA_VERSION = "3"\n_SUPPORTED_DISTRIBUTED_STATE_SCHEMA_VERSIONS = frozenset({"1", "2", "3"})',
    )
    replace_between(
        path,
        "def _node_record(",
        "def _worker_record(",
        '''def _node_record(value: JsonValue) -> NodeRecord:
    data = _object(value, "NodeRecord")
    registered_at = _datetime(_required(data, "registered_at"), "registered_at")
    last_heartbeat_at = _datetime(
        _required(data, "last_heartbeat_at"),
        "last_heartbeat_at",
    )
    updated_raw = data.get("updated_at")
    updated_at = (
        max(registered_at, last_heartbeat_at)
        if updated_raw is None
        else _datetime(updated_raw, "updated_at")
    )
    return NodeRecord(
        node_id=_required_string(data, "node_id"),
        display_name=_required_string(data, "display_name"),
        resources=_resource_snapshot(_required(data, "resources")),
        status=NodeStatus(_required_string(data, "status")),
        registered_at=registered_at,
        last_heartbeat_at=last_heartbeat_at,
        updated_at=updated_at,
        labels=_string_tuple(_required(data, "labels"), "labels"),
        os_name=_optional_string(data.get("os_name"), "os_name"),
        platform=_optional_string(data.get("platform"), "platform"),
        architecture=_optional_string(data.get("architecture"), "architecture"),
        supported_runtimes=_string_tuple(
            _required(data, "supported_runtimes"),
            "supported_runtimes",
        ),
        model_refs=_string_tuple(_required(data, "model_refs"), "model_refs"),
        capability_refs=_string_tuple(
            _required(data, "capability_refs"),
            "capability_refs",
        ),
        worker_refs=_string_tuple(_required(data, "worker_refs"), "worker_refs"),
        trust_level=_required_string(data, "trust_level"),
        draining=_boolean(_required(data, "draining"), "draining"),
        maintenance=_boolean(_required(data, "maintenance"), "maintenance"),
        network_available=_boolean(
            _required(data, "network_available"),
            "network_available",
        ),
        locality_refs=_string_tuple(_required(data, "locality_refs"), "locality_refs"),
        adapter_metadata=_adapter_metadata_tuple(
            _required(data, "adapter_metadata"),
            "adapter_metadata",
        ),
    )''',
    )
    replace_between(
        path,
        "def _worker_record(",
        "def _resource_snapshot(",
        '''def _worker_record(value: JsonValue) -> WorkerRecord:
    data = _object(value, "WorkerRecord")
    registered_at = _datetime(_required(data, "registered_at"), "registered_at")
    last_heartbeat_at = _datetime(
        _required(data, "last_heartbeat_at"),
        "last_heartbeat_at",
    )
    updated_raw = data.get("updated_at")
    updated_at = (
        max(registered_at, last_heartbeat_at)
        if updated_raw is None
        else _datetime(updated_raw, "updated_at")
    )
    return WorkerRecord(
        worker_id=_required_string(data, "worker_id"),
        node_id=_required_string(data, "node_id"),
        worker_type=_required_string(data, "worker_type"),
        supported_executors=_string_tuple(
            _required(data, "supported_executors"),
            "supported_executors",
        ),
        capability_refs=_string_tuple(
            _required(data, "capability_refs"),
            "capability_refs",
        ),
        supported_runtimes=_string_tuple(
            _required(data, "supported_runtimes"),
            "supported_runtimes",
        ),
        model_refs=_string_tuple(_required(data, "model_refs"), "model_refs"),
        concurrency_limit=_integer(
            _required(data, "concurrency_limit"),
            "concurrency_limit",
        ),
        active_jobs=_integer(_required(data, "active_jobs"), "active_jobs"),
        status=WorkerStatus(_required_string(data, "status")),
        protocol_version=_required_string(data, "protocol_version"),
        worker_version=_required_string(data, "worker_version"),
        registered_at=registered_at,
        last_heartbeat_at=last_heartbeat_at,
        updated_at=updated_at,
        draining=_boolean(_required(data, "draining"), "draining"),
        locality_refs=_string_tuple(_required(data, "locality_refs"), "locality_refs"),
        adapter_metadata=_adapter_metadata_tuple(
            _required(data, "adapter_metadata"),
            "adapter_metadata",
        ),
    )''',
    )


def patch_control_plane() -> None:
    path = "src/ai_multi_agent_platform/distributed/control_plane.py"
    replace_once(
        path,
        '        "last_heartbeat_at": _timestamp(node.last_heartbeat_at),\n        "labels": list(node.labels),',
        '        "last_heartbeat_at": _timestamp(node.last_heartbeat_at),\n        "updated_at": _timestamp(node.updated_at),\n        "labels": list(node.labels),',
    )
    replace_once(
        path,
        '        "last_heartbeat_at": _timestamp(worker.last_heartbeat_at),\n        "draining": worker.draining,',
        '        "last_heartbeat_at": _timestamp(worker.last_heartbeat_at),\n        "updated_at": _timestamp(worker.updated_at),\n        "draining": worker.draining,',
    )
    replace_once(
        path,
        '        "updated_at": _timestamp(node.last_heartbeat_at),',
        '        "updated_at": _timestamp(node.updated_at),',
    )
    replace_once(
        path,
        '        "updated_at": _timestamp(worker.last_heartbeat_at),',
        '        "updated_at": _timestamp(worker.updated_at),',
    )


def patch_search_tests() -> None:
    path = "tests/test_issue_288_node_worker_search.py"
    replace_once(path, "from datetime import UTC, datetime", "from datetime import UTC, datetime, timedelta")
    text = read(path)
    marker = "def test_search_updated_at_tracks_canonical_state_changes_not_heartbeats()"
    if marker in text:
        return
    text += '''\n\n\ndef test_search_updated_at_tracks_canonical_state_changes_not_heartbeats() -> None:
    async def scenario() -> None:
        _, http, runtime, node, _ = _stack()
        mutation_at = NOW + timedelta(minutes=5)
        heartbeat_before = runtime.registry.get_node(node.node_id).last_heartbeat_at

        runtime.registry.set_node_draining(node.node_id, draining=True, now=mutation_at)
        canonical = runtime.registry.get_node(node.node_id)
        assert canonical.last_heartbeat_at == heartbeat_before == NOW
        assert canonical.updated_at == mutation_at

        exact = await _search(http, {"id": node.node_id, "type": "node"})
        exact_item = _items(exact)[0]
        assert exact_item["updated_at"] == mutation_at.isoformat()

        included = await _search(
            http,
            {"type": "node", "updated_after": mutation_at.isoformat()},
        )
        assert {item["resource_id"] for item in _items(included)} == {node.node_id}

        excluded = await _search(
            http,
            {
                "type": "node",
                "updated_before": (mutation_at - timedelta(seconds=1)).isoformat(),
            },
        )
        assert node.node_id not in {item["resource_id"] for item in _items(excluded)}

    asyncio.run(scenario())
'''
    write(path, text)


def write_issue_tests() -> None:
    Path("tests/test_issue_414_node_worker_state_timestamps.py").write_text(
        '''from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from ai_multi_agent_platform.distributed import (
    DistributedRegistry,
    Heartbeat,
    NodeRecord,
    NodeStatus,
    RegistrationRequest,
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
''',
        encoding="utf-8",
    )


def write_adr() -> None:
    Path("docs/adr/0010-node-worker-state-change-timestamps.md").write_text(
        '''# ADR 0010 — Separate Node/Worker state-change time from heartbeat evidence

- **Status:** Accepted
- **Issue:** #414
- **Date:** 2026-09-06

## Context

The distributed runtime already records `registered_at` and `last_heartbeat_at` for canonical Node and Worker runtime projections. Derived consumers such as global Search also need a modification timestamp for filtering and ordering. Mapping `last_heartbeat_at` to a generic `updated_at` is incorrect because maintenance, draining, liveness expiry, restart health normalization and other canonical state transitions can happen without a heartbeat.

Conversely, advancing a generic modification timestamp for every otherwise unchanged heartbeat would simply rename liveness evidence and make high-frequency heartbeat traffic appear as meaningful state mutation.

## Decision

`NodeRecord` and `WorkerRecord` expose a timezone-aware canonical `updated_at` that is independent from `last_heartbeat_at`.

`last_heartbeat_at` means only the latest accepted liveness report. `updated_at` means the latest canonical caller-visible state change. A pure heartbeat refresh advances `last_heartbeat_at` only. A heartbeat that changes canonical health, resources, capacity or Worker metadata advances both timestamps. Registration and re-registration establish a new state timestamp. Administrative drain/maintenance changes and liveness-expiry transitions advance `updated_at` without fabricating a heartbeat. No-op administrative mutations do not advance it.

State timestamps advance monotonically within the registry with `max(previous_updated_at, event_time)`, so delayed or regressed event clocks cannot move modification history backwards. All canonical runtime timestamps must be timezone-aware.

Worker heartbeat payload timestamps are not trusted as Control-Plane modification truth. Existing Worker `registered_at` and canonical `updated_at` are preserved unless the accepted heartbeat actually changes canonical Worker state; a Worker first introduced through the generic registry heartbeat path receives the accepted heartbeat observation time.

On Control-Plane restart, persisted health is normalized to offline because persisted health is not fresh liveness evidence. When that normalization changes visible state, restore time advances `updated_at` while preserving `last_heartbeat_at`. Already-offline restored records retain their prior `updated_at`.

Distributed-state schema v3 persists the new field. The reference decoder remains compatible with v1/v2 snapshots by deriving missing `updated_at` as the later of `registered_at` and `last_heartbeat_at` before conservative restart normalization.

Global Search and other derived consumers use canonical `updated_at`; they never become timestamp authorities themselves.

## Consequences

- Heartbeat age remains semantically reliable liveness evidence.
- Search `updated_after`, `updated_before` and `sort=updated_at` reflect state mutation instead of heartbeat frequency.
- Operator and observability views can show heartbeat and modification time independently.
- Administrative state changes become discoverable by modification time without pretending a Worker reported them.
- Persistence schema v3 adds explicit modification timestamps while preserving restore compatibility for v1/v2.
- Scheduler eligibility and heartbeat timeout behavior are unchanged apart from timestamp bookkeeping.

## Alternatives considered

### Continue using `last_heartbeat_at` as `updated_at`

Rejected because non-heartbeat state changes would carry stale modification times and heartbeat traffic would be misrepresented as generic mutation.

### Advance `updated_at` for every heartbeat

Rejected because this would preserve the semantic conflation under a second field.

### Let Search or observability assign modification time

Rejected because derived consumers are not canonical state authorities and could disagree with one another.

### Use provider/backend timestamps

Rejected because it would violate deployment neutrality and make canonical ordering depend on replaceable infrastructure.

## Affected issues and contracts

- #414 owns this timestamp semantic.
- #14 supplies canonical Node/Worker registry and liveness behavior.
- #288 consumes `updated_at` for Node/Worker Search projections.
- #16 may consume heartbeat and state-change times independently for observability.
''',
        encoding="utf-8",
    )


def patch_docs() -> None:
    path = "docs/DISTRIBUTED_RUNTIME.md"
    replace_once(
        path,
        "- health/status and heartbeat timestamps;",
        "- health/status, heartbeat evidence and canonical state-change timestamps;",
    )
    replace_once(
        path,
        "- heartbeat/drain/locality state.",
        "- heartbeat, canonical state-change, drain and locality state.",
    )
    text = read(path)
    insertion = '''### State-change timestamp semantics

`last_heartbeat_at` is liveness evidence only. `updated_at` is the latest canonical caller-visible Node/Worker state change and is never assigned by Search or another derived consumer.

- registration/re-registration establishes both liveness and state-change time;
- a pure heartbeat refresh advances `last_heartbeat_at` but preserves `updated_at`;
- a heartbeat that changes canonical status/resources/Worker metadata advances `updated_at` as well;
- drain/maintenance changes advance `updated_at` without changing `last_heartbeat_at`;
- liveness expiry/offline transitions advance `updated_at` without rewriting the last accepted heartbeat;
- no-op administrative mutations preserve the previous state-change timestamp;
- state-change time is monotonic within the registry and all runtime timestamps are timezone-aware;
- restart health normalization to offline is a canonical state change when the persisted record was not already offline, but it is not a heartbeat.

Derived Search projections use canonical `updated_at`, so modification-time filters and ordering do not depend on heartbeat frequency.

'''
    marker = "## Placement policy\n"
    if insertion not in text:
        text = text.replace(marker, insertion + marker, 1)
    text = text.replace(
        "Distributed JSON state schema v2 adds the optional terminal result field while the reference store remains able to restore schema v1 snapshots. A v1 record therefore restores with no cached result and can recover the result from the same Worker after reachability is re-established.",
        "Distributed JSON state schema v3 adds explicit Node/Worker `updated_at` state-change timestamps. The reference store remains able to restore schema v1/v2 snapshots; missing state-change timestamps are derived conservatively from the later of registration and heartbeat time before restart health normalization. Schema v2 added the optional terminal result field, so a v1 dispatch record still restores with no cached result and can recover the result from the same Worker after reachability is re-established.",
        1,
    )
    write(path, text)


def main() -> None:
    patch_models()
    patch_registry()
    patch_persistence()
    patch_control_plane()
    patch_search_tests()
    write_issue_tests()
    write_adr()
    patch_docs()


if __name__ == "__main__":
    main()
