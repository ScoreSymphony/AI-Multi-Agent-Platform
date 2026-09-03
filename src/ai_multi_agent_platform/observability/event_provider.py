"""Completion-level lifecycle event instrumentation for issue #16."""

from __future__ import annotations

from threading import Lock

from ai_multi_agent_platform.contracts import PlatformEvent

from .exporters import Telemetry
from .instrumentation import ObservabilityEventProvider as _FoundationObservabilityEventProvider
from .models import FailureClassification, TelemetryContext, TelemetryOutcome


class ObservabilityEventProvider(_FoundationObservabilityEventProvider):
    """Foundation event instrumentation plus canonical retry-attempt metrics.

    A retry is observed when the same canonical Task receives a second or later
    ``run.created`` event. The event stream remains authoritative; observability
    only counts the attempts it sees and does not create retry lifecycle state.
    """

    def __init__(self, telemetry: Telemetry) -> None:
        super().__init__(telemetry)
        self._run_attempts_by_task: dict[str, int] = {}
        self._retry_lock = Lock()

    def _observe_span_lifecycle(
        self,
        event: PlatformEvent,
        context: TelemetryContext,
        outcome: TelemetryOutcome,
        failure: FailureClassification | None,
    ) -> float | None:
        if event.event_type == "run.created" and context.task_id is not None:
            with self._retry_lock:
                prior_attempts = self._run_attempts_by_task.get(context.task_id, 0)
                attempt = prior_attempts + 1
                self._run_attempts_by_task[context.task_id] = attempt
            if prior_attempts > 0:
                self._telemetry.metric(
                    "platform.run.retries",
                    1.0,
                    context=context,
                    attributes={"attempt": attempt},
                )
        return super()._observe_span_lifecycle(event, context, outcome, failure)
