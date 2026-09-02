from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ai_multi_agent_platform.capabilities import InvocationRecord, InvocationStatus, InvocationTrace
from ai_multi_agent_platform.contracts import (
    CapabilityKind,
    HealthStatus,
    ModelRequest,
    ModelResponse,
    OperationContext,
    ProviderContract,
    ProviderDescriptor,
    ToolInvocation,
    WorkerDescriptor,
)
from ai_multi_agent_platform.contracts.types import ExecutionRequest
from ai_multi_agent_platform.control_plane import (
    ActorContext,
    ControlPlane,
    PageQuery,
    RequestContext,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.messaging import (
    InProcessMessageTransport,
    MessageKind,
    Subscription,
    TransportEnvelope,
)
from ai_multi_agent_platform.observability import (
    AccountingBridgeExporter,
    AggregatedHealthProvider,
    FailureComponent,
    InMemoryExporter,
    ObservabilityInvocationObserver,
    ObservedModelProvider,
    ObservedToolProvider,
    ObservedWorkerProvider,
    ProviderHealthDependency,
    Telemetry,
    TelemetryContext,
    TraceHierarchy,
    extract_trace_carrier,
    inject_trace_carrier,
    observe_agent_run,
)
from ai_multi_agent_platform.testing import (
    FakeLifecycleBackend,
    FakeModelProvider,
    FakeOrchestrator,
    FakeToolProvider,
    FakeWorkerProvider,
)


class _UsageModelProvider(FakeModelProvider):
    async def generate(self, request: ModelRequest) -> ModelResponse:
        response = await super().generate(request)
        return ModelResponse(
            request_id=response.request_id,
            text=response.text,
            model_ref=response.model_ref,
            usage={
                "input_tokens": 4,
                "output_tokens": 2,
                "provider_note": "not-a-number",
            },
        )


@dataclass
class _RecordingMeasurementSink:
    records: list[object]

    def ingest_metric(self, record: object) -> None:
        self.records.append(record)


class _HealthProvider(ProviderContract):
    def __init__(self, provider_id: str, status: HealthStatus) -> None:
        self._provider_id = provider_id
        self._status = status

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id=self._provider_id,
            provider_type="test-health",
            health=self._status,
            available=True,
        )

    async def health(self) -> HealthStatus:
        return self._status


def _request_context() -> RequestContext:
    return RequestContext(
        request_id="request-observability",
        correlation_id="request-observability",
        actor=ActorContext(
            principal_ref="user:observability",
            owner_type="user",
            owner_id="observability",
        ),
    )


def _kernel_stack(
    *,
    health_providers: tuple[ProviderContract, ...] = (),
) -> tuple[ControlPlane, PlatformKernel, InMemoryKernelRepository]:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    control = ControlPlane(
        kernel=kernel,
        events=repository,
        health_providers=health_providers,
    )
    return control, kernel, repository


