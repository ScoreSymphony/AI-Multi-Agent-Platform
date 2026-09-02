"""Progressive observability adapters for later canonical platform domains."""

from __future__ import annotations

import asyncio
from datetime import datetime

from ai_multi_agent_platform.capabilities import (
    InvocationObserver,
    InvocationRecord,
    InvocationStatus,
)
from ai_multi_agent_platform.contracts import (
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelRouter,
    ModelSelection,
    NodeDescriptor,
    NodeProvider,
    OperationContext,
    Orchestrator,
    PlanRequest,
    PlanResponse,
    ProviderDescriptor,
    ToolInvocation,
    ToolProvider,
    ToolResult,
    WorkerDescriptor,
    WorkerProvider,
)
from ai_multi_agent_platform.contracts.types import ExecutionHandle, ExecutionRequest, JsonValue

from .exporters import Telemetry
from .hierarchy import TraceHierarchy
from .models import (
    FailureClassification,
    FailureComponent,
    TelemetryContext,
    TelemetryOutcome,
    TelemetrySeverity,
)


class ObservedOrchestrator(Orchestrator):
    """Attach orchestration activity beneath the current Task/Run trace."""

    def __init__(
        self,
        orchestrator: Orchestrator,
        telemetry: Telemetry,
        *,
        hierarchy: TraceHierarchy | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._telemetry = telemetry
        self.hierarchy = hierarchy or TraceHierarchy(telemetry)

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._orchestrator.descriptor

    async def plan(self, request: PlanRequest) -> PlanResponse:
        context = TelemetryContext(
            project_id=request.context.project_id,
            task_id=request.task_id,
            correlation_id=request.context.correlation_id,
            causation_id=request.context.causation_id,
            provider_id=self.descriptor.provider_id,
        )

        async def operation() -> PlanResponse:
            return await self._orchestrator.plan(request)

        return await self.hierarchy.observe(
            span_name="orchestration.plan",
            metric_prefix="platform.orchestration",
            event_prefix="orchestration",
            component=FailureComponent.ORCHESTRATION,
            context=context,
            operation=operation,
            attributes={"provider_id": self.descriptor.provider_id},
        )


class ObservedModelRouter(ModelRouter):
    """Instrument deterministic/provider-neutral model routing decisions."""

    def __init__(
        self,
        router: ModelRouter,
        telemetry: Telemetry,
        *,
        hierarchy: TraceHierarchy | None = None,
    ) -> None:
        self._router = router
        self._telemetry = telemetry
        self.hierarchy = hierarchy or TraceHierarchy(telemetry)

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._router.descriptor

    async def select_provider(self, request: ModelRequest) -> ModelSelection:
        context = _context_from_operation(
            request.context,
            model_call_id=request.request_id,
            provider_id=self.descriptor.provider_id,
        )

        async def operation() -> ModelSelection:
            return await self._router.select_provider(request)

        selection = await self.hierarchy.observe(
            span_name="model.route",
            metric_prefix="platform.model.route",
            event_prefix="model.route",
            component=FailureComponent.MODEL_PROVIDER_ROUTER,
            context=context,
            operation=operation,
            attributes={"router_id": self.descriptor.provider_id},
        )
        self._telemetry.metric(
            "platform.model.route.selections",
            1.0,
            context=context,
            attributes={
                "provider_id": selection.provider_id,
                "model_ref": selection.model_ref,
            },
        )
        if _selection_reports_fallback(selection):
            self._telemetry.metric(
                "platform.model.route.fallbacks",
                1.0,
                context=context,
                attributes={"provider_id": selection.provider_id},
            )
        return selection


class ObservedModelProvider(ModelProvider):
    """Instrument model calls without capturing prompts or responses by default."""

    def __init__(
        self,
        provider: ModelProvider,
        telemetry: Telemetry,
        *,
        hierarchy: TraceHierarchy | None = None,
    ) -> None:
        self._provider = provider
        self._telemetry = telemetry
        self.hierarchy = hierarchy or TraceHierarchy(telemetry)

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._provider.descriptor

    async def generate(self, request: ModelRequest) -> ModelResponse:
        context = _context_from_operation(
            request.context,
            model_call_id=request.request_id,
            model_provider_id=self.descriptor.provider_id,
            provider_id=self.descriptor.provider_id,
        )

        async def operation() -> ModelResponse:
            return await self._provider.generate(request)

        response = await self.hierarchy.observe(
            span_name="model.generate",
            metric_prefix="platform.model",
            event_prefix="model",
            component=FailureComponent.MODEL_PROVIDER_ROUTER,
            context=context,
            operation=operation,
            attributes={"provider_id": self.descriptor.provider_id},
        )
        for usage_key, value in response.usage.items():
            if isinstance(value, bool) or not isinstance(value, int | float):
                continue
            unit = "tokens" if "token" in usage_key.casefold() else "provider_units"
            self._telemetry.metric(
                "platform.model.usage",
                float(value),
                context=context,
                unit=unit,
                attributes={"usage_key": usage_key, "model_ref": response.model_ref},
            )
        return response


class ObservedToolProvider(ToolProvider):
    """Instrument provider tool execution without recording tool input/output bodies."""

    def __init__(
        self,
        provider: ToolProvider,
        telemetry: Telemetry,
        *,
        hierarchy: TraceHierarchy | None = None,
    ) -> None:
        self._provider = provider
        self._telemetry = telemetry
        self.hierarchy = hierarchy or TraceHierarchy(telemetry)

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._provider.descriptor

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        context = _context_from_operation(
            invocation.context,
            tool_invocation_id=invocation.invocation_id,
            provider_id=self.descriptor.provider_id,
        )

        async def operation() -> ToolResult:
            return await self._provider.invoke(invocation)

        return await self.hierarchy.observe(
            span_name="tool.invoke",
            metric_prefix="platform.tool",
            event_prefix="tool",
            component=FailureComponent.CAPABILITY_TOOL,
            context=context,
            operation=operation,
            attributes={
                "tool_ref": invocation.tool_ref,
                "provider_id": self.descriptor.provider_id,
            },
        )


class ObservedNodeProvider(NodeProvider):
    """Instrument node inventory/health data that #14 providers actually report."""

    def __init__(
        self,
        provider: NodeProvider,
        telemetry: Telemetry,
        *,
        hierarchy: TraceHierarchy | None = None,
    ) -> None:
        self._provider = provider
        self._telemetry = telemetry
        self.hierarchy = hierarchy or TraceHierarchy(telemetry)

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._provider.descriptor

    async def register_node(
        self,
        node: NodeDescriptor,
        context: OperationContext,
    ) -> NodeDescriptor:
        telemetry_context = _context_from_operation(
            context,
            node_id=node.node_id,
            provider_id=self.descriptor.provider_id,
        )

        async def operation() -> NodeDescriptor:
            return await self._provider.register_node(node, context)

        registered = await self.hierarchy.observe(
            span_name="node.register",
            metric_prefix="platform.node.register",
            event_prefix="node.register",
            component=FailureComponent.SCHEDULER_WORKER_NODE,
            context=telemetry_context,
            operation=operation,
            attributes={"node_id": node.node_id},
        )
        _emit_reported_metadata(
            self._telemetry,
            telemetry_context,
            "platform.node.reported_resource",
            registered.metadata,
        )
        return registered

    async def list_nodes(self, context: OperationContext) -> tuple[NodeDescriptor, ...]:
        telemetry_context = _context_from_operation(
            context,
            provider_id=self.descriptor.provider_id,
        )

        async def operation() -> tuple[NodeDescriptor, ...]:
            return await self._provider.list_nodes(context)

        nodes = await self.hierarchy.observe(
            span_name="node.list",
            metric_prefix="platform.node.list",
            event_prefix="node.list",
            component=FailureComponent.SCHEDULER_WORKER_NODE,
            context=telemetry_context,
            operation=operation,
        )
        for node in nodes:
            node_context = _context_from_operation(
                context,
                node_id=node.node_id,
                provider_id=self.descriptor.provider_id,
            )
            self._telemetry.metric("platform.node.inventory", 1.0, context=node_context)
            _emit_reported_metadata(
                self._telemetry,
                node_context,
                "platform.node.reported_resource",
                node.metadata,
            )
        return nodes


class ObservedWorkerProvider(WorkerProvider):
    """Instrument worker registration, inventory and dispatch through the canonical seam."""

    def __init__(
        self,
        provider: WorkerProvider,
        telemetry: Telemetry,
        *,
        hierarchy: TraceHierarchy | None = None,
    ) -> None:
        self._provider = provider
        self._telemetry = telemetry
        self.hierarchy = hierarchy or TraceHierarchy(telemetry)

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._provider.descriptor

    async def register_worker(
        self,
        worker: WorkerDescriptor,
        context: OperationContext,
    ) -> WorkerDescriptor:
        telemetry_context = _context_from_operation(
            context,
            worker_id=worker.worker_id,
            node_id=worker.node_id,
            provider_id=self.descriptor.provider_id,
        )

        async def operation() -> WorkerDescriptor:
            return await self._provider.register_worker(worker, context)

        registered = await self.hierarchy.observe(
            span_name="worker.register",
            metric_prefix="platform.worker.register",
            event_prefix="worker.register",
            component=FailureComponent.SCHEDULER_WORKER_NODE,
            context=telemetry_context,
            operation=operation,
            attributes={"worker_id": worker.worker_id, "node_id": worker.node_id},
        )
        _emit_reported_metadata(
            self._telemetry,
            telemetry_context,
            "platform.worker.reported_resource",
            registered.metadata,
        )
        return registered

    async def list_workers(self, context: OperationContext) -> tuple[WorkerDescriptor, ...]:
        telemetry_context = _context_from_operation(
            context,
            provider_id=self.descriptor.provider_id,
        )

        async def operation() -> tuple[WorkerDescriptor, ...]:
            return await self._provider.list_workers(context)

        workers = await self.hierarchy.observe(
            span_name="worker.list",
            metric_prefix="platform.worker.list",
            event_prefix="worker.list",
            component=FailureComponent.SCHEDULER_WORKER_NODE,
            context=telemetry_context,
            operation=operation,
        )
        for worker in workers:
            worker_context = _context_from_operation(
                context,
                worker_id=worker.worker_id,
                node_id=worker.node_id,
                provider_id=self.descriptor.provider_id,
            )
            self._telemetry.metric(
                "platform.worker.inventory",
                1.0,
                context=worker_context,
                attributes={"available": worker.available},
            )
            _emit_reported_metadata(
                self._telemetry,
                worker_context,
                "platform.worker.reported_resource",
                worker.metadata,
            )
        return workers

    async def dispatch(self, worker_id: str, request: ExecutionRequest) -> ExecutionHandle:
        task_id = request.subject_id if request.subject_type == "task" else None
        if task_id is None and request.context.correlation_id.startswith("task_"):
            task_id = request.context.correlation_id
        telemetry_context = _context_from_operation(
            request.context,
            task_id=task_id,
            run_id=request.run_id,
            step_id=request.subject_id if request.subject_type == "step" else None,
            worker_id=worker_id,
            provider_id=self.descriptor.provider_id,
        )

        async def operation() -> ExecutionHandle:
            return await self._provider.dispatch(worker_id, request)

        return await self.hierarchy.observe(
            span_name="worker.dispatch",
            metric_prefix="platform.worker.dispatch",
            event_prefix="worker.dispatch",
            component=FailureComponent.SCHEDULER_WORKER_NODE,
            context=telemetry_context,
            operation=operation,
            attributes={"worker_id": worker_id},
        )


class ObservabilityInvocationObserver(InvocationObserver):
    """Convert canonical capability policy/approval outcomes into safe telemetry."""

    def __init__(self, telemetry: Telemetry) -> None:
        self._telemetry = telemetry
        self._running_at: dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    async def record(self, record: InvocationRecord) -> None:
        context = TelemetryContext(
            project_id=record.trace.project_id,
            task_id=record.trace.task_id,
            run_id=record.trace.run_id,
            agent_id=record.trace.agent_id,
            tool_invocation_id=record.invocation_id,
            capability_id=record.capability_id,
            node_id=record.node_id,
            worker_id=record.worker_id,
            correlation_id=record.trace.correlation_id,
            causation_id=record.trace.causation_id,
            provider_id=record.provider_id,
        )
        duration = await self._duration(record)
        outcome = _invocation_outcome(record.status)
        failure = _invocation_failure(record)
        attributes: dict[str, JsonValue] = {
            "capability_id": record.capability_id,
            "capability_version": record.capability_version,
            "provider_id": record.provider_id,
            "approval_decision": record.approval_decision,
        }
        if record.status is InvocationStatus.RUNNING:
            self._telemetry.metric("platform.tool.calls", 1.0, context=context)
            if record.approval_decision == "approved":
                self._telemetry.metric("platform.tool.approved", 1.0, context=context)
        elif record.status is InvocationStatus.DENIED:
            self._telemetry.metric("platform.tool.denied", 1.0, context=context)
        elif record.status is InvocationStatus.APPROVAL_REQUIRED:
            self._telemetry.metric("platform.tool.approval_required", 1.0, context=context)
        elif record.status in {
            InvocationStatus.FAILED,
            InvocationStatus.TIMED_OUT,
        }:
            self._telemetry.metric("platform.tool.failures", 1.0, context=context)

        if duration is not None:
            self._telemetry.metric(
                "platform.tool.duration_seconds",
                duration,
                context=context,
                unit="seconds",
                attributes={"outcome": outcome.value},
            )

        severity = (
            TelemetrySeverity.ERROR
            if record.status
            in {InvocationStatus.DENIED, InvocationStatus.FAILED, InvocationStatus.TIMED_OUT}
            else TelemetrySeverity.INFO
        )
        event_name = f"capability.invocation.{record.status.value}"
        self._telemetry.log(
            severity=severity,
            component=FailureComponent.CAPABILITY_TOOL,
            event_name=event_name,
            context=context,
            outcome=outcome,
            failure=failure,
            duration_seconds=duration,
            attributes=attributes,
        )
        self._telemetry.timeline(
            event_name=event_name,
            component=FailureComponent.CAPABILITY_TOOL,
            context=context,
            timestamp=record.recorded_at,
            outcome=outcome,
            failure=failure,
            duration_seconds=duration,
            attributes=attributes,
        )

    async def _duration(self, record: InvocationRecord) -> float | None:
        async with self._lock:
            if record.status is InvocationStatus.RUNNING:
                self._running_at[record.invocation_id] = record.recorded_at
                return None
            if record.status not in {
                InvocationStatus.SUCCEEDED,
                InvocationStatus.FAILED,
                InvocationStatus.CANCELLED,
                InvocationStatus.TIMED_OUT,
            }:
                return None
            started = self._running_at.pop(record.invocation_id, None)
        if started is None:
            return None
        return max(0.0, (record.recorded_at - started).total_seconds())


class CompositeInvocationObserver(InvocationObserver):
    """Fan out one canonical invocation record to audit and telemetry consumers."""

    def __init__(self, *observers: InvocationObserver) -> None:
        self._observers = observers

    async def record(self, record: InvocationRecord) -> None:
        for observer in self._observers:
            await observer.record(record)


def _context_from_operation(
    context: OperationContext,
    *,
    task_id: str | None = None,
    run_id: str | None = None,
    step_id: str | None = None,
    model_call_id: str | None = None,
    model_provider_id: str | None = None,
    tool_invocation_id: str | None = None,
    node_id: str | None = None,
    worker_id: str | None = None,
    provider_id: str | None = None,
) -> TelemetryContext:
    inferred_task_id = task_id
    if inferred_task_id is None and context.correlation_id.startswith("task_"):
        inferred_task_id = context.correlation_id
    return TelemetryContext(
        project_id=context.project_id,
        task_id=inferred_task_id,
        run_id=run_id,
        step_id=step_id,
        model_call_id=model_call_id,
        model_provider_id=model_provider_id,
        tool_invocation_id=tool_invocation_id,
        node_id=node_id,
        worker_id=worker_id,
        correlation_id=context.correlation_id,
        causation_id=context.causation_id,
        provider_id=provider_id,
    )


def _selection_reports_fallback(selection: ModelSelection) -> bool:
    for metadata in selection.adapter_metadata:
        for key in ("fallback", "used_fallback"):
            value = metadata.values.get(key)
            if value is True:
                return True
    return False


def _emit_reported_metadata(
    telemetry: Telemetry,
    context: TelemetryContext,
    metric_name: str,
    metadata: dict[str, JsonValue],
) -> None:
    for key, value in metadata.items():
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        telemetry.metric(
            metric_name,
            float(value),
            context=context,
            unit="reported_units",
            attributes={"resource_key": key},
        )


def _invocation_outcome(status: InvocationStatus) -> TelemetryOutcome:
    return {
        InvocationStatus.SUCCEEDED: TelemetryOutcome.SUCCEEDED,
        InvocationStatus.FAILED: TelemetryOutcome.FAILED,
        InvocationStatus.CANCELLED: TelemetryOutcome.CANCELLED,
        InvocationStatus.TIMED_OUT: TelemetryOutcome.TIMED_OUT,
        InvocationStatus.DENIED: TelemetryOutcome.FAILED,
    }.get(status, TelemetryOutcome.UNKNOWN)


def _invocation_failure(record: InvocationRecord) -> FailureClassification | None:
    if record.status not in {
        InvocationStatus.FAILED,
        InvocationStatus.TIMED_OUT,
        InvocationStatus.DENIED,
    }:
        return None
    return FailureClassification(
        component=FailureComponent.CAPABILITY_TOOL,
        code=record.error_code or record.status.value,
        retryable=record.status is InvocationStatus.TIMED_OUT,
    )
