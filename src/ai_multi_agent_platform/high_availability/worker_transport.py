"""HA-aware Worker transport that rejects stale Control Plane fencing generations."""

from __future__ import annotations

from collections.abc import Mapping

from ai_multi_agent_platform.contracts import ExecutionHandle, ExecutionSnapshot
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.distributed.models import WorkerJobRequest
from ai_multi_agent_platform.distributed.transport import (
    WORKER_REPLY_TOPIC_PREFIX,
    TransportWorkerDispatcher,
    WorkerTransportCodec,
    WorkerTransportEndpoint,
)
from ai_multi_agent_platform.distributed.worker import WorkerDispatcher
from ai_multi_agent_platform.messaging import MessageTransport, TransportEnvelope

from .contracts import (
    AuthorityGrant,
    CoordinationError,
    CoordinationProvider,
    CoordinationUnavailable,
    FencingToken,
    NotLeaderError,
    StaleFencingToken,
)
from .integrations import AuthorityCheck

_CONTROL_PLANE_SOURCE_PREFIX = "distributed-control-plane"
_FENCED_OPERATIONS = frozenset({"dispatch", "cancel"})


class FencedTransportWorkerDispatcher(TransportWorkerDispatcher):
    """Control-side transport that attaches the current HA fencing token to side effects."""

    def __init__(
        self,
        worker_id: str,
        transport: MessageTransport,
        *,
        authority_check: AuthorityCheck,
        control_plane_instance_id: str,
        response_timeout_seconds: float = 30.0,
    ) -> None:
        if not control_plane_instance_id.strip():
            raise ValueError("control_plane_instance_id must not be blank")
        self._authority_check = authority_check
        self._control_plane_instance_id = control_plane_instance_id
        super().__init__(
            worker_id,
            transport,
            client_id=_source_component(control_plane_instance_id),
            response_timeout_seconds=response_timeout_seconds,
        )

    async def dispatch(self, job: WorkerJobRequest) -> ExecutionHandle:
        fence = await self._fence_payload()
        reply = await self._request(
            operation="dispatch",
            worker_job_id=job.worker_job_id,
            job=job,
            payload={
                "job": WorkerTransportCodec.encode_job(job),
                "control_plane_fence": fence,
            },
        )
        return WorkerTransportCodec.decode_handle(reply["handle"])

    async def cancel(self, worker_job_id: str) -> ExecutionSnapshot:
        fence = await self._fence_payload()
        reply = await self._request(
            operation="cancel",
            worker_job_id=worker_job_id,
            payload={"control_plane_fence": fence},
        )
        return WorkerTransportCodec.decode_snapshot(reply["snapshot"])

    async def _fence_payload(self) -> dict[str, JsonValue]:
        grant = await self._authority_check()
        token = _require_ha_token(grant)
        if grant.instance_id != self._control_plane_instance_id:
            raise StaleFencingToken(
                "authority grant belongs to a different Control Plane instance"
            )
        if token.instance_id != self._control_plane_instance_id:
            raise StaleFencingToken(
                "fencing token belongs to a different Control Plane instance"
            )
        return _encode_fencing_token(token)


class FencedWorkerTransportEndpoint(WorkerTransportEndpoint):
    """Worker-side endpoint that validates HA epochs before dispatch/cancel side effects.

    The endpoint consults the replaceable coordination authority directly instead of trusting a
    process-local highest-seen epoch. This means Worker restart does not erase stale-leader
    protection. Transport authentication remains the responsibility of the existing messaging and
    Worker service-identity boundaries; source-component binding here is an additional consistency
    check, not a replacement for authentication.
    """

    def __init__(
        self,
        dispatcher: WorkerDispatcher,
        transport: MessageTransport,
        *,
        coordinator: CoordinationProvider,
        consumer_id: str | None = None,
    ) -> None:
        self._ha_coordinator = coordinator
        super().__init__(dispatcher, transport, consumer_id=consumer_id)

    async def _handle(self, command: TransportEnvelope) -> None:
        data = command.payload
        if not isinstance(data, Mapping):
            await super()._handle(command)
            return

        operation = data.get("operation")
        if operation not in _FENCED_OPERATIONS:
            await super()._handle(command)
            return

        reply_topic = data.get("reply_topic")
        worker_job_id = data.get("worker_job_id")
        target_worker_id = data.get("worker_id")
        if (
            not isinstance(reply_topic, str)
            or not reply_topic.startswith(f"{WORKER_REPLY_TOPIC_PREFIX}.")
            or not isinstance(worker_job_id, str)
            or target_worker_id != self.worker_id
        ):
            await super()._handle(command)
            return

        try:
            token = _decode_fencing_token(data.get("control_plane_fence"))
        except (TypeError, ValueError) as exc:
            await self._publish_error(
                command,
                reply_topic,
                worker_job_id,
                category="control_plane_fence_required",
                message=str(exc),
                retryable=False,
            )
            return

        if command.source_component != _source_component(token.instance_id):
            await self._publish_error(
                command,
                reply_topic,
                worker_job_id,
                category="control_plane_fence_identity_mismatch",
                message="Worker command source does not match its Control Plane fencing identity",
                retryable=False,
            )
            return

        try:
            await self._ha_coordinator.assert_fence(token)
        except CoordinationUnavailable as exc:
            await self._publish_error(
                command,
                reply_topic,
                worker_job_id,
                category="control_plane_coordination_unavailable",
                message=str(exc),
                retryable=True,
            )
            return
        except StaleFencingToken as exc:
            await self._publish_error(
                command,
                reply_topic,
                worker_job_id,
                category="stale_control_plane_fence",
                message=str(exc),
                retryable=False,
            )
            return
        except CoordinationError as exc:
            await self._publish_error(
                command,
                reply_topic,
                worker_job_id,
                category="control_plane_fence_rejected",
                message=str(exc),
                retryable=False,
            )
            return

        await super()._handle(command)


def _require_ha_token(grant: AuthorityGrant) -> FencingToken:
    token = grant.fencing_token
    if token is None:
        raise NotLeaderError("HA Worker transport requires a current fencing token")
    return token


def _source_component(instance_id: str) -> str:
    return f"{_CONTROL_PLANE_SOURCE_PREFIX}:{instance_id}"


def _encode_fencing_token(token: FencingToken) -> dict[str, JsonValue]:
    return {"instance_id": token.instance_id, "epoch": token.epoch}


def _decode_fencing_token(value: object) -> FencingToken:
    if not isinstance(value, Mapping):
        raise ValueError("HA Worker command requires control_plane_fence")
    instance_id = value.get("instance_id")
    epoch = value.get("epoch")
    if not isinstance(instance_id, str) or not instance_id.strip():
        raise ValueError("control_plane_fence.instance_id must be a non-blank string")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch <= 0:
        raise ValueError("control_plane_fence.epoch must be a positive integer")
    return FencingToken(instance_id=instance_id, epoch=epoch)
