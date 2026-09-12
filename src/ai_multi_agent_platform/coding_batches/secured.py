"""Public coding-batch coordinator with sensitive integration seams sealed."""

from __future__ import annotations

from dataclasses import replace

from .models import (
    BatchAggregationPolicy,
    CodingBatch,
    CodingWorkItem,
    CodingWorkstream,
    CombinedValidationEvidence,
    IntegrationCandidate,
    IntegrationExecutionProvenance,
    IntegrationState,
    VerificationEvidence,
    WorkstreamResult,
    WorkstreamState,
)
from .overlap import ConservativeOverlapClassifier
from .service import CodingBatchCoordinator as _StateCoordinator
from .service import CodingBatchStore


class CodingBatchCoordinator:
    """Supported #872 coordinator surface over the internal deterministic state machine.

    Productive merge readiness must cross canonical #15, and a clean combined revision must first
    be bound to canonical #384/#33/#37 execution provenance. Callers therefore cannot advance a
    ready integration candidate by merely presenting a Git SHA.
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
        batch = self.get(batch_id)
        selected = (
            tuple(
                workstream.id
                for workstream in batch.workstreams
                if workstream.state is WorkstreamState.ACCEPTED
            )
            if workstream_ids is None
            else workstream_ids
        )
        self._validate_selected_workstreams(batch, selected)
        self._enforce_aggregation_policy(batch, selected)
        return self._state.build_integration_candidate(
            batch_id,
            current_target_revision=current_target_revision,
            workstream_ids=selected,
        )

    def bind_integration_execution(
        self,
        batch_id: str,
        integration_id: str,
        execution: IntegrationExecutionProvenance,
    ) -> IntegrationCandidate:
        """Bind a clean candidate to the exact canonical integration execution attempt."""

        batch = self.get(batch_id)
        candidate = batch.integration_candidate(integration_id)
        if candidate.state is IntegrationState.INTEGRATING:
            if candidate.execution == execution:
                return candidate
            raise ValueError("integration retry conflicts with canonical execution provenance")
        if candidate.state is not IntegrationState.READY:
            raise ValueError("only a ready integration candidate can start integration execution")
        updated = replace(
            candidate,
            state=IntegrationState.INTEGRATING,
            execution=execution,
            integrated_revision=None,
            validation=None,
            blocker_reasons=(),
        )
        self._state._save_candidate(batch, updated)
        return updated

    def record_integrated_revision(
        self,
        batch_id: str,
        integration_id: str,
        *,
        integrated_revision: str,
    ) -> IntegrationCandidate:
        """Record #82 output only after canonical integration execution has been bound."""

        batch = self.get(batch_id)
        candidate = batch.integration_candidate(integration_id)
        if (
            candidate.integrated_revision == integrated_revision
            and candidate.state is IntegrationState.VALIDATING
        ):
            return candidate
        if candidate.state is not IntegrationState.INTEGRATING or candidate.execution is None:
            raise ValueError(
                "integrated revision requires canonical integration execution provenance"
            )
        updated = replace(
            candidate,
            state=IntegrationState.VALIDATING,
            integrated_revision=integrated_revision,
            validation=None,
        )
        self._state._save_candidate(batch, updated)
        return updated

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

    @staticmethod
    def _validate_selected_workstreams(batch: CodingBatch, selected: tuple[str, ...]) -> None:
        if len(selected) != len(set(selected)):
            raise ValueError("integration candidate workstream ids must be unique")
        known_ids = {workstream.id for workstream in batch.workstreams}
        unknown_ids = set(selected) - known_ids
        if unknown_ids:
            raise ValueError(
                "integration candidate references unknown workstreams: "
                + ", ".join(sorted(unknown_ids))
            )

    @staticmethod
    def _enforce_aggregation_policy(batch: CodingBatch, selected: tuple[str, ...]) -> None:
        selected_set = set(selected)
        if batch.aggregation_policy is BatchAggregationPolicy.ALL_REQUIRED:
            required = {workstream.id for workstream in batch.workstreams}
            if selected_set != required:
                missing = tuple(
                    workstream.id
                    for workstream in batch.workstreams
                    if workstream.id not in selected_set
                )
                raise ValueError(
                    "all_required aggregation cannot integrate a partial batch; missing: "
                    + ", ".join(missing)
                )
            return

        if batch.aggregation_policy is BatchAggregationPolicy.DEPENDENCY_CLOSED:
            by_id = {workstream.id: workstream for workstream in batch.workstreams}
            for workstream_id in selected:
                workstream = by_id.get(workstream_id)
                if workstream is None:
                    continue
                missing_dependencies = tuple(
                    dependency_id
                    for dependency_id in workstream.work_item.dependencies
                    if dependency_id not in selected_set
                )
                if missing_dependencies:
                    raise ValueError(
                        "dependency_closed aggregation requires selected dependencies for "
                        f"{workstream_id}: " + ", ".join(missing_dependencies)
                    )
