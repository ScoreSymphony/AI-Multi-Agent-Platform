"""Deterministic in-memory node/worker registry and lease store."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from .models import (
    Heartbeat,
    JobRequirements,
    NodeRecord,
    NodeStatus,
    RegistrationRequest,
    Reservation,
    ReservationStatus,
    ResourceSnapshot,
    WORKER_PROTOCOL_VERSION,
    WorkerRecord,
    WorkerStatus,
    utc_now,
)


class RegistryError(RuntimeError):
    """Raised when registration, liveness or reservation invariants are violated."""


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
        node = replace(
            request.node,
            registered_at=timestamp,
            last_heartbeat_at=timestamp,
            status=NodeStatus.MAINTENANCE
            if request.node.maintenance
            else NodeStatus.ONLINE,
            worker_refs=tuple(sorted(worker.worker_id for worker in request.workers)),
        )
        existing = self._nodes.get(node.node_id)
        if existing is not None and existing.display_name != node.display_name:
            # Display names are descriptive, not identity. Re-registration may update them.
            pass
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
            self._workers[stale_worker_id] = replace(
                stale,
                status=WorkerStatus.OFFLINE,
                draining=True,
            )
        for worker in request.workers:
            self._workers[worker.worker_id] = replace(
                worker,
                registered_at=timestamp,
                last_heartbeat_at=timestamp,
            )
        return node

    def heartbeat(self, heartbeat: Heartbeat) -> NodeRecord:
        """Apply an idempotent monotonic heartbeat and refresh worker state."""

        if heartbeat.protocol_version != WORKER_PROTOCOL_VERSION:
            raise RegistryError("heartbeat protocol version mismatch")
        try:
            node = self._nodes[heartbeat.node_id]
        except KeyError as exc:
            raise RegistryError(f"unknown node: {heartbeat.node_id}") from exc

        previous_sequence = self._heartbeat_sequence.get(heartbeat.node_id, 0)
        if heartbeat.sequence < previous_sequence:
            raise RegistryError("stale heartbeat sequence")
        if heartbeat.sequence == previous_sequence and previous_sequence != 0:
            return node

        status = heartbeat.node_status or node.status
        if node.maintenance:
            status = NodeStatus.MAINTENANCE
        updated = replace(
            node,
            last_heartbeat_at=heartbeat.observed_at,
            resources=heartbeat.resources or node.resources,
            status=status,
        )
        self._nodes[node.node_id] = updated
        self._heartbeat_sequence[node.node_id] = heartbeat.sequence
        for worker in heartbeat.workers:
            existing = self._workers.get(worker.worker_id)
            if existing is not None and existing.node_id != node.node_id:
                raise RegistryError("worker cannot move between nodes during heartbeat")
            self._workers[worker.worker_id] = replace(
                worker,
                last_heartbeat_at=heartbeat.observed_at,
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

    def set_node_draining(self, node_id: str, *, draining: bool) -> NodeRecord:
        node = self.get_node(node_id)
        updated = replace(node, draining=draining)
        self._nodes[node_id] = updated
        return updated

    def set_node_maintenance(self, node_id: str, *, maintenance: bool) -> NodeRecord:
        node = self.get_node(node_id)
        updated = replace(
            node,
            maintenance=maintenance,
            status=NodeStatus.MAINTENANCE if maintenance else NodeStatus.ONLINE,
        )
        self._nodes[node_id] = updated
        return updated

    def set_worker_draining(self, worker_id: str, *, draining: bool) -> WorkerRecord:
        worker = self.get_worker(worker_id)
        updated = replace(worker, draining=draining)
        self._workers[worker_id] = updated
        return updated

    def deregister_worker(self, worker_id: str) -> None:
        self.get_worker(worker_id)
        self._workers.pop(worker_id)
        for reservation in tuple(self.active_reservations(worker_id=worker_id)):
            self.release_reservation(reservation.reservation_id)

    def deregister_node(self, node_id: str) -> None:
        self.get_node(node_id)
        for worker in tuple(self.list_workers()):
            if worker.node_id == node_id:
                self.deregister_worker(worker.worker_id)
        self._nodes.pop(node_id)
        self._heartbeat_sequence.pop(node_id, None)

    def expire_heartbeats(self, *, now: datetime | None = None) -> tuple[str, ...]:
        """Mark nodes/workers offline after deterministic heartbeat expiry."""

        timestamp = now or utc_now()
        expired: list[str] = []
        for node_id, node in tuple(self._nodes.items()):
            if timestamp - node.last_heartbeat_at <= self.heartbeat_timeout:
                continue
            if node.status is not NodeStatus.OFFLINE:
                self._nodes[node_id] = replace(node, status=NodeStatus.OFFLINE)
                expired.append(node_id)
            for worker_id, worker in tuple(self._workers.items()):
                if worker.node_id == node_id and worker.status is not WorkerStatus.OFFLINE:
                    self._workers[worker_id] = replace(worker, status=WorkerStatus.OFFLINE)
        return tuple(sorted(expired))

    def active_reservations(
        self,
        *,
        worker_id: str | None = None,
    ) -> tuple[Reservation, ...]:
        reservations = (
            reservation
            for reservation in self._reservations.values()
            if reservation.status is ReservationStatus.ACTIVE
            and (worker_id is None or reservation.worker_id == worker_id)
        )
        return tuple(sorted(reservations, key=lambda item: item.reservation_id))

    def reserved_resources(self, worker_id: str) -> ResourceSnapshot:
        reservations = self.active_reservations(worker_id=worker_id)
        return ResourceSnapshot(
            cpu_cores_total=sum(item.cpu_cores for item in reservations),
            cpu_cores_available=sum(item.cpu_cores for item in reservations),
            ram_total_bytes=sum(item.ram_bytes for item in reservations),
            ram_available_bytes=sum(item.ram_bytes for item in reservations),
            storage_total_bytes=sum(item.storage_bytes for item in reservations),
            storage_available_bytes=sum(item.storage_bytes for item in reservations),
        )

    def reserved_concurrency(self, worker_id: str) -> int:
        return sum(
            reservation.concurrency_units
            for reservation in self.active_reservations(worker_id=worker_id)
        )

    def reserve(
        self,
        *,
        worker_job_id: str,
        worker_id: str,
        requirements: JobRequirements,
        now: datetime | None = None,
    ) -> Reservation:
        """Claim capacity idempotently for one worker job."""

        timestamp = now or utc_now()
        self.expire_reservations(now=timestamp)
        existing_id = self._job_reservation.get(worker_job_id)
        if existing_id is not None:
            existing = self._reservations[existing_id]
            if existing.status is ReservationStatus.ACTIVE:
                if existing.worker_id != worker_id:
                    raise RegistryError("worker job already has an active reservation elsewhere")
                return existing

        worker = self.get_worker(worker_id)
        node = self.get_node(worker.node_id)
        reserved = self.reserved_resources(worker_id)
        available_cpu = node.resources.cpu_cores_available - reserved.cpu_cores_total
        available_ram = node.resources.ram_available_bytes - reserved.ram_total_bytes
        available_storage = node.resources.storage_available_bytes - reserved.storage_total_bytes
        available_slots = (
            worker.concurrency_limit - worker.active_jobs - self.reserved_concurrency(worker_id)
        )
        if requirements.cpu_cores_min > available_cpu:
            raise RegistryError("insufficient CPU capacity for reservation")
        if requirements.ram_min_bytes > available_ram:
            raise RegistryError("insufficient RAM capacity for reservation")
        if requirements.storage_min_bytes > available_storage:
            raise RegistryError("insufficient storage capacity for reservation")
        if requirements.concurrency_units > available_slots:
            raise RegistryError("insufficient worker concurrency for reservation")

        reservation = Reservation(
            worker_job_id=worker_job_id,
            worker_id=worker_id,
            cpu_cores=requirements.cpu_cores_min,
            ram_bytes=requirements.ram_min_bytes,
            storage_bytes=requirements.storage_min_bytes,
            concurrency_units=requirements.concurrency_units,
            created_at=timestamp,
            expires_at=timestamp + self.reservation_ttl,
        )
        self._reservations[reservation.reservation_id] = reservation
        self._job_reservation[worker_job_id] = reservation.reservation_id
        return reservation

    def release_reservation(self, reservation_id: str) -> Reservation:
        try:
            reservation = self._reservations[reservation_id]
        except KeyError as exc:
            raise RegistryError(f"unknown reservation: {reservation_id}") from exc
        if reservation.status is not ReservationStatus.ACTIVE:
            return reservation
        released = replace(reservation, status=ReservationStatus.RELEASED)
        self._reservations[reservation_id] = released
        return released

    def expire_reservations(self, *, now: datetime | None = None) -> tuple[str, ...]:
        timestamp = now or utc_now()
        expired: list[str] = []
        for reservation_id, reservation in tuple(self._reservations.items()):
            if reservation.status is not ReservationStatus.ACTIVE:
                continue
            if reservation.expires_at is None or timestamp < reservation.expires_at:
                continue
            self._reservations[reservation_id] = replace(
                reservation,
                status=ReservationStatus.EXPIRED,
            )
            expired.append(reservation_id)
        return tuple(sorted(expired))
