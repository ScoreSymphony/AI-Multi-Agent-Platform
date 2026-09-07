"""Issue-#500 pressure instrumentation over the canonical #16 Telemetry facade."""

from __future__ import annotations

from datetime import datetime
from threading import Lock

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.observability import (
    FailureComponent,
    Telemetry,
    TelemetryContext,
    TelemetryOutcome,
)

from .models import WorkerJobRequest
from .pressure import AdmissionAction, AdmissionDecision, HostPressureSnapshot, PressureState


class PressureTelemetry:
    """Emit portable pressure/admission facts without exporting provider-private host metadata."""

    def __init__(self, telemetry: Telemetry) -> None:
        self.telemetry = telemetry
        self._states: dict[str, PressureState] = {}
        self._last_observations: dict[str, tuple[datetime, PressureState, bool]] = {}
        self._lock = Lock()

    def snapshot(self, node_id: str, snapshot: HostPressureSnapshot) -> None:
        """Record one portable Node pressure observation and state transition."""

        observation = (snapshot.observed_at, snapshot.state, snapshot.trusted)
        with self._lock:
            if self._last_observations.get(node_id) == observation:
                return
            self._last_observations[node_id] = observation
            previous = self._states.get(node_id)
            self._states[node_id] = snapshot.state

        context = TelemetryContext(node_id=node_id)
        self.telemetry.metric(
            "platform.node.pressure.observations",
            1.0,
            context=context,
            attributes={
                "state": snapshot.state.value,
                "trusted": snapshot.trusted,
            },
        )
        for signal in snapshot.signals:
            attributes: dict[str, JsonValue] = {
                "kind": signal.kind.value,
                "state": signal.state.value,
            }
            self.telemetry.metric(
                "platform.node.pressure.signals",
                1.0,
                context=context,
                attributes=attributes,
            )
            if signal.value is not None:
                self.telemetry.metric(
                    "platform.node.pressure.signal_value",
                    signal.value,
                    context=context,
                    unit=signal.unit or "value",
                    attributes=attributes,
                )

        if previous is None or previous is snapshot.state:
            return
        recovered = (
            previous in {PressureState.ELEVATED, PressureState.CRITICAL}
            and snapshot.state is PressureState.HEALTHY
        )
        self.telemetry.timeline(
            event_name="node.pressure.recovered" if recovered else "node.pressure.changed",
            component=FailureComponent.SCHEDULER_WORKER_NODE,
            context=context,
            timestamp=snapshot.observed_at,
            outcome=TelemetryOutcome.SUCCEEDED if recovered else TelemetryOutcome.UNKNOWN,
            attributes={
                "previous_state": previous.value,
                "current_state": snapshot.state.value,
                "trusted": snapshot.trusted,
            },
        )

    def admission(
        self,
        job: WorkerJobRequest,
        *,
        node_id: str,
        worker_id: str,
        decision: AdmissionDecision,
        workload_class: str | None,
    ) -> None:
        """Record a deterministic pressure-admission result and its structured reasons."""

        context = _job_context(job, node_id=node_id, worker_id=worker_id)
        attributes: dict[str, JsonValue] = {
            "action": decision.action.value,
            "pressure_state": decision.pressure_state.value,
            "workload_class": workload_class,
        }
        self.telemetry.metric(
            "platform.scheduler.pressure_admissions",
            1.0,
            context=context,
            attributes=attributes,
        )
        if decision.snapshot_age_seconds is not None:
            self.telemetry.metric(
                "platform.scheduler.pressure_snapshot_age_seconds",
                decision.snapshot_age_seconds,
                context=context,
                unit="seconds",
                attributes={
                    "action": decision.action.value,
                    "pressure_state": decision.pressure_state.value,
                },
            )
        for reason in decision.reasons:
            self.telemetry.metric(
                "platform.scheduler.pressure_admission_reasons",
                1.0,
                context=context,
                attributes={
                    "action": decision.action.value,
                    "reason_code": reason.code.value,
                },
            )
        self.telemetry.timeline(
            event_name="scheduler.pressure_admission",
            component=FailureComponent.SCHEDULER_WORKER_NODE,
            context=context,
            outcome=(
                TelemetryOutcome.SUCCEEDED
                if decision.action is AdmissionAction.ADMIT
                else TelemetryOutcome.UNKNOWN
            ),
            attributes={
                **attributes,
                "reason_codes": [reason.code.value for reason in decision.reasons],
            },
        )


def _job_context(
    job: WorkerJobRequest,
    *,
    node_id: str,
    worker_id: str,
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


__all__ = ["PressureTelemetry"]
