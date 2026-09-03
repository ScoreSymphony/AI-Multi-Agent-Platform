"""#14 scheduler, lease and liveness telemetry over the existing #16 facade."""

from __future__ import annotations

from datetime import datetime

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.observability import (
    FailureClassification,
    FailureComponent,
    Telemetry,
    TelemetryContext,
    TelemetryOutcome,
    TelemetrySeverity,
)

from .models import (
    NodeRecord,
    Reservation,
    SchedulingDecision,
    WorkerJobRequest,
    WorkerRecord,
)
from .runtime_types import DistributedDispatchState


class DistributedTelemetry:
    """Emit #14-owned scheduler/Worker/Node facts through #16 telemetry contracts.

    This class owns no exporter, storage or trace backend. It intentionally records only
    operational metadata and canonical references; job inputs and secret references are
    never copied into telemetry.
    """

    def __init__(self, telemetry: Telemetry) -> None:
        self.telemetry = telemetry

    def scheduling_decision(
        self,
        job: WorkerJobRequest,
        decision: SchedulingDecision,
    ) -> None:
        context = _job_context(job)
        for evaluation in decision.evaluations:
            candidate_context = _job_context(
                job,
                node_id=evaluation.node_id,
                worker_id=evaluation.worker_id,
            )
            self.telemetry.metric(
                "platform.scheduler.candidates",
                1.0,
                context=candidate_context,
                attributes={
                    "accepted": evaluation.accepted,
                    "score": evaluation.score,
                },
            )
            for reason in evaluation.reasons:
                self.telemetry.metric(
                    "platform.scheduler.rejections",
                    1.0,
                    context=candidate_context,
                    attributes={"reason_code": reason.code.value},
                )
        self.telemetry.timeline(
            event_name="scheduler.decision",
            component=FailureComponent.SCHEDULER_WORKER_NODE,
            context=context,
            outcome=(
                TelemetryOutcome.SUCCEEDED
                if decision.selected_worker_id is not None
                else TelemetryOutcome.FAILED
            ),
            attributes={
                "selected_worker_id": decision.selected_worker_id,
                "candidate_count": len(decision.evaluations),
                "accepted_count": sum(item.accepted for item in decision.evaluations),
            },
        )

    def reservation(self, job: WorkerJobRequest, reservation: Reservation, *, event: str) -> None:
        context = _job_context(
            job,
            node_id=reservation.node_id,
            worker_id=reservation.worker_id,
        )
        attributes: dict[str, JsonValue] = {
            "reservation_id": reservation.reservation_id,
            "reservation_status": reservation.status.value,
            "event": event,
            "concurrency_units": reservation.concurrency_units,
            "accelerator_id": reservation.accelerator_id,
        }
        self.telemetry.metric(
            "platform.scheduler.reservations",
            1.0,
            context=context,
            attributes=attributes,
        )
        for name, value, unit in (
            ("platform.scheduler.reserved_cpu_cores", reservation.cpu_cores, "cores"),
            ("platform.scheduler.reserved_ram_bytes", reservation.ram_bytes, "bytes"),
            ("platform.scheduler.reserved_storage_bytes", reservation.storage_bytes, "bytes"),
            ("platform.scheduler.reserved_vram_bytes", reservation.vram_bytes, "bytes"),
        ):
            self.telemetry.metric(
                name,
                float(value),
                context=context,
                unit=unit,
                attributes={"reservation_id": reservation.reservation_id, "event": event},
            )

    def dispatch(
        self,
        job: WorkerJobRequest,
        *,
        node_id: str,
        worker_id: str,
        duration_seconds: float,
        succeeded: bool,
        failure_code: str | None = None,
    ) -> None:
        context = _job_context(job, node_id=node_id, worker_id=worker_id)
        outcome = TelemetryOutcome.SUCCEEDED if succeeded else TelemetryOutcome.FAILED
        failure = (
            None
            if failure_code is None
            else FailureClassification(
                component=FailureComponent.SCHEDULER_WORKER_NODE,
                code=failure_code,
                retryable=True,
            )
        )
        self.telemetry.metric(
            "platform.worker.dispatch.duration_seconds",
            duration_seconds,
            context=context,
            unit="seconds",
            attributes={"outcome": outcome.value},
        )
        if not succeeded:
            self.telemetry.metric(
                "platform.worker.dispatch.failures",
                1.0,
                context=context,
                attributes={"failure_code": failure_code or "unknown"},
            )
        self.telemetry.log(
            severity=TelemetrySeverity.INFO if succeeded else TelemetrySeverity.ERROR,
            component=FailureComponent.SCHEDULER_WORKER_NODE,
            event_name="worker.dispatch.completed" if succeeded else "worker.dispatch.failed",
            context=context,
            outcome=outcome,
            failure=failure,
            duration_seconds=duration_seconds,
            attributes={"worker_job_id": job.worker_job_id},
        )

    def heartbeat(
        self,
        node: NodeRecord,
        workers: tuple[WorkerRecord, ...],
        *,
        observed_at: datetime,
    ) -> None:
        node_context = TelemetryContext(node_id=node.node_id)
        self.telemetry.metric(
            "platform.node.heartbeats",
            1.0,
            context=node_context,
            attributes={"status": node.status.value},
        )
        self._node_resources(node, context=node_context)
        for worker in workers:
            worker_context = TelemetryContext(node_id=node.node_id, worker_id=worker.worker_id)
            self.telemetry.metric(
                "platform.worker.heartbeats",
                1.0,
                context=worker_context,
                attributes={
                    "status": worker.status.value,
                    "draining": worker.draining,
                },
            )
            self.telemetry.metric(
                "platform.worker.active_jobs",
                float(worker.active_jobs),
                context=worker_context,
            )
        self.telemetry.timeline(
            event_name="node.heartbeat",
            component=FailureComponent.SCHEDULER_WORKER_NODE,
            context=node_context,
            timestamp=observed_at,
            outcome=TelemetryOutcome.SUCCEEDED,
            attributes={"worker_count": len(workers), "status": node.status.value},
        )

    def liveness(
        self,
        nodes: tuple[NodeRecord, ...],
        workers: tuple[WorkerRecord, ...],
        *,
        observed_at: datetime,
    ) -> None:
        for node in nodes:
            age = max(0.0, (observed_at - node.last_heartbeat_at).total_seconds())
            context = TelemetryContext(node_id=node.node_id)
            self.telemetry.metric(
                "platform.node.heartbeat_age_seconds",
                age,
                context=context,
                unit="seconds",
                attributes={
                    "status": node.status.value,
                    "draining": node.draining,
                    "maintenance": node.maintenance,
                },
            )
            self._node_resources(node, context=context)
        for worker in workers:
            age = max(0.0, (observed_at - worker.last_heartbeat_at).total_seconds())
            context = TelemetryContext(node_id=worker.node_id, worker_id=worker.worker_id)
            self.telemetry.metric(
                "platform.worker.heartbeat_age_seconds",
                age,
                context=context,
                unit="seconds",
                attributes={"status": worker.status.value, "draining": worker.draining},
            )
            self.telemetry.metric(
                "platform.worker.active_jobs",
                float(worker.active_jobs),
                context=context,
            )
            self.telemetry.metric(
                "platform.worker.concurrency_limit",
                float(worker.concurrency_limit),
                context=context,
            )

    def reconciliation(
        self,
        job: WorkerJobRequest,
        *,
        node_id: str | None,
        worker_id: str,
        previous_state: DistributedDispatchState,
        current_state: DistributedDispatchState,
        error_code: str | None,
        observed_at: datetime,
    ) -> None:
        context = _job_context(job, node_id=node_id, worker_id=worker_id)
        attributes: dict[str, JsonValue] = {
            "previous_state": previous_state.value,
            "current_state": current_state.value,
            "error_code": error_code,
        }
        self.telemetry.metric(
            "platform.worker.reconciliations",
            1.0,
            context=context,
            attributes=attributes,
        )
        self.telemetry.timeline(
            event_name="worker.reconciled",
            component=FailureComponent.SCHEDULER_WORKER_NODE,
            context=context,
            timestamp=observed_at,
            outcome=_dispatch_outcome(current_state),
            failure=(
                None
                if error_code is None
                else FailureClassification(
                    component=FailureComponent.SCHEDULER_WORKER_NODE,
                    code=error_code,
                    retryable=True,
                )
            ),
            attributes=attributes,
        )

    def _node_resources(self, node: NodeRecord, *, context: TelemetryContext) -> None:
        resources = node.resources
        for name, value, unit in (
            ("platform.node.cpu_cores_available", resources.cpu_cores_available, "cores"),
            ("platform.node.ram_available_bytes", resources.ram_available_bytes, "bytes"),
            (
                "platform.node.storage_available_bytes",
                resources.storage_available_bytes,
                "bytes",
            ),
            (
                "platform.node.accelerator_memory_available_bytes",
                resources.max_available_accelerator_memory_bytes,
                "bytes",
            ),
        ):
            self.telemetry.metric(name, float(value), context=context, unit=unit)


def _job_context(
    job: WorkerJobRequest,
    *,
    node_id: str | None = None,
    worker_id: str | None = None,
) -> TelemetryContext:
    request = job.execution
    return TelemetryContext(
        project_id=request.context.project_id,
        task_id=request.subject_id if request.subject_type == "task" else None,
        run_id=request.run_id,
        step_id=request.subject_id if request.subject_type == "step" else None,
        worker_job_id=job.worker_job_id,
        node_id=node_id,
        worker_id=worker_id,
        correlation_id=request.context.correlation_id,
        causation_id=request.context.causation_id,
    )


def _dispatch_outcome(state: DistributedDispatchState) -> TelemetryOutcome:
    if state is DistributedDispatchState.TERMINAL:
        return TelemetryOutcome.SUCCEEDED
    if state is DistributedDispatchState.CANCEL_PENDING:
        return TelemetryOutcome.CANCELLED
    if state is DistributedDispatchState.LOST:
        return TelemetryOutcome.FAILED
    return TelemetryOutcome.UNKNOWN
