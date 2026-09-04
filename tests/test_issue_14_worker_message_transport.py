from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import replace

import pytest

from ai_multi_agent_platform.contracts import (
    ExecutionHandle,
    ExecutionRequest,
    ExecutionSnapshot,
    LifecycleBackend,
    OperationContext,
    OperationControl,
    ProviderDescriptor,
    RetryMode,
)
from ai_multi_agent_platform.distributed import LocalWorker, WorkerJobRequest, WorkerJobResult
from ai_multi_agent_platform.distributed.transport import (
    WORKER_REPLY_TOPIC_PREFIX,
    RemoteWorkerTransportError,
    TransportWorkerDispatcher,
    WorkerTransportCodec,
    WorkerTransportEndpoint,
)
from ai_multi_agent_platform.domain import RunStatus, new_id
from ai_multi_agent_platform.messaging import (
    InProcessMessageTransport,
    MessageKind,
    PublishReceipt,
    TransportEnvelope,
)


class _Lifecycle(LifecycleBackend):
    def __init__(self) -> None:
        self.states: dict[str, RunStatus] = {}
        self.start_calls = 0

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="issue-14-transport-lifecycle",
            provider_type="lifecycle",
        )

    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        if request.run_id not in self.states:
            self.start_calls += 1
            self.states[request.run_id] = RunStatus.RUNNING
        return ExecutionHandle(run_id=request.run_id, backend_ref="transport-fixture")

    async def get(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        del context
        return ExecutionSnapshot(
            run_id=run_id,
            status=self.states[run_id],
            output={"source": "remote-worker"},
        )

    async def cancel(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        del context
        self.states[run_id] = RunStatus.CANCELLED
        return await self.get(run_id, OperationContext(correlation_id="cancelled"))


class _ResultWorker(LocalWorker):
    def __init__(self, worker_id: str, lifecycle: LifecycleBackend) -> None:
        super().__init__(worker_id, lifecycle)
        self.output_artifact = new_id("artifact")
        self.evidence_ref = "evidence:worker-transport"

    async def result(self, worker_job_id: str) -> WorkerJobResult | None:
        result = await super().result(worker_job_id)
        if result is None:
            return None
        return replace(
            result,
            artifact_refs=(*result.artifact_refs, self.output_artifact),
            evidence_refs=(self.evidence_ref,),
        )


class _RecordingTransport(InProcessMessageTransport):
    def __init__(self) -> None:
        super().__init__(provider_id="issue-14-recording-transport")
        self.published: list[tuple[str, TransportEnvelope]] = []

    async def _publish_once(
        self,
        topic: str,
        envelope: TransportEnvelope,
    ) -> PublishReceipt:
        self.published.append((topic, envelope))
        return await super()._publish_once(topic, envelope)


class _DropFirstDispatchReplyTransport(_RecordingTransport):
    def __init__(self) -> None:
        super().__init__()
        self.dropped = False

    async def _publish_once(
        self,
        topic: str,
        envelope: TransportEnvelope,
    ) -> PublishReceipt:
        self.published.append((topic, envelope))
        if (
            not self.dropped
            and topic.startswith(f"{WORKER_REPLY_TOPIC_PREFIX}.")
            and envelope.message_type == "worker.dispatch.accepted"
        ):
            self.dropped = True
            return PublishReceipt(message_id=envelope.message_id, topic=topic)
        return await InProcessMessageTransport._publish_once(self, topic, envelope)


class _DropFirstResultReplyTransport(_RecordingTransport):
    def __init__(self) -> None:
        super().__init__()
        self.dropped = False

    async def _publish_once(
        self,
        topic: str,
        envelope: TransportEnvelope,
    ) -> PublishReceipt:
        self.published.append((topic, envelope))
        if (
            not self.dropped
            and topic.startswith(f"{WORKER_REPLY_TOPIC_PREFIX}.")
            and envelope.message_type == "worker.result"
        ):
            self.dropped = True
            return PublishReceipt(message_id=envelope.message_id, topic=topic)
        return await InProcessMessageTransport._publish_once(self, topic, envelope)


def _job() -> WorkerJobRequest:
    project_id = new_id("project")
    return WorkerJobRequest(
        worker_job_id=new_id("worker_job"),
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=new_id("task"),
            context=OperationContext(
                correlation_id="issue-14-remote-transport",
                causation_id="message-upstream",
                owner_type="service",
                owner_id="service:distributed-runtime",
                project_id=project_id,
                control=OperationControl(
                    timeout_seconds=5,
                    idempotency_key="canonical-run-request",
                    retry_mode=RetryMode.IDEMPOTENT,
                ),
            ),
            input={"prompt_ref": "artifact:input"},
        ),
        workspace_ref=new_id("workspace"),
        snapshot_ref=new_id("workspace_snapshot"),
        artifact_refs=(new_id("artifact"),),
        secret_refs=("secret-ref:worker-api",),
        actor_ref="service:distributed-runtime",
        cancellation_ref="cancel:issue-14",
        timeout_seconds=5,
        dispatch_attempt=2,
        idempotency_key="worker-job-idempotency",
        trace_parent="00-issue14-trace-parent",
    )


async def _stop_endpoint(task: asyncio.Task[None], transport: InProcessMessageTransport) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    await transport.close(graceful=False)


def test_worker_transport_codec_round_trip_preserves_portable_job_contract() -> None:
    job = _job()
    encoded = WorkerTransportCodec.encode_job(job)
    decoded = WorkerTransportCodec.decode_job(encoded)

    assert decoded == job
    assert decoded.execution.context.project_id == job.execution.context.project_id
    assert decoded.workspace_ref == job.workspace_ref
    assert decoded.snapshot_ref == job.snapshot_ref
    assert decoded.artifact_refs == job.artifact_refs
    assert decoded.secret_refs == job.secret_refs
    assert decoded.dispatch_attempt == 2
    assert decoded.trace_parent == job.trace_parent


def test_message_transport_dispatch_snapshot_result_and_cancel_round_trip() -> None:
    async def scenario() -> None:
        transport = _RecordingTransport()
        lifecycle = _Lifecycle()
        worker_id = new_id("worker")
        worker = _ResultWorker(worker_id, lifecycle)
        endpoint = WorkerTransportEndpoint(worker, transport)
        endpoint_task = asyncio.create_task(endpoint.serve())
        await asyncio.sleep(0)
        client = TransportWorkerDispatcher(worker_id, transport)
        job = _job()

        try:
            handle = await client.dispatch(job)
            assert handle.run_id == job.execution.run_id
            assert handle.backend_ref == "transport-fixture"

            running = await client.get(job.worker_job_id)
            assert running.status is RunStatus.RUNNING
            assert running.output == {"source": "remote-worker"}

            lifecycle.states[job.execution.run_id] = RunStatus.SUCCEEDED
            result = await client.result(job.worker_job_id)
            assert result is not None
            assert result.status.value == "succeeded"
            assert result.worker_id == worker_id
            assert worker.output_artifact in result.artifact_refs
            assert result.evidence_refs == (worker.evidence_ref,)
            assert result.execution is not None
            assert result.execution.status is RunStatus.SUCCEEDED

            cancelled = await client.cancel(job.worker_job_id)
            assert cancelled.status is RunStatus.CANCELLED

            command = next(
                envelope
                for _, envelope in transport.published
                if envelope.message_type == "worker.dispatch"
            )
            reply = next(
                envelope
                for _, envelope in transport.published
                if envelope.message_type == "worker.dispatch.accepted"
            )
            assert command.correlation_id == job.execution.context.correlation_id
            assert command.project_id == job.execution.context.project_id
            assert command.task_id == job.execution.subject_id
            assert command.run_id == job.execution.run_id
            assert command.idempotency_key == f"{job.worker_job_id}:dispatch:2"
            assert reply.causation_id == command.message_id
            assert reply.correlation_id == command.correlation_id
            serialized = repr([envelope.to_dict() for _, envelope in transport.published])
            assert "secret-ref:worker-api" in serialized
            assert "plaintext-secret-material" not in serialized
        finally:
            await _stop_endpoint(endpoint_task, transport)

    asyncio.run(scenario())


def test_lost_dispatch_reply_retries_same_worker_job_without_duplicate_execution() -> None:
    async def scenario() -> None:
        transport = _DropFirstDispatchReplyTransport()
        lifecycle = _Lifecycle()
        worker_id = new_id("worker")
        worker = LocalWorker(worker_id, lifecycle)
        endpoint = WorkerTransportEndpoint(worker, transport)
        endpoint_task = asyncio.create_task(endpoint.serve())
        await asyncio.sleep(0)
        client = TransportWorkerDispatcher(
            worker_id,
            transport,
            response_timeout_seconds=0.02,
        )
        job = replace(_job(), timeout_seconds=None, dispatch_attempt=1)

        try:
            with pytest.raises(RemoteWorkerTransportError) as lost:
                await client.dispatch(job)
            assert lost.value.category == "response_timeout"
            assert lost.value.retryable is True
            assert lifecycle.start_calls == 1

            handle = await client.dispatch(job)
            assert handle.run_id == job.execution.run_id
            assert lifecycle.start_calls == 1
            assert transport.dropped is True

            dispatch_commands = [
                envelope
                for _, envelope in transport.published
                if envelope.message_type == "worker.dispatch"
            ]
            assert len(dispatch_commands) == 2
            assert dispatch_commands[0].message_id != dispatch_commands[1].message_id
            assert dispatch_commands[0].idempotency_key == dispatch_commands[1].idempotency_key
        finally:
            await _stop_endpoint(endpoint_task, transport)

    asyncio.run(scenario())


def test_lost_terminal_result_reply_is_retried_without_reexecuting_job() -> None:
    async def scenario() -> None:
        transport = _DropFirstResultReplyTransport()
        lifecycle = _Lifecycle()
        worker_id = new_id("worker")
        worker = _ResultWorker(worker_id, lifecycle)
        endpoint = WorkerTransportEndpoint(worker, transport)
        endpoint_task = asyncio.create_task(endpoint.serve())
        await asyncio.sleep(0)
        client = TransportWorkerDispatcher(
            worker_id,
            transport,
            response_timeout_seconds=0.02,
        )
        job = replace(_job(), timeout_seconds=None, dispatch_attempt=1)

        try:
            await client.dispatch(job)
            assert lifecycle.start_calls == 1
            lifecycle.states[job.execution.run_id] = RunStatus.SUCCEEDED

            terminal = await client.get(job.worker_job_id)
            assert terminal.status is RunStatus.SUCCEEDED

            with pytest.raises(RemoteWorkerTransportError) as lost:
                await client.result(job.worker_job_id)
            assert lost.value.category == "response_timeout"
            assert lost.value.retryable is True
            assert transport.dropped is True
            assert lifecycle.start_calls == 1

            result = await client.result(job.worker_job_id)
            assert result is not None
            assert result.status.value == "succeeded"
            assert result.execution is not None
            assert result.execution.status is RunStatus.SUCCEEDED
            assert worker.output_artifact in result.artifact_refs
            assert result.evidence_refs == (worker.evidence_ref,)
            assert lifecycle.start_calls == 1

            result_commands = [
                envelope
                for _, envelope in transport.published
                if envelope.kind is MessageKind.COMMAND
                and envelope.message_type == "worker.result"
            ]
            assert len(result_commands) == 2
            assert result_commands[0].message_id != result_commands[1].message_id
            assert result_commands[0].idempotency_key == result_commands[1].idempotency_key

            dispatch_commands = [
                envelope
                for _, envelope in transport.published
                if envelope.kind is MessageKind.COMMAND
                and envelope.message_type == "worker.dispatch"
            ]
            assert len(dispatch_commands) == 1
        finally:
            await _stop_endpoint(endpoint_task, transport)

    asyncio.run(scenario())
