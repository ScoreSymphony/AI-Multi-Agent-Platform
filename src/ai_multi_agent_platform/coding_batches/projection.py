"""Read-only Control Plane projection helpers for coding batches.

Clients consume this derived state instead of inferring safety from provider-native Git refs.
"""

from __future__ import annotations

from typing import Any

from .models import CodingBatch


def coding_batch_resource(batch: CodingBatch) -> dict[str, Any]:
    """Project canonical orchestration state without exposing host-local paths or secrets."""

    return {
        "id": batch.batch_id,
        "kind": "coding_batch",
        "request_key": batch.request_key,
        "repository_id": batch.repository_id,
        "target_ref": batch.target_ref,
        "base_revision": batch.base_revision,
        "aggregation_policy": batch.aggregation_policy.value,
        "concurrency_limit": batch.concurrency_limit,
        "workstreams": [
            {
                "id": workstream.id,
                "task_id": workstream.provenance.task_id,
                "plan_id": workstream.provenance.plan_id,
                "plan_revision": workstream.provenance.plan_revision,
                "step_id": workstream.provenance.step_id,
                "state": workstream.state.value,
                "blocked_by": list(workstream.blocked_by),
                "agent_revision": workstream.provenance.agent_revision,
                "agent_run_id": workstream.provenance.agent_run_id,
                "workspace_id": workstream.provenance.workspace_id,
                "snapshot_id": workstream.provenance.snapshot_id,
                "repository_id": workstream.provenance.repository_id,
                "base_revision": workstream.provenance.base_revision,
                # branch_ref is explicitly implementation/external metadata, not identity.
                "branch_ref": workstream.provenance.branch_ref,
                "output_revision": workstream.provenance.output_revision,
                "changed_paths": (
                    list(workstream.result.changed_paths) if workstream.result is not None else []
                ),
                "diff_digest": (
                    workstream.result.diff_digest if workstream.result is not None else None
                ),
                "verification": (
                    {
                        "id": workstream.verification.verification_id,
                        "subject_revision": workstream.verification.subject_revision,
                        "passed": workstream.verification.passed,
                        "check_refs": list(workstream.verification.check_refs),
                    }
                    if workstream.verification is not None
                    else None
                ),
                "failure_reason": workstream.failure_reason,
            }
            for workstream in batch.workstreams
        ],
        "overlaps": [
            {
                "left_work_item_id": decision.left_work_item_id,
                "right_work_item_id": decision.right_work_item_id,
                "kind": decision.kind.value,
                "rationale": decision.rationale,
                "blocks_parallel_start": decision.blocks_parallel_start,
            }
            for decision in batch.overlaps
        ],
        "integration_candidates": [
            {
                "id": candidate.integration_id,
                "state": candidate.state.value,
                "target_base_revision": candidate.target_base_revision,
                "ordered_workstream_ids": list(candidate.ordered_workstream_ids),
                "ordered_revisions": list(candidate.ordered_revisions),
                "integrated_revision": candidate.integrated_revision,
                "stale_base": candidate.stale_base,
                "blocker_reasons": list(candidate.blocker_reasons),
                "conflicts": [
                    {
                        "kind": conflict.kind.value,
                        "workstream_ids": list(conflict.workstream_ids),
                        "rationale": conflict.rationale,
                    }
                    for conflict in candidate.conflicts
                ],
                "repair_attempts": [
                    {
                        "id": repair.repair_id,
                        "attempt": repair.attempt,
                        "state": repair.state.value,
                        "task_id": repair.task_id,
                        "plan_id": repair.plan_id,
                        "step_id": repair.step_id,
                        "target_revision": repair.target_revision,
                        "source_blocker_reasons": list(repair.source_blocker_reasons),
                        "source_conflicts": [
                            {
                                "kind": conflict.kind.value,
                                "workstream_ids": list(conflict.workstream_ids),
                                "rationale": conflict.rationale,
                            }
                            for conflict in repair.source_conflicts
                        ],
                        "output_revision": repair.output_revision,
                        "verification": (
                            {
                                "id": repair.verification.verification_id,
                                "subject_revision": repair.verification.subject_revision,
                                "passed": repair.verification.passed,
                                "check_refs": list(repair.verification.check_refs),
                            }
                            if repair.verification is not None
                            else None
                        ),
                        "failure_reason": repair.failure_reason,
                    }
                    for repair in candidate.repair_attempts
                ],
                "combined_validation": (
                    {
                        "subject_revision": candidate.validation.subject_revision,
                        "verification_id": candidate.validation.verification_id,
                        "tests_passed": candidate.validation.tests_passed,
                        "passes": candidate.validation.passes,
                        "required_checks": [
                            {
                                "name": check.name,
                                "revision": check.revision,
                                "state": check.state.value,
                                "external_ref": check.external_ref,
                            }
                            for check in candidate.validation.required_checks
                        ],
                        "evaluation_refs": list(candidate.validation.evaluation_refs),
                    }
                    if candidate.validation is not None
                    else None
                ),
                "change_request_ref": candidate.change_request_ref,
            }
            for candidate in batch.integration_candidates
        ],
    }
