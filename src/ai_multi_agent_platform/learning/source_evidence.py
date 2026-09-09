"""Canonical source-evidence resolvers for governed Learning (#694).

Learning may project exact source bindings into a LearningCandidate, but Task/Run and
Planning remain authoritative for their own records.  These adapters therefore re-read the
canonical owners and fail closed instead of trusting caller-asserted LearningReference values.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import RunStatus, validate_id
from ai_multi_agent_platform.kernel.models import RunState, TaskState
from ai_multi_agent_platform.planning.models import PlanningTrigger, ProposalRecord, ProposalStatus

from .models import LearningReference


@dataclass(frozen=True, slots=True)
class RunFailureSourceRef:
    """Lookup reference for one canonical failed/timed-out Run."""

    task_id: str
    run_id: str
    revision: int | None = None
    digest: str | None = None

    def __post_init__(self) -> None:
        validate_id(self.task_id, "task")
        validate_id(self.run_id, "run")
        if self.revision is not None and self.revision < 1:
            raise ValueError("run failure source revision must be >= 1")
        if self.digest is not None and not self.digest.strip():
            raise ValueError("run failure source digest must not be blank")


@dataclass(frozen=True, slots=True)
class PlanningFailureSourceRef:
    """Lookup reference for one canonical Planning proposal/failure observation."""

    task_id: str
    proposal_id: str
    revision: int | None = None
    digest: str | None = None

    def __post_init__(self) -> None:
        validate_id(self.task_id, "task")
        validate_id(self.proposal_id, "plan_proposal")
        if self.revision is not None and self.revision < 1:
            raise ValueError("planning failure source revision must be >= 1")
        if self.digest is not None and not self.digest.strip():
            raise ValueError("planning failure source digest must not be blank")


@dataclass(frozen=True, slots=True)
class ResolvedLearningSource:
    """Exact primary source plus canonical supporting owner bindings."""

    source: LearningReference
    supporting_refs: tuple[LearningReference, ...] = ()


class LearningRunEvidenceKernel(Protocol):
    async def get_task(self, task_id: str) -> TaskState: ...

    async def get_run(self, task_id: str, run_id: str) -> RunState: ...


class RunFailureEvidenceResolver(Protocol):
    async def resolve(
        self,
        reference: RunFailureSourceRef,
        *,
        project_id: str | None,
    ) -> ResolvedLearningSource: ...


class PlanningEvidenceKernel(Protocol):
    async def get_task(self, task_id: str) -> TaskState: ...


class PlanningEvidenceService(Protocol):
    kernel: PlanningEvidenceKernel

    def history(self, task_id: str) -> tuple[ProposalRecord, ...]: ...


class PlanningFailureEvidenceResolver(Protocol):
    async def resolve(
        self,
        reference: PlanningFailureSourceRef,
        *,
        project_id: str | None,
    ) -> ResolvedLearningSource: ...


class KernelRunFailureEvidenceResolver:
    """Resolve Learning Run-failure evidence from canonical event-sourced Run state."""

    def __init__(self, kernel: LearningRunEvidenceKernel) -> None:
        self._kernel = kernel

    async def resolve(
        self,
        reference: RunFailureSourceRef,
        *,
        project_id: str | None,
    ) -> ResolvedLearningSource:
        task = await self._kernel.get_task(reference.task_id)
        run = await self._kernel.get_run(reference.task_id, reference.run_id)
        if run.task_id != reference.task_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Learning Run evidence does not belong to the requested canonical Task",
                details={"task_id": reference.task_id, "run_id": reference.run_id},
            )
        if run.status not in {RunStatus.FAILED, RunStatus.TIMED_OUT}:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Learning Run failure evidence requires a failed or timed-out canonical Run",
                details={"run_id": reference.run_id, "run_status": run.status.value},
            )
        _require_owner_project_consistency(task, run)
        _require_project_scope(
            expected_project_id=project_id,
            actual_project_id=run.run.project_id,
            source_label="Run",
            source_id=reference.run_id,
        )
        if reference.revision is not None and reference.revision != run.revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Learning Run evidence revision is stale or mismatched",
                details={
                    "run_id": reference.run_id,
                    "expected_revision": reference.revision,
                    "actual_revision": run.revision,
                },
            )
        digest = _run_projection_digest(run)
        if reference.digest is not None and reference.digest != digest:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Learning Run evidence digest is stale or mismatched",
                details={"run_id": reference.run_id},
            )
        return ResolvedLearningSource(
            source=LearningReference(
                kind="run_failure",
                resource_id=reference.run_id,
                revision=str(run.revision),
                digest=digest,
            ),
            supporting_refs=(
                LearningReference(
                    kind="task",
                    resource_id=reference.task_id,
                    revision=str(task.revision),
                ),
            ),
        )


_FAILURE_PLANNING_TRIGGERS = frozenset(
    {
        PlanningTrigger.TERMINAL_FAILURE,
        PlanningTrigger.RETRY_EXHAUSTED,
        PlanningTrigger.VERIFICATION_CHANGES_REQUIRED,
        PlanningTrigger.VERIFICATION_FAILED,
        PlanningTrigger.VERIFICATION_INCONCLUSIVE,
        PlanningTrigger.AGENT_UNAVAILABLE,
        PlanningTrigger.CAPABILITY_UNAVAILABLE,
        PlanningTrigger.MODEL_UNAVAILABLE,
        PlanningTrigger.ASSUMPTION_INVALIDATED,
        PlanningTrigger.FEASIBILITY_BLOCKER,
    }
)


class PlanningProposalFailureEvidenceResolver:
    """Resolve failure/replanning observations from durable Planning proposal history."""

    def __init__(self, planning: PlanningEvidenceService) -> None:
        self._planning = planning

    async def resolve(
        self,
        reference: PlanningFailureSourceRef,
        *,
        project_id: str | None,
    ) -> ResolvedLearningSource:
        task = await self._planning.kernel.get_task(reference.task_id)
        record = next(
            (
                candidate
                for candidate in self._planning.history(reference.task_id)
                if candidate.proposal.proposal_id == reference.proposal_id
            ),
            None,
        )
        if record is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                "Learning Planning evidence was not found in canonical proposal history",
                details={
                    "task_id": reference.task_id,
                    "proposal_id": reference.proposal_id,
                },
            )
        if record.proposal.task_id != reference.task_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Learning Planning evidence does not belong to the requested canonical Task",
                details={
                    "task_id": reference.task_id,
                    "proposal_id": reference.proposal_id,
                },
            )
        if (
            record.proposal.trigger not in _FAILURE_PLANNING_TRIGGERS
            and record.status is not ProposalStatus.INVALID
        ):
            raise ContractError(
                ErrorCode.CONFLICT,
                "Learning Planning failure evidence requires a failure/replanning proposal",
                details={
                    "proposal_id": reference.proposal_id,
                    "trigger": record.proposal.trigger.value,
                    "status": record.status.value,
                },
            )
        _require_project_scope(
            expected_project_id=project_id,
            actual_project_id=task.task.project_id,
            source_label="Planning",
            source_id=reference.proposal_id,
        )
        if reference.revision is not None and reference.revision != record.revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Learning Planning evidence revision is stale or mismatched",
                details={
                    "proposal_id": reference.proposal_id,
                    "expected_revision": reference.revision,
                    "actual_revision": record.revision,
                },
            )
        digest = record.proposal.digest
        if reference.digest is not None and reference.digest != digest:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Learning Planning evidence digest is stale or mismatched",
                details={"proposal_id": reference.proposal_id},
            )
        return ResolvedLearningSource(
            source=LearningReference(
                kind="planning_failure",
                resource_id=reference.proposal_id,
                revision=str(record.revision),
                digest=digest,
            ),
            supporting_refs=(
                LearningReference(
                    kind="task",
                    resource_id=reference.task_id,
                    revision=str(task.revision),
                ),
            ),
        )


def _require_owner_project_consistency(task: TaskState, run: RunState) -> None:
    if task.task.project_id != run.run.project_id:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "canonical Task/Run project ownership is inconsistent",
            details={
                "task_id": task.task_id,
                "run_id": run.run_id,
                "task_project_id": task.task.project_id,
                "run_project_id": run.run.project_id,
            },
        )


def _require_project_scope(
    *,
    expected_project_id: str | None,
    actual_project_id: str | None,
    source_label: str,
    source_id: str,
) -> None:
    if expected_project_id != actual_project_id:
        raise ContractError(
            ErrorCode.FORBIDDEN,
            f"Learning Candidate project scope does not match canonical {source_label} evidence",
            details={
                "source_id": source_id,
                "candidate_project_id": expected_project_id,
                "evidence_project_id": actual_project_id,
            },
        )


def _run_projection_digest(run: RunState) -> str:
    payload = {
        "run_id": run.run_id,
        "task_id": run.task_id,
        "revision": run.revision,
        "status": run.status.value,
        "attempt": run.attempt,
        "subject_type": run.run.subject_type,
        "subject_id": run.run.subject_id,
        "project_id": run.run.project_id,
        "created_at": run.run.created_at.isoformat(),
        "updated_at": run.run.updated_at.isoformat(),
        "started_at": None if run.run.started_at is None else run.run.started_at.isoformat(),
        "finished_at": None if run.run.finished_at is None else run.run.finished_at.isoformat(),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


__all__ = [
    "KernelRunFailureEvidenceResolver",
    "PlanningFailureEvidenceResolver",
    "PlanningFailureSourceRef",
    "PlanningProposalFailureEvidenceResolver",
    "ResolvedLearningSource",
    "RunFailureEvidenceResolver",
    "RunFailureSourceRef",
]
