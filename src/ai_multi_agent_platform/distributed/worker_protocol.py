"""Authenticated worker-facing registration and heartbeat boundary for issue #14."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from ai_multi_agent_platform.contracts import AuthorizationProvider, OperationContext
from ai_multi_agent_platform.contracts.authorization import AuthorizationRequest
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.security import (
    ActorType,
    AuthenticatedActor,
    AuthorizationAction,
    CredentialScope,
    ResourceType,
)

from .models import Heartbeat, NodeRecord, RegistrationRequest, WorkerRecord, utc_now
from .pressure_reporting import authenticate_pressure_report
from .registry import RegistryError
from .runtime import DistributedRuntime


class WorkerProtocolError(RuntimeError):
    """Stable protocol-boundary rejection that never carries credential material."""


class WorkerProtocolAuthorizationError(WorkerProtocolError):
    """Raised when credential scope or #15 rejects a worker protocol action."""


class WorkerRequestAuthenticator(Protocol):
    """Minimal #36 worker-authentication surface consumed by the distributed runtime."""

    def authenticate_worker_request(
        self,
        token: str,
        *,
        nonce: str,
        issued_at: datetime,
        tls_peer_ref: str | None = None,
        now: datetime | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AuthenticatedActor: ...

    def credential_scope(self, credential_id: str) -> CredentialScope: ...


@dataclass(frozen=True, slots=True)
class WorkerRequestCredentials:
    token: str
    nonce: str
    issued_at: datetime
    tls_peer_ref: str | None = None
    request_id: str | None = None
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if not self.token:
            raise ValueError("worker credential token must not be empty")
        if not self.nonce.strip():
            raise ValueError("worker request nonce must not be blank")
        for value, label in (
            (self.tls_peer_ref, "tls_peer_ref"),
            (self.request_id, "request_id"),
            (self.correlation_id, "correlation_id"),
        ):
            if value is not None and not value.strip():
                raise ValueError(f"{label} must not be blank when provided")


@dataclass(frozen=True, slots=True)
class WorkerHeartbeatRequest:
    """Node-level heartbeat explicitly bound to the authenticated reporter Worker."""

    heartbeat: Heartbeat
    service_identity_ref: str

    def __post_init__(self) -> None:
        if not self.service_identity_ref.strip():
            raise ValueError("service_identity_ref must not be blank")


@dataclass(frozen=True, slots=True)
class WorkerProtocolReceipt:
    node_id: str
    reporter_worker_id: str
    observed_at: datetime
    worker_ids: tuple[str, ...]


class WorkerProtocolService:
    """Security boundary for remote Node reporters using #36 identity and #15 policy.

    Registration and heartbeat are authoritative Node Worker snapshots. The reporter is
    one Worker contained in the snapshot and is bound through ``service_identity_ref``.
    Runtime capability/resource reports may change, while Control-Plane-owned trust,
    maintenance and drain state cannot be overwritten by a remote reporter.
    """

    def __init__(
        self,
        runtime: DistributedRuntime,
        *,
        authentication: WorkerRequestAuthenticator,
        authorization: AuthorizationProvider,
        initial_trust_level: str = "untrusted",
    ) -> None:
        if not initial_trust_level.strip():
            raise ValueError("initial_trust_level must not be blank")
        self.runtime = runtime
        self.authentication = authentication
        self.authorization = authorization
        self.initial_trust_level = initial_trust_level

    async def register(
        self,
        request: RegistrationRequest,
        credentials: WorkerRequestCredentials,
        *,
        now: datetime | None = None,
    ) -> WorkerProtocolReceipt:
        timestamp = now or utc_now()
        actor = self._authenticate(credentials, now=timestamp)
        reporter_id = self._registration_reporter(request, actor)
        existing_node = self._optional_node(request.node.node_id)
        incoming_ids = {worker.worker_id for worker in request.workers}
        known_ids = self._known_worker_ids(request.node.node_id)
        missing = known_ids - incoming_ids
        if missing:
            raise WorkerProtocolError(
                "remote registration must include every known Worker; "
                "use explicit deregistration before removing Workers"
            )
        existing_workers = {
            worker.worker_id: self._optional_worker(worker.worker_id) for worker in request.workers
        }

        await self._authorize(
            actor,
            AuthorizationAction.CREATE if existing_node is None else AuthorizationAction.MODIFY,
            ResourceType.NODE,
            request.node.node_id,
            node_id=request.node.node_id,
            credentials=credentials,
        )
        for worker in request.workers:
            await self._authorize(
                actor,
                (
                    AuthorizationAction.CREATE
                    if existing_workers[worker.worker_id] is None
                    else AuthorizationAction.MODIFY
                ),
                ResourceType.WORKER,
                worker.worker_id,
                node_id=request.node.node_id,
                credentials=credentials,
            )

        safe_node = self._safe_registered_node(request.node, existing_node)
        safe_workers = tuple(
            authenticate_pressure_report(
                self._safe_registered_worker(worker, existing_workers[worker.worker_id]),
                node_id=request.node.node_id,
                reporter_worker_id=reporter_id,
                accepted_at=timestamp,
            )
            for worker in request.workers
        )
        safe_request = replace(
            request,
            node=safe_node,
            workers=safe_workers,
            service_identity_ref=reporter_id,
        )
        registered = self.runtime.register(safe_request, now=timestamp)
        return WorkerProtocolReceipt(
            node_id=registered.node_id,
            reporter_worker_id=reporter_id,
            observed_at=timestamp,
            worker_ids=registered.worker_refs,
        )

    async def heartbeat(
        self,
        request: WorkerHeartbeatRequest,
        credentials: WorkerRequestCredentials,
        *,
        now: datetime | None = None,
    ) -> WorkerProtocolReceipt:
        timestamp = now or utc_now()
        actor = self._authenticate(credentials, now=timestamp)
        reporter_id = self._require_reporter_identity(request.service_identity_ref, actor)
        node = self._required_node(request.heartbeat.node_id)
        reporter = self._required_worker(reporter_id)
        if reporter.node_id != node.node_id:
            raise WorkerProtocolError("authenticated reporter is not attached to heartbeat node")

        reported_ids = {worker.worker_id for worker in request.heartbeat.workers}
        known_ids = self._known_worker_ids(node.node_id)
        if reporter_id not in reported_ids:
            raise WorkerProtocolError("heartbeat must include the authenticated reporter Worker")
        if known_ids != reported_ids:
            raise WorkerProtocolError(
                "authenticated heartbeat must report the complete registered Worker snapshot"
            )

        await self._authorize(
            actor,
            AuthorizationAction.MODIFY,
            ResourceType.NODE,
            node.node_id,
            node_id=node.node_id,
            credentials=credentials,
        )
        safe_workers: list[WorkerRecord] = []
        for reported in request.heartbeat.workers:
            existing = self._required_worker(reported.worker_id)
            if existing.node_id != node.node_id:
                raise WorkerProtocolError("heartbeat cannot move a Worker between Nodes")
            await self._authorize(
                actor,
                AuthorizationAction.MODIFY,
                ResourceType.WORKER,
                existing.worker_id,
                node_id=node.node_id,
                credentials=credentials,
            )
            safe_workers.append(
                authenticate_pressure_report(
                    replace(
                        reported,
                        registered_at=existing.registered_at,
                        draining=existing.draining,
                    ),
                    node_id=node.node_id,
                    reporter_worker_id=reporter_id,
                    accepted_at=timestamp,
                )
            )

        safe_heartbeat = replace(
            request.heartbeat,
            observed_at=timestamp,
            workers=tuple(safe_workers),
        )
        updated = self.runtime.heartbeat(safe_heartbeat)
        return WorkerProtocolReceipt(
            node_id=updated.node_id,
            reporter_worker_id=reporter_id,
            observed_at=timestamp,
            worker_ids=tuple(worker.worker_id for worker in safe_workers),
        )

    async def deregister_worker(
        self,
        worker_id: str,
        node_id: str,
        credentials: WorkerRequestCredentials,
        *,
        now: datetime | None = None,
    ) -> None:
        timestamp = now or utc_now()
        actor = self._authenticate(credentials, now=timestamp)
        reporter_id = self._require_reporter_identity(worker_id, actor)
        worker = self._required_worker(reporter_id)
        if worker.node_id != node_id:
            raise WorkerProtocolError("worker deregistration node binding mismatch")
        await self._authorize(
            actor,
            AuthorizationAction.DELETE,
            ResourceType.WORKER,
            worker.worker_id,
            node_id=node_id,
            credentials=credentials,
        )
        self.runtime.deregister_worker(worker.worker_id)

    def _authenticate(
        self,
        credentials: WorkerRequestCredentials,
        *,
        now: datetime,
    ) -> AuthenticatedActor:
        actor = self.authentication.authenticate_worker_request(
            credentials.token,
            nonce=credentials.nonce,
            issued_at=credentials.issued_at,
            tls_peer_ref=credentials.tls_peer_ref,
            now=now,
            request_id=credentials.request_id,
            correlation_id=credentials.correlation_id,
        )
        if actor.identity.actor_type is not ActorType.WORKER:
            raise WorkerProtocolError("worker protocol requires an authenticated Worker identity")
        if actor.credential_id is None:
            raise WorkerProtocolError("worker protocol requires a credential-backed identity")
        return actor

    def _registration_reporter(
        self,
        request: RegistrationRequest,
        actor: AuthenticatedActor,
    ) -> str:
        reporter_ref = request.service_identity_ref
        if reporter_ref is None:
            raise WorkerProtocolError("remote registration requires service_identity_ref")
        reporter_id = self._require_reporter_identity(reporter_ref, actor)
        if reporter_id not in {worker.worker_id for worker in request.workers}:
            raise WorkerProtocolError("registration reporter must be part of the Worker snapshot")
        return reporter_id

    @staticmethod
    def _require_reporter_identity(
        reporter_ref: str,
        actor: AuthenticatedActor,
    ) -> str:
        if actor.identity.actor_id != reporter_ref:
            raise WorkerProtocolError("authenticated Worker does not match service_identity_ref")
        return reporter_ref

    async def _authorize(
        self,
        actor: AuthenticatedActor,
        action: AuthorizationAction,
        resource_type: ResourceType,
        resource_id: str,
        *,
        node_id: str,
        credentials: WorkerRequestCredentials,
    ) -> None:
        credential_id = actor.credential_id
        if credential_id is None:
            raise WorkerProtocolError("worker credential identity is missing")
        scope = self.authentication.credential_scope(credential_id)
        if not scope.allows(action, resource_type, resource_id):
            raise WorkerProtocolAuthorizationError(
                "worker credential scope does not permit this protocol action"
            )
        correlation_id = (
            actor.correlation_id
            or credentials.correlation_id
            or credentials.request_id
            or f"worker-request:{credentials.nonce}"
        )
        trust_context: dict[str, JsonValue] = {
            "authentication": {
                "method": actor.method.value,
                "credential_id": credential_id,
                "credential_scope": scope.to_json(),
            }
        }
        if credentials.tls_peer_ref is not None:
            trust_context["tls_peer_ref"] = credentials.tls_peer_ref
        decision = await self.authorization.authorize(
            AuthorizationRequest(
                principal_ref=actor.identity.actor_id,
                action=action.value,
                resource_ref=resource_id,
                context=OperationContext(correlation_id=correlation_id),
                actor_type=ActorType.WORKER.value,
                resource_type=resource_type.value,
                node_id=node_id,
                trust_context=trust_context,
                side_effect=f"worker_protocol.{action.value}",
            )
        )
        if not decision.allowed:
            reason = decision.reason or "worker protocol action denied by #15"
            raise WorkerProtocolAuthorizationError(reason)

    def _safe_registered_node(
        self,
        reported: NodeRecord,
        existing: NodeRecord | None,
    ) -> NodeRecord:
        if existing is None:
            return replace(
                reported,
                trust_level=self.initial_trust_level,
                draining=False,
                maintenance=False,
            )
        return replace(
            reported,
            trust_level=existing.trust_level,
            draining=existing.draining,
            maintenance=existing.maintenance,
        )

    @staticmethod
    def _safe_registered_worker(
        reported: WorkerRecord,
        existing: WorkerRecord | None,
    ) -> WorkerRecord:
        if existing is None:
            return replace(reported, draining=False)
        if existing.node_id != reported.node_id:
            raise WorkerProtocolError("registration cannot move a Worker between Nodes")
        return replace(reported, draining=existing.draining)

    def _known_worker_ids(self, node_id: str) -> set[str]:
        return {
            worker.worker_id
            for worker in self.runtime.registry.list_workers()
            if worker.node_id == node_id
        }

    def _required_node(self, node_id: str) -> NodeRecord:
        try:
            return self.runtime.registry.get_node(node_id)
        except RegistryError as exc:
            raise WorkerProtocolError(f"unknown Node: {node_id}") from exc

    def _required_worker(self, worker_id: str) -> WorkerRecord:
        try:
            return self.runtime.registry.get_worker(worker_id)
        except RegistryError as exc:
            raise WorkerProtocolError(f"unknown Worker: {worker_id}") from exc

    def _optional_node(self, node_id: str) -> NodeRecord | None:
        try:
            return self.runtime.registry.get_node(node_id)
        except RegistryError:
            return None

    def _optional_worker(self, worker_id: str) -> WorkerRecord | None:
        try:
            return self.runtime.registry.get_worker(worker_id)
        except RegistryError:
            return None
