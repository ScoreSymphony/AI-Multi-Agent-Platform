"""Public coding-batch coordinator with the #15 merge-readiness seam sealed."""

from __future__ import annotations

from .models import (
    BatchAggregationPolicy,
    CodingBatch,
    CodingWorkItem,
    CodingWorkstream,
    CombinedValidationEvidence,
    IntegrationCandidate,
    VerificationEvidence,
    WorkstreamResult,
)
from .overlap import ConservativeOverlapClassifier
from .service import CodingBatchCoordinator as _StateCoordinator
from .service import CodingBatchStore


class CodingBatchCoordinator:
    """Supported #872 coordinator surface over the internal deterministic state machine.

    The internal state machine is deliberately not exported as the package coordinator. In
    particular, callers cannot assert authorization with a boolean: productive merge readiness must
    cross :class:`AuthorizedCodingBatchIntegration`, which invokes canonical #15 first.
    """

    def __init__(
        self,
        store: CodingBatchStore | None = None,
        classifier: ConservativeOverlapClassifier | None = None,
    ) -> None:
        self._state = _StateCoordinator(store=store, classifier=classifier)

    def create_batch(
        self,
        *,
        request_key: str,
        repository_id: str,
        target_ref: str,
        base_revision: str,
        work_items: tuple[CodingWorkItem, ...],
        aggregation_policy: BatchAggregationPolicy = BatchAggregationPolicy.ALL_REQUIRED,
        concurrency_limit: int = 4,
    ) -> CodingBatch:
        return self._state.create_batch(
            request_key=request_key,
            repository_id=repository_id,
            target_ref=target_ref,
            base_revision=base_revision,
            work_items=work_items,
            aggregation_policy=aggregation_policy,
            concurrency_limit=concurrency_limit,
        )

    def get(self, batch_id: str) -> CodingBatch:
        return self._state.get(batch_id)

    def bind_plan_revision(
        self,
        batch_id: str,
        workstream_id: str,
        *,
        plan_revision: int,
    ) -> CodingWorkstream:
        """Bind the exact canonical #439/#384 Plan revision before materialization."""

        return self._state.bind_plan_revision(
            batch_id,
            workstream_id,
            plan_revision=plan_revision,
        )

    def ready_workstreams(self, batch_id: str) -> tuple[CodingWorkstream, ...]:
        return self._state.ready_workstreams(batch_id)

    def materialize_workstream(
        self,
        batch_id: str,
        workstream_id: str,
        *,
        workspace_id: str,
        snapshot_id: str,
        agent_revision: str,
        agent_run_id: str,
        branch_ref: str | None = None,
    ) -> CodingWorkstream:
        return self._state.materialize_workstream(
            batch_id,
            workstream_id,
            workspace_id=workspace_id,
            snapshot_id=snapshot_id,
            agent_revision=agent_revision,
            agent_run_id=agent_run_id,
            branch_ref=branch_ref,
        )

    def start_workstream(self, batch_id: str, workstream_id: str) -> CodingWorkstream:
        return self._state.start_workstream(batch_id, workstream_id)

    def record_result(
        self,
        batch_id: str,
        workstream_id: str,
        result: WorkstreamResult,
    ) -> CodingWorkstream:
        return self._state.record_result(batch_id, workstream_id, result)

    def record_verification(
        self,
        batch_id: str,
        workstream_id: str,
        evidence: VerificationEvidence,
    ) -> CodingWorkstream:
        return self._state.record_verification(batch_id, workstream_id, evidence)

    def accept_workstream(self, batch_id: str, workstream_id: str) -> CodingWorkstream:
        return self._state.accept_workstream(batch_id, workstream_id)

    def fail_workstream(self, batch_id: str, workstream_id: str, reason: str) -> CodingWorkstream:
        return self._state.fail_workstream(batch_id, workstream_id, reason)

    def cancel_workstream(self, batch_id: str, workstream_id: str, reason: str) -> CodingWorkstream:
        return self._state.cancel_workstream(batch_id, workstream_id, reason)

    def build_integration_candidate(
        self,
        batch_id: str,
        *,
        current_target_revision: str,
        workstream_ids: tuple[str, ...] | None = None,
    ) -> IntegrationCandidate:
        return self._state.build_integration_candidate(
            batch_id,
            current_target_revision=current_target_revision,
            workstream_ids=workstream_ids,
        )

    def record_integrated_revision(
        self,
        batch_id: str,
        integration_id: str,
        *,
        integrated_revision: str,
    ) -> IntegrationCandidate:
        return self._state.record_integrated_revision(
            batch_id,
            integration_id,
            integrated_revision=integrated_revision,
        )

    def record_combined_validation(
        self,
        batch_id: str,
        integration_id: str,
        evidence: CombinedValidationEvidence,
    ) -> IntegrationCandidate:
        return self._state.record_combined_validation(batch_id, integration_id, evidence)

    def mark_merge_ready(
        self,
        batch_id: str,
        integration_id: str,
    ) -> IntegrationCandidate:
        del batch_id, integration_id
        raise PermissionError(
            "merge readiness requires canonical #15 AuthorizedCodingBatchIntegration"
        )

    def _mark_merge_ready_after_authorization(
        self,
        batch_id: str,
        integration_id: str,
    ) -> IntegrationCandidate:
        """Internal state transition used only after the #15 adapter has enforced its action."""

        return self._state._mark_merge_ready_after_authorization(batch_id, integration_id)

    def attach_change_request(
        self,
        batch_id: str,
        integration_id: str,
        *,
        change_request_ref: str,
    ) -> IntegrationCandidate:
        return self._state.attach_change_request(
            batch_id,
            integration_id,
            change_request_ref=change_request_ref,
        )
