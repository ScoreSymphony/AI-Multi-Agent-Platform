"""Bounded Integration/Repair Step composition for issue #872.

Repair Steps are created and progressed by the canonical planning/#384 authorities. This module
only binds those existing canonical identities to a blocked integration candidate and records the
exact repaired repository revision plus #86-style Verification evidence returned by the canonical
review boundary.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from ai_multi_agent_platform.domain import Plan, Step

from .models import (
    CodingBatch,
    IntegrationCandidate,
    IntegrationRepairAttempt,
    IntegrationState,
    RepairAttemptState,
    VerificationEvidence,
)
from .service import CodingBatchStore

DEFAULT_MAX_REPAIR_ATTEMPTS = 3


def _required(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


def _repair_id(
    *,
    batch_id: str,
    integration_id: str,
    step_id: str,
    attempt: int,
    target_revision: str,
) -> str:
    payload = json.dumps(
        {
            "batch_id": batch_id,
            "integration_id": integration_id,
            "step_id": step_id,
            "attempt": attempt,
            "target_revision": target_revision,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"repair-{hashlib.sha256(payload).hexdigest()[:24]}"


class CodingBatchRepairCoordinator:
    """Compose canonical repair Steps with #872 integration state.

    The coordinator deliberately accepts canonical :class:`Plan`/:class:`Step` objects rather
    than creating repair work itself. The caller must obtain those objects through #439/#384.
    """

    def __init__(
        self,
        store: CodingBatchStore,
        *,
        max_attempts: int = DEFAULT_MAX_REPAIR_ATTEMPTS,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self._store = store
        self._max_attempts = max_attempts

    def bind_repair_step(
        self,
        batch_id: str,
        integration_id: str,
        *,
        repair_plan: Plan,
        repair_step: Step,
        target_revision: str,
    ) -> IntegrationRepairAttempt:
        """Bind one blocked candidate to one canonical repair Step idempotently."""

        target_revision = _required(target_revision, "target_revision")
        if repair_step.plan_id != repair_plan.id:
            raise ValueError("repair Step does not belong to supplied canonical Plan")
        batch = self._get_batch(batch_id)
        candidate = batch.integration_candidate(integration_id)

        if candidate.state is IntegrationState.REPAIRING:
            current = candidate.repair_attempts[-1]
            if (
                current.task_id == repair_plan.task_id
                and current.plan_id == repair_plan.id
                and current.step_id == repair_step.id
                and current.target_revision == target_revision
                and current.state is RepairAttemptState.BOUND
            ):
                return current
            raise ValueError("integration candidate already has a repair Step in progress")
        if candidate.state is not IntegrationState.BLOCKED:
            raise ValueError("repair Step binding requires a blocked integration candidate")
        if len(candidate.repair_attempts) >= self._max_attempts:
            raise ValueError("integration repair attempt limit exhausted")
        if any(item.step_id == repair_step.id for item in candidate.repair_attempts):
            raise ValueError("canonical repair Step identity has already been consumed")

        attempt_number = len(candidate.repair_attempts) + 1
        repair = IntegrationRepairAttempt(
            repair_id=_repair_id(
                batch_id=batch.batch_id,
                integration_id=candidate.integration_id,
                step_id=repair_step.id,
                attempt=attempt_number,
                target_revision=target_revision,
            ),
            attempt=attempt_number,
            task_id=repair_plan.task_id,
            plan_id=repair_plan.id,
            step_id=repair_step.id,
            target_revision=target_revision,
            source_blocker_reasons=candidate.blocker_reasons,
            source_conflicts=candidate.conflicts,
        )
        updated = replace(
            candidate,
            state=IntegrationState.REPAIRING,
            validation=None,
            repair_attempts=(*candidate.repair_attempts, repair),
        )
        self._save_candidate(batch, updated)
        return repair

    def record_repair_result(
        self,
        batch_id: str,
        integration_id: str,
        repair_id: str,
        *,
        output_revision: str,
        verification: VerificationEvidence,
    ) -> IntegrationCandidate:
        """Accept repaired output only with fresh exact-subject passing Verification."""

        output_revision = _required(output_revision, "output_revision")
        batch = self._get_batch(batch_id)
        candidate = batch.integration_candidate(integration_id)
        repair = self._active_repair(candidate, repair_id)
        if repair.state is RepairAttemptState.VERIFIED:
            if repair.output_revision == output_revision and repair.verification == verification:
                return candidate
            raise ValueError("repair result retry conflicts with recorded verified output")
        if repair.state is not RepairAttemptState.BOUND:
            raise ValueError("only a bound repair Step may record repaired output")
        if verification.subject_revision != output_revision:
            raise ValueError("repair Verification subject does not match repaired output revision")
        if not verification.passed:
            raise ValueError("failed repair Verification cannot unblock integration")

        verified = replace(
            repair,
            state=RepairAttemptState.VERIFIED,
            output_revision=output_revision,
            verification=verification,
            failure_reason=None,
        )
        updated = replace(
            candidate,
            state=IntegrationState.VALIDATING,
            target_base_revision=repair.target_revision,
            conflicts=(),
            integrated_revision=output_revision,
            validation=None,
            stale_base=False,
            blocker_reasons=(),
            repair_attempts=(*candidate.repair_attempts[:-1], verified),
        )
        self._save_candidate(batch, updated)
        return updated

    def fail_repair_attempt(
        self,
        batch_id: str,
        integration_id: str,
        repair_id: str,
        *,
        reason: str,
    ) -> IntegrationCandidate:
        """Return an unresolved repair to visible blocked state without losing history."""

        reason = _required(reason, "reason")
        batch = self._get_batch(batch_id)
        candidate = batch.integration_candidate(integration_id)
        repair = self._active_repair(candidate, repair_id)
        if repair.state is RepairAttemptState.FAILED and repair.failure_reason == reason:
            return candidate
        if repair.state is not RepairAttemptState.BOUND:
            raise ValueError("only a bound repair Step may fail")
        failed = replace(repair, state=RepairAttemptState.FAILED, failure_reason=reason)
        updated = replace(
            candidate,
            state=IntegrationState.BLOCKED,
            validation=None,
            blocker_reasons=(reason,),
            repair_attempts=(*candidate.repair_attempts[:-1], failed),
        )
        self._save_candidate(batch, updated)
        return updated

    def reconcile_target_revision(
        self,
        batch_id: str,
        integration_id: str,
        *,
        current_target_revision: str,
    ) -> IntegrationCandidate:
        """Invalidate stale integration/validation state when the target branch moves."""

        current_target_revision = _required(current_target_revision, "current_target_revision")
        batch = self._get_batch(batch_id)
        candidate = batch.integration_candidate(integration_id)
        if candidate.state is IntegrationState.MERGED:
            raise ValueError("merged integration candidate cannot be reconciled against a new target")
        if current_target_revision == candidate.target_base_revision:
            return candidate

        reason = (
            f"target revision changed from {candidate.target_base_revision} "
            f"to {current_target_revision}"
        )
        repair_attempts = candidate.repair_attempts
        if candidate.state is IntegrationState.REPAIRING and repair_attempts:
            active = repair_attempts[-1]
            if active.state is RepairAttemptState.BOUND:
                active = replace(
                    active,
                    state=RepairAttemptState.FAILED,
                    failure_reason="target changed while repair Step was in progress",
                )
                repair_attempts = (*repair_attempts[:-1], active)
        updated = replace(
            candidate,
            state=IntegrationState.BLOCKED,
            validation=None,
            stale_base=True,
            blocker_reasons=(reason,),
            repair_attempts=repair_attempts,
        )
        self._save_candidate(batch, updated)
        return updated

    def _get_batch(self, batch_id: str) -> CodingBatch:
        batch = self._store.get(batch_id)
        if batch is None:
            raise KeyError(batch_id)
        return batch

    @staticmethod
    def _active_repair(
        candidate: IntegrationCandidate,
        repair_id: str,
    ) -> IntegrationRepairAttempt:
        if candidate.state is not IntegrationState.REPAIRING or not candidate.repair_attempts:
            raise ValueError("integration candidate has no repair Step in progress")
        repair = candidate.repair_attempts[-1]
        if repair.repair_id != repair_id:
            raise ValueError("repair result does not match the active canonical repair Step")
        return repair

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
