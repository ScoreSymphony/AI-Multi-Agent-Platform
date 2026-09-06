"""Deterministic in-memory node/worker registry and lease store."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from .models import (
    WORKER_PROTOCOL_VERSION,
    AcceleratorResource,
    Heartbeat,
    JobRequirements,
    NodeRecord,
    NodeStatus,
    RegistrationRequest,
    Reservation,
    ReservationStatus,
    ResourceSnapshot,
    WorkerRecord,
    WorkerStatus,
    utc_now,
)


class RegistryError(RuntimeError):
    """Raised when registration, liveness or reservation invariants are violated."""


def _state_timestamp(previous: datetime, event_time: datetime) -> datetime:
    """Advance canonical state time monotonically without changing heartbeat evidence."""

    return max(previous, event_time)


def _worker_state_changed(previous: WorkerRecord, current: WorkerRecord) -> bool:
    """Compare Worker state while excluding liveness and modification timestamps."""

    normalized = replace(
        previous,
        registered_at=current.registered_at,
        last_heartbeat_at=current.last_heartbeat_at,
        updated_at=current.updated_at,
    )
    return normalized != current


@dataclass(frozen=True, slots=True)
class RegistrySnapshot:
    """Restart-safe registry state without backend- or process-specific identity."""

    nodes: tuple[NodeRecord, ...] = ()
    workers: tuple[WorkerRecord, ...] = ()
    heartbeat_sequences: tuple[tuple[str, int], ...] = ()
    reservations: tuple[Reservation, ...] = ()


class DistributedRegistry:
    """Reference registry used by both single-node and multi-node scheduling."""

    def __init__(
        self,
        *,
        heartbeat_timeout: timedelta = timedelta(seconds=30),
        reservation_ttl: timedelta = timedelta(seconds=60),
    ) -> None:
        if heartbeat_timeout <= timedelta(0):
            raise ValueError("heartbeat_timeout must be positive")
        if reservation_ttl <= timedelta(0):
            raise ValueError("reservation_ttl must be positive")
        self.heartbeat_timeout = heartbeat_timeout
        self.reservation_ttl = reservation_ttl
        self._nodes: dict[str, NodeRecord] = {}
        self._workers: dict[str, WorkerRecord] = {}
        self._heartbeat_sequence: dict[str, int] = {}
        self._reservations: dict[str, Reservation] = {}
        self._job_reservation: dict[str, str] = {}

    def snapshot(self) -> RegistrySnapshot:
        """Capture only state required to preserve liveness and capacity correctness."""

        return RegistrySnapshot(
            nodes=self.list_nodes(),
            workers=self.list_workers(),
            heartbeat_sequences=tuple(sorted(self._heartbeat_sequence.items())),
            reservations=self.active_reservations(),
        )

    def restore_snapshot(
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
        self._job_reservation = job_reservations

    def register(
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
        return node

    def heartbeat(self, heartbeat: Heartbeat) -> NodeRecord:
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
        return updated

    def list_nodes(self) -> tuple[NodeRecord, ...]:
        return tuple(self._nodes[node_id] for node_id in sorted(self._nodes))

    def list_workers(self) -> tuple[WorkerRecord, ...]:
        return tuple(self._workers[worker_id] for worker_id in sorted(self._workers))

    def get_node(self, node_id: str) -> NodeRecord:
        try:
            return self._nodes[node_id]
        except KeyError as exc:
            raise RegistryError(f"unknown node: {node_id}") from exc

    def get_worker(self, worker_id: str) -> WorkerRecord:
        try:
            return self._workers[worker_id]
        except KeyError as exc:
            raise RegistryError(f"unknown worker: {worker_id}") from exc

    def set_node_draining(
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

        return tuple(sorted(expired_nodes))

    def active_reservations(
        self,
        *,
        worker_id: str | None = None,
        node_id: str | None = None,
    ) -> tuple[Reservation, ...]:
        claiming_statuses = {ReservationStatus.RESERVED, ReservationStatus.ACTIVE}
        reservations = (
            reservation
            for reservation in self._reservations.values()
            if reservation.status in claiming_statuses
            and (worker_id is None or reservation.worker_id == worker_id)
            and (node_id is None or reservation.node_id == node_id)
        )
        return tuple(sorted(reservations, key=lambda item: item.reservation_id))

    def reserved_node_resources(self, node_id: str) -> ResourceSnapshot:
        reservations = self.active_reservations(node_id=node_id)
        return ResourceSnapshot(
            cpu_cores_total=sum(item.cpu_cores for item in reservations),
            cpu_cores_available=sum(item.cpu_cores for item in reservations),
            ram_total_bytes=sum(item.ram_bytes for item in reservations),
            ram_available_bytes=sum(item.ram_bytes for item in reservations),
            storage_total_bytes=sum(item.storage_bytes for item in reservations),
            storage_available_bytes=sum(item.storage_bytes for item in reservations),
        )

    def reserved_accelerator_memory(self, node_id: str, accelerator_id: str) -> int:
        return sum(
            reservation.vram_bytes
            for reservation in self.active_reservations(node_id=node_id)
            if reservation.accelerator_id == accelerator_id
        )

    def available_node_resources(self, node_id: str) -> ResourceSnapshot:
        """Combine reported availability with scheduler-owned node-wide claims."""

        node = self.get_node(node_id)
        reserved = self.reserved_node_resources(node_id)
        cpu_available = max(
            0.0,
            min(
                node.resources.cpu_cores_available,
                node.resources.cpu_cores_total - reserved.cpu_cores_total,
            ),
        )
        ram_available = max(
            0,
            min(
                node.resources.ram_available_bytes,
                node.resources.ram_total_bytes - reserved.ram_total_bytes,
            ),
        )
        storage_available = max(
            0,
            min(
                node.resources.storage_available_bytes,
                node.resources.storage_total_bytes - reserved.storage_total_bytes,
            ),
        )
        accelerators = tuple(
            self._available_accelerator(node.node_id, accelerator)
            for accelerator in node.resources.accelerators
        )
        return ResourceSnapshot(
            cpu_cores_total=node.resources.cpu_cores_total,
            cpu_cores_available=cpu_available,
            ram_total_bytes=node.resources.ram_total_bytes,
            ram_available_bytes=ram_available,
            storage_total_bytes=node.resources.storage_total_bytes,
            storage_available_bytes=storage_available,
            accelerators=accelerators,
        )

    def reserved_concurrency(self, worker_id: str) -> int:
        return sum(
            reservation.concurrency_units
            for reservation in self.active_reservations(worker_id=worker_id)
        )

    def available_concurrency(self, worker_id: str) -> int:
        """Avoid double-counting active load already represented by reservations."""

        worker = self.get_worker(worker_id)
        reported_available = worker.concurrency_limit - worker.active_jobs
        reserved_available = worker.concurrency_limit - self.reserved_concurrency(worker_id)
        return max(0, min(reported_available, reserved_available))

    def reserve(
        self,
        *,
        worker_job_id: str,
        worker_id: str,
        requirements: JobRequirements,
        now: datetime | None = None,
    ) -> Reservation:
        """Claim node resources and worker concurrency idempotently for one worker job."""

        timestamp = now or utc_now()
        self.expire_reservations(now=timestamp)
        existing_id = self._job_reservation.get(worker_job_id)
        if existing_id is not None:
            existing = self._reservations[existing_id]
            if existing.status in {ReservationStatus.RESERVED, ReservationStatus.ACTIVE}:
                if existing.worker_id != worker_id:
                    raise RegistryError("worker job already has an active reservation elsewhere")
                return existing

        worker = self.get_worker(worker_id)
        node = self.get_node(worker.node_id)
        available = self.available_node_resources(node.node_id)
        if requirements.cpu_cores_min > available.cpu_cores_available:
            raise RegistryError("insufficient CPU capacity for reservation")
        if requirements.ram_min_bytes > available.ram_available_bytes:
            raise RegistryError("insufficient RAM capacity for reservation")
        if requirements.storage_min_bytes > available.storage_available_bytes:
            raise RegistryError("insufficient storage capacity for reservation")
        if requirements.concurrency_units > self.available_concurrency(worker_id):
            raise RegistryError("insufficient worker concurrency for reservation")
        if requirements.gpu == "forbidden" and available.accelerators:
            raise RegistryError("CPU-only placement required")

        accelerator_id: str | None = None
        if requirements.gpu == "required" or requirements.vram_min_bytes > 0:
            eligible_accelerators = tuple(
                accelerator
                for accelerator in available.accelerators
                if accelerator.memory_available_bytes >= requirements.vram_min_bytes
            )
            if not eligible_accelerators:
                if requirements.gpu == "required" and not available.accelerators:
                    raise RegistryError("accelerator required for reservation")
                raise RegistryError("insufficient VRAM capacity for reservation")
            accelerator_id = min(
                eligible_accelerators,
                key=lambda accelerator: accelerator.accelerator_id,
            ).accelerator_id

        reservation = Reservation(
            worker_job_id=worker_job_id,
            worker_id=worker_id,
            node_id=node.node_id,
            cpu_cores=requirements.cpu_cores_min,
            ram_bytes=requirements.ram_min_bytes,
            storage_bytes=requirements.storage_min_bytes,
            concurrency_units=requirements.concurrency_units,
            accelerator_id=accelerator_id,
            vram_bytes=requirements.vram_min_bytes,
            created_at=timestamp,
            expires_at=timestamp + self.reservation_ttl,
        )
        self._reservations[reservation.reservation_id] = reservation
        self._job_reservation[worker_job_id] = reservation.reservation_id
        return reservation

    def commit_reservation(
        self,
        reservation_id: str,
        *,
        now: datetime | None = None,
    ) -> Reservation:
        """Mark a dispatch-acknowledged claim active and renew its lease."""

        timestamp = now or utc_now()
        reservation = self._reservation(reservation_id)
        if reservation.status is ReservationStatus.ACTIVE:
            return self.renew_reservation(reservation_id, now=timestamp)
        if reservation.status is not ReservationStatus.RESERVED:
            raise RegistryError("only reserved capacity can be committed")
        committed = replace(
            reservation,
            status=ReservationStatus.ACTIVE,
            expires_at=timestamp + self.reservation_ttl,
        )
        self._reservations[reservation_id] = committed
        return committed

    def renew_reservation(
        self,
        reservation_id: str,
        *,
        now: datetime | None = None,
    ) -> Reservation:
        """Renew a live accepted job lease using fresh worker/reconciliation evidence."""

        timestamp = now or utc_now()
        reservation = self._reservation(reservation_id)
        if reservation.status is not ReservationStatus.ACTIVE:
            raise RegistryError("only active reservations can be renewed")
        renewed = replace(
            reservation,
            expires_at=timestamp + self.reservation_ttl,
        )
        self._reservations[reservation_id] = renewed
        return renewed

    def release_reservation(self, reservation_id: str) -> Reservation:
        reservation = self._reservation(reservation_id)
        if reservation.status in {ReservationStatus.RELEASED, ReservationStatus.EXPIRED}:
            return reservation
        released = replace(reservation, status=ReservationStatus.RELEASED)
        self._reservations[reservation_id] = released
        return released

    def expire_reservations(self, *, now: datetime | None = None) -> tuple[str, ...]:
        timestamp = now or utc_now()
        expired: list[str] = []
        for reservation_id, reservation in tuple(self._reservations.items()):
            if reservation.status not in {ReservationStatus.RESERVED, ReservationStatus.ACTIVE}:
                continue
            if reservation.expires_at is None or timestamp < reservation.expires_at:
                continue
            self._reservations[reservation_id] = replace(
                reservation,
                status=ReservationStatus.EXPIRED,
            )
            expired.append(reservation_id)
        return tuple(sorted(expired))

    def _available_accelerator(
        self,
        node_id: str,
        accelerator: AcceleratorResource,
    ) -> AcceleratorResource:
        reserved = self.reserved_accelerator_memory(node_id, accelerator.accelerator_id)
        available = max(
            0,
            min(
                accelerator.memory_available_bytes,
                accelerator.memory_total_bytes - reserved,
            ),
        )
        return replace(accelerator, memory_available_bytes=available)

    def _renew_active_reservations_for_worker(self, worker_id: str, *, now: datetime) -> None:
        for reservation in self.active_reservations(worker_id=worker_id):
            if reservation.status is ReservationStatus.ACTIVE:
                self.renew_reservation(reservation.reservation_id, now=now)

    @staticmethod
    def _assert_worker_protocol_versions(workers: tuple[WorkerRecord, ...]) -> None:
        for worker in workers:
            if worker.protocol_version != WORKER_PROTOCOL_VERSION:
                raise RegistryError(
                    "worker protocol version mismatch: "
                    f"{worker.worker_id} reports {worker.protocol_version!r}; "
                    f"expected {WORKER_PROTOCOL_VERSION!r}"
                )

    def _reservation(self, reservation_id: str) -> Reservation:
        try:
            return self._reservations[reservation_id]
        except KeyError as exc:
            raise RegistryError(f"unknown reservation: {reservation_id}") from exc
