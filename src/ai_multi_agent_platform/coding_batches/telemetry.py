"""#16 observability projection for parallel coding-batch composition."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import JsonValue
from ai_multi_agent_platform.observability import (
    FailureComponent,
    Telemetry,
    TelemetryContext,
    TelemetryOutcome,
    TelemetrySeverity,
)

from .models import CodingBatch, CodingWorkstream, IntegrationCandidate


class CodingBatchTelemetry:
    """Emit safe coding-batch orchestration facts through canonical #16 telemetry.

    The adapter owns no event store or lifecycle truth. It projects only canonical IDs, exact
    repository revisions and bounded provider-neutral state; host-local paths and content are never
    included.
    """

    def __init__(self, telemetry: Telemetry) -> None:
        self.telemetry = telemetry

    def workstream_dispatched(
        self,
        batch: CodingBatch,
        workstream: CodingWorkstream,
        *,
        run_id: str,
        agent_id: str,
        plan_revision: int,
        attempt: int,
    ) -> None:
        self._event(
            "coding_batch.workstream.dispatched",
            batch,
            workstream=workstream,
            run_id=run_id,
            agent_id=agent_id,
            outcome=TelemetryOutcome.SUCCEEDED,
            attributes={
                "plan_id": workstream.work_item.plan_id,
                "plan_revision": plan_revision,
                "attempt": attempt,
                "workspace_id": workstream.provenance.workspace_id,
                "snapshot_id": workstream.provenance.snapshot_id,
                "branch_ref": workstream.provenance.branch_ref,
                "base_revision": workstream.provenance.base_revision,
                "agent_run_id": workstream.provenance.agent_run_id,
            },
        )

    def verification_requested(
        self,
        batch: CodingBatch,
        workstream: CodingWorkstream,
        *,
        run_id: str,
        verification_id: str,
        subject_artifact_id: str,
    ) -> None:
        self._event(
            "coding_batch.workstream.verification_requested",
            batch,
            workstream=workstream,
            run_id=run_id,
            verification_id=verification_id,
            attributes={
                "subject_artifact_id": subject_artifact_id,
                "output_revision": workstream.provenance.output_revision,
            },
        )

    def verification_completed(
        self,
        batch: CodingBatch,
        workstream: CodingWorkstream,
        *,
        run_id: str,
        verification_id: str,
        passed: bool,
    ) -> None:
        self._event(
            "coding_batch.workstream.verification_completed",
            batch,
            workstream=workstream,
            run_id=run_id,
            verification_id=verification_id,
            outcome=TelemetryOutcome.SUCCEEDED if passed else TelemetryOutcome.FAILED,
            attributes={
                "passed": passed,
                "output_revision": workstream.provenance.output_revision,
            },
        )

    def integration_state(
        self,
        batch: CodingBatch,
        candidate: IntegrationCandidate,
        *,
        event_name: str,
        outcome: TelemetryOutcome = TelemetryOutcome.UNKNOWN,
    ) -> None:
        self._event(
            event_name,
            batch,
            outcome=outcome,
            attributes={
                "integration_id": candidate.integration_id,
                "integration_state": candidate.state.value,
                "target_base_revision": candidate.target_base_revision,
                "integrated_revision": candidate.integrated_revision,
                "stale_base": candidate.stale_base,
                "ordered_workstream_ids": list(candidate.ordered_workstream_ids),
                "blocker_reasons": list(candidate.blocker_reasons),
                "conflict_kinds": [conflict.kind.value for conflict in candidate.conflicts],
            },
        )

    def _event(
        self,
        event_name: str,
        batch: CodingBatch,
        *,
        workstream: CodingWorkstream | None = None,
        run_id: str | None = None,
        agent_id: str | None = None,
        verification_id: str | None = None,
        outcome: TelemetryOutcome = TelemetryOutcome.UNKNOWN,
        attributes: dict[str, JsonValue] | None = None,
    ) -> None:
        context = TelemetryContext(
            task_id=None if workstream is None else workstream.work_item.task_id,
            run_id=run_id,
            step_id=None if workstream is None else workstream.work_item.step_id,
            agent_id=agent_id,
            workspace_id=None if workstream is None else workstream.provenance.workspace_id,
            verification_id=verification_id,
            correlation_id=batch.batch_id,
        )
        safe_attributes: dict[str, JsonValue] = {
            "coding_batch_id": batch.batch_id,
            "repository_id": batch.repository_id,
            "target_ref": batch.target_ref,
            "batch_base_revision": batch.base_revision,
        }
        if workstream is not None:
            safe_attributes["workstream_id"] = workstream.id
        safe_attributes.update(attributes or {})
        self.telemetry.timeline(
            event_name=event_name,
            component=FailureComponent.ORCHESTRATION,
            context=context,
            outcome=outcome,
            attributes=safe_attributes,
        )
        self.telemetry.log(
            severity=(
                TelemetrySeverity.ERROR
                if outcome is TelemetryOutcome.FAILED
                else TelemetrySeverity.INFO
            ),
            component=FailureComponent.ORCHESTRATION,
            event_name=event_name,
            context=context,
            outcome=outcome,
            attributes=safe_attributes,
        )
