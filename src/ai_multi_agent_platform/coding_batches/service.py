"""Deterministic composition state machine for issue #872 coding batches.

The coordinator intentionally performs no Git, Workspace, Agent, Verification or authorization
side effects itself.  Those remain with #82, #37, #33/#384, #86 and #15 respectively.  Callers
record the exact results returned by those authorities here, making restart/reconciliation and
Control Plane projection possible without inventing duplicate canonical resources.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Protocol

from .models import (
    BatchAggregationPolicy,
    CodingBatch,
    CodingWorkItem,
    CodingWorkstream,
    CombinedValidationEvidence,
    IntegrationCandidate,
    IntegrationConflict,
    IntegrationState,
    OverlapKind,
    VerificationEvidence,
    WorkstreamProvenance,
    WorkstreamResult,
    WorkstreamState,
)
from .overlap import ConservativeOverlapClassifier


class CodingBatchStore(Protocol):
    """Persistence seam; production wiring may use the platform persistence authority."""

    def get(self, batch_id: str) -> CodingBatch | None: ...

    def get_by_request_key(self, request_key: str) -> CodingBatch | None: ...

    def save(self, batch: CodingBatch) -> None: ...


class InMemoryCodingBatchStore:
    """Deterministic reference store used by local operation and tests."""

    def __init__(self) -> None:
        self._batches: dict[str, CodingBatch] = {}
        self._request_keys: dict[str, str] = {}

    def get(self, batch_id: str) -> CodingBatch | None:
        return self._batches.get(batch_id)

    def get_by_request_key(self, request_key: str) -> CodingBatch | None:
        batch_id = self._request_keys.get(request_key)
        return None if batch_id is None else self._batches.get(batch_id)

    def save(self, batch: CodingBatch) -> None:
        existing_id = self._request_keys.get(batch.request_key)
        if existing_id is not None and existing_id != batch.batch_id:
            raise ValueError("request_key already belongs to a different coding batch")
        self._batches[batch.batch_id] = batch
        self._request_keys[batch.request_key] = batch.batch_id


def deterministic_branch_ref(batch_id: str, work_item_id: str) -> str:
    """Provider-local branch name; never a canonical Workspace or workstream identity."""

    digest = hashlib.sha256(f"{batch_id}:{work_item_id}".encode()).hexdigest()[:12]
    safe_item = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in work_item_id)[:40]
    return f"coding/{safe_item}-{digest}"


def _stable_id(prefix: str, payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:24]}"


def _batch_fingerprint(
    *,
    repository_id: str,
    target_ref: str,
    base_revision: str,
    items: tuple[CodingWorkItem, ...],
    aggregation_policy: BatchAggregationPolicy,
    concurrency_limit: int,
) -> object:
    return {
        "repository_id": repository_id,
        "target_ref": target_ref,
        "base_revision": base_revision,
        "aggregation_policy": aggregation_policy.value,
        "concurrency_limit": concurrency_limit,
        "items": [
            {
                "id": item.work_item_id,
                "task_id": item.task_id,
                "plan_id": item.plan_id,
                "step_id": item.step_id,
                "dependencies": item.dependencies,
                "affected_paths": item.affected_paths,
                "semantic_scopes": item.semantic_scopes,
            }
            for item in items
        ],
    }


class CodingBatchCoordinator:
    """Compose canonical authorities into restart-safe parallel coding batch state."""

    def __init__(
        self,
        store: CodingBatchStore | None = None,
        classifier: ConservativeOverlapClassifier | None = None,
    ) -> None:
        self._store = store or InMemoryCodingBatchStore()
        self._classifier = classifier or ConservativeOverlapClassifier()

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
        if not work_items:
            raise ValueError("coding batch requires at least one work item")
        ids = tuple(item.work_item_id for item in work_items)
        if len(ids) != len(set(ids)):
            raise ValueError("coding batch work item ids must be unique")
        known = set(ids)
        for item in work_items:
            unknown = set(item.dependencies) - known
            if unknown:
                raise ValueError(
                    f"work item {item.work_item_id} depends on unknown items: {sorted(unknown)}"
                )

        fingerprint = _batch_fingerprint(
            repository_id=repository_id,
            target_ref=target_ref,
            base_revision=base_revision,
            items=work_items,
            aggregation_policy=aggregation_policy,
            concurrency_limit=concurrency_limit,
        )
        batch_id = _stable_id("coding-batch", {"request_key": request_key, "input": fingerprint})
        existing = self._store.get_by_request_key(request_key)
        if existing is not None:
            if existing.batch_id != batch_id:
                raise ValueError("request_key replay does not match the original coding batch input")
            return existing

        overlaps = self._classifier.classify_all(work_items)
        order = {item.work_item_id: index for index, item in enumerate(work_items)}
        blockers: dict[str, set[str]] = {item.work_item_id: set(item.dependencies) for item in work_items}
        for decision in overlaps:
            if decision.kind is OverlapKind.INDEPENDENT or decision.kind is OverlapKind.DEPENDENCY:
                continue
            # Ambiguous/conflicting peers are serialized deterministically instead of being
            # declared independent merely to increase concurrency.
            left = decision.left_work_item_id
            right = decision.right_work_item_id
            if order[left] < order[right]:
                blockers[right].add(left)
            else:
                blockers[left].add(right)

        workstreams = tuple(
            CodingWorkstream(
                work_item=item,
                provenance=WorkstreamProvenance(
                    task_id=item.task_id,
                    plan_id=item.plan_id,
                    step_id=item.step_id,
                    repository_id=repository_id,
                    base_revision=base_revision,
                ),
                state=WorkstreamState.READY if not blockers[item.work_item_id] else WorkstreamState.BLOCKED,
                blocked_by=tuple(sorted(blockers[item.work_item_id], key=order.__getitem__)),
            )
            for item in work_items
        )
        batch = CodingBatch(
            batch_id=batch_id,
            request_key=request_key,
            repository_id=repository_id,
            target_ref=target_ref,
            base_revision=base_revision,
            workstreams=workstreams,
            overlaps=overlaps,
            aggregation_policy=aggregation_policy,
            concurrency_limit=concurrency_limit,
        )
        self._store.save(batch)
        return batch

    def get(self, batch_id: str) -> CodingBatch:
        batch = self._store.get(batch_id)
        if batch is None:
            raise KeyError(batch_id)
        return batch

    def ready_workstreams(self, batch_id: str) -> tuple[CodingWorkstream, ...]:
        batch = self.get(batch_id)
        active_states = {WorkstreamState.MATERIALIZED, WorkstreamState.RUNNING}
        available = max(
            0,
            batch.concurrency_limit
            - sum(workstream.state in active_states for workstream in batch.workstreams),
        )
        return tuple(
            workstream
            for workstream in batch.workstreams
            if workstream.state is WorkstreamState.READY
        )[:available]

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
        batch = self.get(batch_id)
        current = batch.workstream(workstream_id)
        expected_branch = deterministic_branch_ref(batch.batch_id, workstream_id)
        resolved_branch = branch_ref or expected_branch
        if current.state is WorkstreamState.MATERIALIZED:
            if (
                current.provenance.workspace_id == workspace_id
                and current.provenance.snapshot_id == snapshot_id
                and current.provenance.agent_run_id == agent_run_id
                and current.provenance.branch_ref == resolved_branch
            ):
                return current
            raise ValueError("materialization retry conflicts with recorded canonical provenance")
        if current.state is not WorkstreamState.READY:
            raise ValueError("only ready workstreams can be materialized")
        provenance = replace(
            current.provenance,
            workspace_id=workspace_id,
            snapshot_id=snapshot_id,
            agent_revision=agent_revision,
            agent_run_id=agent_run_id,
            branch_ref=resolved_branch,
        )
        updated = current.with_state(WorkstreamState.MATERIALIZED, provenance=provenance)
        self._save_workstream(batch, updated)
        return updated

    def start_workstream(self, batch_id: str, workstream_id: str) -> CodingWorkstream:
        batch = self.get(batch_id)
        current = batch.workstream(workstream_id)
        if current.state is WorkstreamState.RUNNING:
            return current
        if current.state is not WorkstreamState.MATERIALIZED:
            raise ValueError("workstream must be materialized before execution")
        updated = current.with_state(WorkstreamState.RUNNING)
        self._save_workstream(batch, updated)
        return updated

    def record_result(
        self,
        batch_id: str,
        workstream_id: str,
        result: WorkstreamResult,
    ) -> CodingWorkstream:
        batch = self.get(batch_id)
        current = batch.workstream(workstream_id)
        if current.result == result and current.state is WorkstreamState.PRODUCED:
            return current
        if current.state not in {WorkstreamState.RUNNING, WorkstreamState.PRODUCED}:
            raise ValueError("workstream result requires running/produced state")
        provenance = replace(current.provenance, output_revision=result.output_revision)
        # Any changed output invalidates earlier review evidence by construction.
        updated = current.with_state(
            WorkstreamState.PRODUCED,
            provenance=provenance,
            result=result,
            verification=None,
        )
        self._save_workstream(batch, updated)
        return updated

    def record_verification(
        self,
        batch_id: str,
        workstream_id: str,
        evidence: VerificationEvidence,
    ) -> CodingWorkstream:
        batch = self.get(batch_id)
        current = batch.workstream(workstream_id)
        if current.result is None:
            raise ValueError("verification requires an exact produced result")
        if evidence.subject_revision != current.result.output_revision:
            raise ValueError("verification subject does not match current output revision")
        if current.verification == evidence:
            return current
        state = WorkstreamState.VERIFIED if evidence.passed else WorkstreamState.FAILED
        updated = current.with_state(
            state,
            verification=evidence,
            failure_reason=None if evidence.passed else "verification failed",
        )
        self._save_workstream(batch, updated)
        return updated

    def accept_workstream(self, batch_id: str, workstream_id: str) -> CodingWorkstream:
        batch = self.get(batch_id)
        current = batch.workstream(workstream_id)
        if current.state is WorkstreamState.ACCEPTED:
            return current
        if current.state is not WorkstreamState.VERIFIED or current.verification is None:
            raise ValueError("workstream requires passing exact-subject verification before acceptance")
        if not current.verification.passed:
            raise ValueError("failed verification cannot be accepted")
        updated = current.with_state(WorkstreamState.ACCEPTED)
        batch = self._replace_workstream(batch, updated)
        batch = self._refresh_readiness(batch)
        self._store.save(batch)
        return batch.workstream(workstream_id)

    def fail_workstream(self, batch_id: str, workstream_id: str, reason: str) -> CodingWorkstream:
        batch = self.get(batch_id)
        current = batch.workstream(workstream_id)
        updated = current.with_state(WorkstreamState.FAILED, failure_reason=reason)
        self._save_workstream(batch, updated)
        return updated

    def cancel_workstream(self, batch_id: str, workstream_id: str, reason: str) -> CodingWorkstream:
        batch = self.get(batch_id)
        current = batch.workstream(workstream_id)
        updated = current.with_state(WorkstreamState.CANCELLED, failure_reason=reason)
        self._save_workstream(batch, updated)
        return updated

    def build_integration_candidate(
        self,
        batch_id: str,
        *,
        current_target_revision: str,
        workstream_ids: tuple[str, ...] | None = None,
    ) -> IntegrationCandidate:
        batch = self.get(batch_id)
        selected = (
            tuple(ws.id for ws in batch.workstreams if ws.state is WorkstreamState.ACCEPTED)
            if workstream_ids is None
            else workstream_ids
        )
        if not selected:
            raise ValueError("integration candidate requires at least one accepted workstream")
        if len(selected) != len(set(selected)):
            raise ValueError("integration candidate workstream ids must be unique")
        index = {workstream.id: position for position, workstream in enumerate(batch.workstreams)}
        ordered = tuple(sorted(selected, key=index.__getitem__))
        workstreams = tuple(batch.workstream(item) for item in ordered)
        if any(item.state is not WorkstreamState.ACCEPTED for item in workstreams):
            raise ValueError("integration candidates may contain only accepted workstreams")
        if any(item.result is None for item in workstreams):
            raise ValueError("accepted workstream is missing result provenance")

        conflicts = self._integration_conflicts(batch, workstreams)
        stale_base = current_target_revision != batch.base_revision
        blockers = [conflict.rationale for conflict in conflicts]
        if stale_base:
            blockers.append(
                f"target revision changed from {batch.base_revision} to {current_target_revision}"
            )
        revisions = tuple(item.result.output_revision for item in workstreams if item.result is not None)
        integration_id = _stable_id(
            "integration",
            {
                "batch_id": batch.batch_id,
                "target_revision": current_target_revision,
                "workstreams": ordered,
                "revisions": revisions,
            },
        )
        existing = next(
            (item for item in batch.integration_candidates if item.integration_id == integration_id),
            None,
        )
        if existing is not None:
            return existing
        candidate = IntegrationCandidate(
            integration_id=integration_id,
            target_base_revision=current_target_revision,
            ordered_workstream_ids=ordered,
            ordered_revisions=revisions,
            state=IntegrationState.BLOCKED if blockers else IntegrationState.READY,
            conflicts=conflicts,
            stale_base=stale_base,
            blocker_reasons=tuple(blockers),
        )
        self._store.save(
            replace(batch, integration_candidates=(*batch.integration_candidates, candidate))
        )
        return candidate

    def record_integrated_revision(
        self,
        batch_id: str,
        integration_id: str,
        *,
        integrated_revision: str,
    ) -> IntegrationCandidate:
        batch = self.get(batch_id)
        candidate = batch.integration_candidate(integration_id)
        if candidate.integrated_revision == integrated_revision and candidate.state is IntegrationState.VALIDATING:
            return candidate
        if candidate.state is not IntegrationState.READY:
            raise ValueError("blocked integration candidate cannot produce an integrated revision")
        updated = replace(
            candidate,
            state=IntegrationState.VALIDATING,
            integrated_revision=integrated_revision,
            validation=None,
        )
        self._save_candidate(batch, updated)
        return updated

    def record_combined_validation(
        self,
        batch_id: str,
        integration_id: str,
        evidence: CombinedValidationEvidence,
    ) -> IntegrationCandidate:
        batch = self.get(batch_id)
        candidate = batch.integration_candidate(integration_id)
        if candidate.integrated_revision is None:
            raise ValueError("combined validation requires an integrated revision")
        if evidence.subject_revision != candidate.integrated_revision:
            raise ValueError("combined validation is stale for the integrated revision")
        stale_checks = tuple(
            check.name
            for check in evidence.required_checks
            if check.revision != candidate.integrated_revision
        )
        passes = evidence.passes and not stale_checks
        blockers: tuple[str, ...] = ()
        if not passes:
            reasons = []
            if not evidence.tests_passed:
                reasons.append("combined repository tests failed")
            if stale_checks:
                reasons.append("required checks target stale revision: " + ", ".join(stale_checks))
            failed_checks = [check.name for check in evidence.required_checks if check.state.value != "pass"]
            if failed_checks:
                reasons.append("required checks are not passing: " + ", ".join(failed_checks))
            blockers = tuple(reasons or ["combined validation did not pass"])
        updated = replace(
            candidate,
            state=IntegrationState.VALIDATED if passes else IntegrationState.BLOCKED,
            validation=evidence,
            blocker_reasons=blockers,
        )
        self._save_candidate(batch, updated)
        return updated

    def mark_merge_ready(
        self,
        batch_id: str,
        integration_id: str,
        *,
        authorization_granted: bool,
    ) -> IntegrationCandidate:
        batch = self.get(batch_id)
        candidate = batch.integration_candidate(integration_id)
        if candidate.state is IntegrationState.MERGE_READY:
            return candidate
        if candidate.state is not IntegrationState.VALIDATED or candidate.validation is None:
            raise ValueError("integration requires fresh combined validation before merge readiness")
        if not authorization_granted:
            raise PermissionError("merge readiness is blocked by canonical authorization")
        updated = replace(candidate, state=IntegrationState.MERGE_READY, blocker_reasons=())
        self._save_candidate(batch, updated)
        return updated

    def attach_change_request(
        self,
        batch_id: str,
        integration_id: str,
        *,
        change_request_ref: str,
    ) -> IntegrationCandidate:
        batch = self.get(batch_id)
        candidate = batch.integration_candidate(integration_id)
        if candidate.state is not IntegrationState.MERGE_READY:
            raise ValueError("change request attachment requires merge-ready candidate")
        if candidate.change_request_ref == change_request_ref:
            return candidate
        if candidate.change_request_ref is not None:
            raise ValueError("integration candidate already references a different change request")
        updated = replace(candidate, change_request_ref=change_request_ref)
        self._save_candidate(batch, updated)
        return updated

    def _integration_conflicts(
        self,
        batch: CodingBatch,
        workstreams: tuple[CodingWorkstream, ...],
    ) -> tuple[IntegrationConflict, ...]:
        selected = {item.id: item for item in workstreams}
        conflicts: list[IntegrationConflict] = []
        ids = tuple(selected)
        for left_index, left_id in enumerate(ids):
            left = selected[left_id]
            assert left.result is not None
            for right_id in ids[left_index + 1 :]:
                right = selected[right_id]
                assert right.result is not None
                shared_paths = set(left.result.changed_paths) & set(right.result.changed_paths)
                if shared_paths:
                    conflicts.append(
                        IntegrationConflict(
                            kind=OverlapKind.TEXTUAL_CONFLICT,
                            workstream_ids=(left_id, right_id),
                            rationale="accepted outputs modify the same paths: "
                            + ", ".join(sorted(shared_paths)),
                        )
                    )
                    continue
                decision = next(
                    (
                        item
                        for item in batch.overlaps
                        if {item.left_work_item_id, item.right_work_item_id} == {left_id, right_id}
                    ),
                    None,
                )
                if decision is not None and decision.kind in {
                    OverlapKind.SEMANTIC_OVERLAP,
                    OverlapKind.UNKNOWN,
                }:
                    conflicts.append(
                        IntegrationConflict(
                            kind=decision.kind,
                            workstream_ids=(left_id, right_id),
                            rationale=decision.rationale,
                        )
                    )
        return tuple(conflicts)

    def _refresh_readiness(self, batch: CodingBatch) -> CodingBatch:
        accepted = {item.id for item in batch.workstreams if item.state is WorkstreamState.ACCEPTED}
        terminal_bad = {
            item.id
            for item in batch.workstreams
            if item.state in {WorkstreamState.FAILED, WorkstreamState.CANCELLED}
        }
        updated: list[CodingWorkstream] = []
        for item in batch.workstreams:
            if item.state is not WorkstreamState.BLOCKED:
                updated.append(item)
                continue
            if set(item.blocked_by) & terminal_bad:
                updated.append(item)
                continue
            if set(item.blocked_by).issubset(accepted):
                updated.append(item.with_state(WorkstreamState.READY, blocked_by=()))
            else:
                updated.append(item)
        return replace(batch, workstreams=tuple(updated))

    def _save_workstream(self, batch: CodingBatch, workstream: CodingWorkstream) -> None:
        self._store.save(self._replace_workstream(batch, workstream))

    @staticmethod
    def _replace_workstream(batch: CodingBatch, workstream: CodingWorkstream) -> CodingBatch:
        if workstream.id not in {item.id for item in batch.workstreams}:
            raise KeyError(workstream.id)
        return replace(
            batch,
            workstreams=tuple(
                workstream if item.id == workstream.id else item for item in batch.workstreams
            ),
        )

    def _save_candidate(self, batch: CodingBatch, candidate: IntegrationCandidate) -> None:
        if candidate.integration_id not in {
            item.integration_id for item in batch.integration_candidates
        }:
            raise KeyError(candidate.integration_id)
        self._store.save(
            replace(
                batch,
                integration_candidates=tuple(
                    candidate if item.integration_id == candidate.integration_id else item
                    for item in batch.integration_candidates
                ),
            )
        )
