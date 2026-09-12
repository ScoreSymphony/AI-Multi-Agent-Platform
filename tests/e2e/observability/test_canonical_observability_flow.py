from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from ai_multi_agent_platform.contracts import (
    ModelRequest,
    OperationContext,
    ToolInvocation,
    WorkerDescriptor,
)
from ai_multi_agent_platform.contracts.types import ExecutionRequest
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.messaging import (
    InProcessMessageTransport,
    MessageKind,
    Subscription,
    TransportEnvelope,
)
from ai_multi_agent_platform.observability import (
    AccountingBridgeExporter,
    FailureComponent,
    InMemoryExporter,
    ObservedModelProvider,
    ObservedToolProvider,
    ObservedWorkerProvider,
    Telemetry,
    TelemetryContext,
    TraceHierarchy,
    extract_trace_carrier,
    inject_trace_carrier,
    observe_agent_run,
)
from ai_multi_agent_platform.testing import FakeModelProvider, FakeToolProvider, FakeWorkerProvider


@dataclass
class _MeasurementSink:
    records: list[object] = field(default_factory=list)

    def ingest_metric(self, record: object) -> None:
        self.records.append(record)


class _TransportingWorkerProvider(FakeWorkerProvider):
    def __init__(
        self,
        *,
        workers: tuple[WorkerDescriptor, ...],
        hierarchy: TraceHierarchy,
        transport: InProcessMessageTransport,
        topic: str,
    ) -> None:
        super().__init__(workers=workers)
        self._hierarchy = hierarchy
        self._transport = transport
        self._topic = topic

    async def dispatch(self, worker_id: str, request: ExecutionRequest):  # type: ignore[no-untyped-def]
        carrier = self._hierarchy.current_carrier()
        envelope = inject_trace_carrier(
            TransportEnvelope(
                message_type="worker.dispatch",
                kind=MessageKind.COMMAND,
                payload_schema_version="1.0",
                source_component="observability-reference",
                correlation_id=request.context.correlation_id,
                payload={"worker_id": worker_id},
            ),
            carrier,
        )
        await self._transport.publish(self._topic, envelope)
        return await super().dispatch(worker_id, request)


def test_final_end_to_end_trace_crosses_every_issue_16_layer() -> None:
    task_id = new_id("task")
    run_id = new_id("run")
    agent_id = new_id("agent")
    team_id = new_id("team")
    node_id = new_id("node")
    worker_id = new_id("worker")
    worker_job_id = new_id("worker_job")
    topic = "issue-16-final-worker-jobs"

    local_exporter = InMemoryExporter()
    measurement_sink = _MeasurementSink()
    local_telemetry = Telemetry(AccountingBridgeExporter(local_exporter, measurement_sink))
    local_hierarchy = TraceHierarchy(local_telemetry)
    remote_exporter = InMemoryExporter()
    remote_hierarchy = TraceHierarchy(Telemetry(remote_exporter))
    transport = InProcessMessageTransport()

    task_span = local_telemetry.start_span(
        "task.lifecycle",
        context=TelemetryContext(task_id=task_id, correlation_id=task_id),
    )
    local_telemetry.set_anchor("task", task_id, task_span)
    run_span = local_telemetry.start_span(
        "run.lifecycle",
        context=TelemetryContext(task_id=task_id, run_id=run_id, correlation_id=task_id),
        parent=task_span,
    )
    local_telemetry.set_anchor("run", run_id, run_span)

    model = ObservedModelProvider(FakeModelProvider(), local_telemetry, hierarchy=local_hierarchy)
    tool = ObservedToolProvider(FakeToolProvider(), local_telemetry, hierarchy=local_hierarchy)
    worker = ObservedWorkerProvider(
        _TransportingWorkerProvider(
            workers=(WorkerDescriptor(worker_id=worker_id, node_id=node_id),),
            hierarchy=local_hierarchy,
            transport=transport,
            topic=topic,
        ),
        local_telemetry,
        hierarchy=local_hierarchy,
    )
    operation_context = OperationContext(correlation_id=task_id, causation_id="agent-run")

    async def agent_operation() -> None:
        await model.generate(
            ModelRequest(
                request_id="model-call-final",
                messages=("PRIVATE_PROMPT",),
                context=operation_context,
            )
        )
        await tool.invoke(
            ToolInvocation(
                invocation_id="tool-call-final",
                tool_ref="example.tool",
                arguments={"secret": "PRIVATE_TOOL_INPUT"},
                context=operation_context,
            )
        )
        await worker.dispatch(
            worker_id,
            ExecutionRequest(
                run_id=run_id,
                subject_type="task",
                subject_id=task_id,
                context=operation_context,
            ),
        )

        subscription = transport.subscribe(
            Subscription(topic=topic, consumer_id="issue-16-final-worker")
        )
        delivery = await anext(subscription)
        carrier = extract_trace_carrier(delivery.envelope)
        assert carrier.agent_id == agent_id
        assert carrier.team_id == team_id
        assert carrier.worker_id == worker_id

        async def remote_execute() -> None:
            return None

        await remote_hierarchy.observe_remote(
            carrier=carrier,
            span_name="worker.node.execute",
            metric_prefix="platform.worker.job",
            event_prefix="worker.job",
            component=FailureComponent.SCHEDULER_WORKER_NODE,
            context=TelemetryContext(
                worker_job_id=worker_job_id,
                node_id=node_id,
                worker_id=worker_id,
                correlation_id=task_id,
            ),
            operation=remote_execute,
        )
        await transport.ack(delivery)
        await subscription.aclose()

    async def scenario() -> None:
        await observe_agent_run(
            local_hierarchy,
            agent_id=agent_id,
            context=TelemetryContext(
                task_id=task_id,
                run_id=run_id,
                agent_id=agent_id,
                team_id=team_id,
                correlation_id=task_id,
            ),
            operation=agent_operation,
        )

    asyncio.run(scenario())

    agent_span = next(span for span in local_exporter.spans if span.name == "agent.run")
    model_span = next(span for span in local_exporter.spans if span.name == "model.generate")
    tool_span = next(span for span in local_exporter.spans if span.name == "tool.invoke")
    dispatch_span = next(span for span in local_exporter.spans if span.name == "worker.dispatch")
    remote_span = next(span for span in remote_exporter.spans if span.name == "worker.node.execute")

    assert agent_span.trace_id == run_span.trace_id
    assert agent_span.parent_span_id == run_span.span_id
    for child in (model_span, tool_span, dispatch_span):
        assert child.trace_id == agent_span.trace_id
        assert child.parent_span_id == agent_span.span_id
        assert child.context.task_id == task_id
        assert child.context.run_id == run_id
        assert child.context.agent_id == agent_id
        assert child.context.team_id == team_id

    assert remote_span.trace_id == agent_span.trace_id
    assert remote_span.parent_span_id == dispatch_span.span_id
    assert remote_span.context.task_id == task_id
    assert remote_span.context.run_id == run_id
    assert remote_span.context.agent_id == agent_id
    assert remote_span.context.team_id == team_id
    assert remote_span.context.worker_job_id == worker_job_id
    assert remote_span.context.node_id == node_id
    assert remote_span.context.worker_id == worker_id

    assert measurement_sink.records
    serialized = repr(local_exporter.logs) + repr(remote_exporter.logs)
    assert "PRIVATE_PROMPT" not in serialized
    assert "PRIVATE_TOOL_INPUT" not in serialized