def test_agent_model_tool_and_worker_attach_to_one_canonical_trace() -> None:
    task_id = new_id("task")
    run_id = new_id("run")
    agent_id = new_id("agent")
    node_id = new_id("node")
    worker_id = new_id("worker")
    exporter = InMemoryExporter()
    telemetry = Telemetry(exporter)
    task_context = TelemetryContext(task_id=task_id, correlation_id=task_id)
    task_span = telemetry.start_span("task.lifecycle", context=task_context)
    telemetry.set_anchor("task", task_id, task_span)
    run_context = TelemetryContext(task_id=task_id, run_id=run_id, correlation_id=task_id)
    run_span = telemetry.start_span("run.lifecycle", context=run_context, parent=task_span)
    telemetry.set_anchor("run", run_id, run_span)
    hierarchy = TraceHierarchy(telemetry)

    model = ObservedModelProvider(FakeModelProvider(), telemetry, hierarchy=hierarchy)
    tool = ObservedToolProvider(FakeToolProvider(), telemetry, hierarchy=hierarchy)
    worker = ObservedWorkerProvider(
        FakeWorkerProvider(
            workers=(WorkerDescriptor(worker_id=worker_id, node_id=node_id),),
        ),
        telemetry,
        hierarchy=hierarchy,
    )
    operation_context = OperationContext(correlation_id=task_id, causation_id="agent-run")

    async def agent_operation() -> None:
        await model.generate(
            ModelRequest(
                request_id="model-call-16",
                messages=("PRIVATE_PROMPT",),
                context=operation_context,
            )
        )
        await tool.invoke(
            ToolInvocation(
                invocation_id="tool-call-16",
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

    async def scenario() -> None:
        await observe_agent_run(
            hierarchy,
            agent_id=agent_id,
            context=TelemetryContext(
                task_id=task_id,
                run_id=run_id,
                correlation_id=task_id,
            ),
            operation=agent_operation,
        )

    asyncio.run(scenario())

    agent_span = next(span for span in exporter.spans if span.name == "agent.run")
    model_span = next(span for span in exporter.spans if span.name == "model.generate")
    tool_span = next(span for span in exporter.spans if span.name == "tool.invoke")
    worker_span = next(span for span in exporter.spans if span.name == "worker.dispatch")
    assert agent_span.trace_id == run_span.trace_id
    assert agent_span.parent_span_id == run_span.span_id
    for child in (model_span, tool_span, worker_span):
        assert child.trace_id == agent_span.trace_id
        assert child.parent_span_id == agent_span.span_id
        assert child.context.task_id == task_id
        assert child.context.run_id == run_id
        assert child.context.agent_id == agent_id
    serialized = repr(exporter.logs)
    assert "PRIVATE_PROMPT" not in serialized
    assert "PRIVATE_TOOL_INPUT" not in serialized


def test_model_usage_metrics_emit_only_reported_numeric_measurements() -> None:
    task_id = new_id("task")
    exporter = InMemoryExporter()
    telemetry = Telemetry(exporter)
    hierarchy = TraceHierarchy(telemetry)
    model = ObservedModelProvider(_UsageModelProvider(), telemetry, hierarchy=hierarchy)

    async def scenario() -> None:
        await model.generate(
            ModelRequest(
                request_id="usage-call",
                messages=("private",),
                context=OperationContext(correlation_id=task_id),
            )
        )

    asyncio.run(scenario())
    usage = [metric for metric in exporter.metrics if metric.name == "platform.model.usage"]
    assert len(usage) == 2
    assert {metric.attributes["usage_key"] for metric in usage} == {
        "input_tokens",
        "output_tokens",
    }
    assert all(metric.unit == "tokens" for metric in usage)


def test_capability_policy_and_approval_outcomes_are_observable() -> None:
    task_id = new_id("task")
    run_id = new_id("run")
    agent_id = new_id("agent")
    trace = InvocationTrace(
        correlation_id=task_id,
        task_id=task_id,
        run_id=run_id,
        agent_id=agent_id,
    )
    exporter = InMemoryExporter()
    observer = ObservabilityInvocationObserver(Telemetry(exporter))

    def record(
        invocation_id: str,
        status: InvocationStatus,
        *,
        approval_decision: str | None = None,
        error_code: str | None = None,
    ) -> InvocationRecord:
        return InvocationRecord(
            invocation_id=invocation_id,
            capability_id="tool.echo",
            capability_version="1.0",
            provider_id="native",
            provider_tool_ref="echo",
            status=status,
            trace=trace,
            approval_decision=approval_decision,
            error_code=error_code,
        )

    async def scenario() -> None:
        await observer.record(record("denied", InvocationStatus.DENIED, error_code="forbidden"))
        await observer.record(record("approval", InvocationStatus.APPROVAL_REQUIRED))
        await observer.record(
            record("approved", InvocationStatus.RUNNING, approval_decision="approved")
        )
        await observer.record(
            record("approved", InvocationStatus.SUCCEEDED, approval_decision="approved")
        )

    asyncio.run(scenario())
    metric_names = {metric.name for metric in exporter.metrics}
    assert "platform.tool.denied" in metric_names
    assert "platform.tool.approval_required" in metric_names
    assert "platform.tool.approved" in metric_names
    names = {entry.event_name for entry in exporter.timeline}
    assert "capability.invocation.denied" in names
    assert "capability.invocation.approval_required" in names
    denied = next(entry for entry in exporter.timeline if entry.event_name.endswith("denied"))
    assert denied.failure is not None
    assert denied.failure.component is FailureComponent.CAPABILITY_TOOL
    assert denied.context.agent_id == agent_id


def test_trace_context_crosses_actual_message_transport_boundary() -> None:
    task_id = new_id("task")
    run_id = new_id("run")
    node_id = new_id("node")
    worker_id = new_id("worker")
    local_exporter = InMemoryExporter()
    local_telemetry = Telemetry(local_exporter)
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
    local_hierarchy = TraceHierarchy(local_telemetry)
    remote_exporter = InMemoryExporter()
    remote_hierarchy = TraceHierarchy(Telemetry(remote_exporter))
    transport = InProcessMessageTransport()

    async def scenario() -> None:
        async def dispatch() -> None:
            carrier = local_hierarchy.current_carrier()
            envelope = inject_trace_carrier(
                TransportEnvelope(
                    message_type="worker.dispatch",
                    kind=MessageKind.COMMAND,
                    payload_schema_version="1.0",
                    source_component="scheduler",
                    correlation_id=task_id,
                    payload={"worker_id": worker_id},
                ),
                carrier,
            )
            await transport.publish("worker-jobs", envelope)

        await local_hierarchy.observe(
            span_name="worker.dispatch",
            metric_prefix="platform.worker.dispatch",
            event_prefix="worker.dispatch",
            component=FailureComponent.SCHEDULER_WORKER_NODE,
            context=TelemetryContext(
                task_id=task_id,
                run_id=run_id,
                worker_id=worker_id,
                correlation_id=task_id,
            ),
            operation=dispatch,
        )
        subscription = transport.subscribe(
            Subscription(topic="worker-jobs", consumer_id="worker-test")
        )
        delivery = await anext(subscription)
        carrier = extract_trace_carrier(delivery.envelope)

        async def remote_execute() -> None:
            return None

        await remote_hierarchy.observe_remote(
            carrier=carrier,
            span_name="worker.node.execute",
            metric_prefix="platform.worker.job",
            event_prefix="worker.job",
            component=FailureComponent.SCHEDULER_WORKER_NODE,
            context=TelemetryContext(
                task_id=task_id,
                run_id=run_id,
                node_id=node_id,
                worker_id=worker_id,
                correlation_id=task_id,
            ),
            operation=remote_execute,
        )
        await transport.ack(delivery)
        await subscription.aclose()

    asyncio.run(scenario())
    dispatch_span = next(span for span in local_exporter.spans if span.name == "worker.dispatch")
    remote_span = next(span for span in remote_exporter.spans if span.name == "worker.node.execute")
    assert remote_span.trace_id == dispatch_span.trace_id
    assert remote_span.parent_span_id == dispatch_span.span_id
    assert remote_span.context.task_id == task_id
    assert remote_span.context.run_id == run_id
    assert remote_span.context.worker_id == worker_id
    assert remote_span.context.node_id == node_id


def test_optional_degradation_stays_ready_and_required_failure_does_not() -> None:
    async def scenario() -> None:
        optional = AggregatedHealthProvider(
            (
                ProviderHealthDependency(
                    _HealthProvider("optional", HealthStatus.UNAVAILABLE),
                    required=False,
                ),
            )
        )
        optional_control, _, _ = _kernel_stack(health_providers=(optional,))
        optional_health = await optional_control.health()
        assert optional_health["ready"] is True
        providers = optional_health["providers"]
        assert isinstance(providers, list)
        assert isinstance(providers[0], dict)
        assert providers[0]["status"] == "degraded"

        required = AggregatedHealthProvider(
            (
                ProviderHealthDependency(
                    _HealthProvider("required", HealthStatus.UNAVAILABLE),
                    required=True,
                ),
            )
        )
        required_control, _, _ = _kernel_stack(health_providers=(required,))
        required_health = await required_control.health()
        assert required_health["ready"] is False

    asyncio.run(scenario())


def test_control_plane_timeline_is_enriched_without_private_backend_queries() -> None:
    async def scenario() -> None:
        control, kernel, _ = _kernel_stack()
        task = await kernel.create_task(
            idempotency_key="issue-16-timeline",
            title="Observable",
            objective="Expose canonical timeline",
            owner_type="user",
            owner_id="observability",
        )
        exporter = InMemoryExporter()
        telemetry = Telemetry(exporter)
        telemetry.timeline(
            event_name="executor.completed",
            component=FailureComponent.EXECUTION,
            context=TelemetryContext(
                task_id=task.task_id,
                correlation_id=task.task_id,
            ),
        )
        control.bind_observability_timeline(exporter)
        page = await control.timeline(_request_context(), task.task_id, PageQuery())
        items = page["items"]
        assert isinstance(items, list)
        assert any(isinstance(item, dict) and item.get("type") == "event" for item in items)
        telemetry_items = [
            item for item in items if isinstance(item, dict) and item.get("type") == "telemetry"
        ]
        assert len(telemetry_items) == 1
        assert telemetry_items[0]["event_name"] == "executor.completed"
        assert telemetry_items[0]["component"] == "execution"

    asyncio.run(scenario())


def test_accounting_bridge_forwards_measurements_without_owning_accounting_state() -> None:
    delegate = InMemoryExporter()
    sink = _RecordingMeasurementSink(records=[])
    bridge = AccountingBridgeExporter(delegate, sink)
    telemetry = Telemetry(bridge)
    telemetry.metric(
        "platform.executor.duration_seconds",
        1.25,
        context=TelemetryContext(task_id=new_id("task"), correlation_id="usage-correlation"),
        unit="seconds",
    )
    assert len(delegate.metrics) == 1
    assert sink.records == [delegate.metrics[0]]
    assert not hasattr(bridge, "budgets")
    assert not hasattr(bridge, "costs")
