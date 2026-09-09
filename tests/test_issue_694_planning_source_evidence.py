from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef, Task, TaskStatus, new_id
from ai_multi_agent_platform.kernel.models import TaskState
from ai_multi_agent_platform.learning import (
    InMemoryLearningRepository,
    LearningGatePlan,
    LearningQualityGate,
    LearningService,
    LearningSourceBridge,
    LearningSourceType,
    LearningTarget,
    LearningTargetType,
    PlanningFailureSourceRef,
    PlanningProposalFailureEvidenceResolver,
    PromotionRegistry,
)
from ai_multi_agent_platform.planning.models import (
    PlannerDescriptor,
    PlannerKind,
    PlanningTrigger,
    PlanProposal,
    ProposalRecord,
    ProposalStatus,
    ProposalValidation,
)
from ai_multi_agent_platform.security import (
    AuthorizationGate,
    LocalAuthorizationProvider,
    RiskClassification,
)

OWNER = OwnerRef(type="user", id="issue-694-planning-owner")


class _KernelStub:
    def __init__(self, task: TaskState) -> None:
        self.task = task

    async def get_task(self, task_id: str) -> TaskState:
        if task_id != self.task.task_id:
            raise ContractError(ErrorCode.NOT_FOUND, "canonical Task not found")
        return self.task


class _PlanningStub:
    def __init__(self, task: TaskState, records: tuple[ProposalRecord, ...]) -> None:
        self.kernel = _KernelStub(task)
        self.records = records

    def history(self, task_id: str) -> tuple[ProposalRecord, ...]:
        return tuple(record for record in self.records if record.proposal.task_id == task_id)


def _task(project_id: str) -> TaskState:
    return TaskState(
        task=Task(
            title="Issue 694 Planning evidence",
            owner_ref=OWNER,
            status=TaskStatus.RUNNING,
            project_id=project_id,
        ),
        revision=3,
    )


def _proposal(
    task: TaskState,
    *,
    trigger: PlanningTrigger = PlanningTrigger.TERMINAL_FAILURE,
    status: ProposalStatus = ProposalStatus.VALIDATED,
    revision: int = 2,
) -> ProposalRecord:
    proposal = PlanProposal(
        proposal_id=new_id("plan_proposal"),
        task_id=task.task_id,
        task_revision=task.revision,
        plan_revision=2,
        trigger=trigger,
        summary="Bounded replanning proposal",
        steps=(),
        planner=PlannerDescriptor(
            planner_id="issue-694-planner",
            kind=PlannerKind.DETERMINISTIC,
        ),
        reason="canonical Planning failure evidence",
        evidence_refs=("failure-evidence",),
    )
    validation = (
        ProposalValidation(valid=False, errors=("invalid proposal",))
        if status is ProposalStatus.INVALID
        else ProposalValidation(valid=True)
    )
    return ProposalRecord(
        proposal=proposal,
        status=status,
        idempotency_key=f"proposal:{proposal.proposal_id}",
        validation=validation,
        trigger_fingerprint=f"fingerprint:{proposal.proposal_id}",
        revision=revision,
    )


def _learning() -> LearningService:
    return LearningService(
        InMemoryLearningRepository(),
        quality_gate=LearningQualityGate(),
        promotion_registry=PromotionRegistry(()),
        authorization_gate=AuthorizationGate(LocalAuthorizationProvider(())),
    )


def _target() -> LearningTarget:
    return LearningTarget(
        resource_type=LearningTargetType.AGENT,
        resource_id=new_id("agent"),
        revision=1,
    )


def _create_pattern(
    bridge: LearningSourceBridge,
    project_id: str,
    source_refs: tuple[PlanningFailureSourceRef, ...],
):
    return asyncio.run(
        bridge.from_planning_failure_pattern(
            source_refs=source_refs,
            problem="Repeated canonical Planning failure.",
            target=_target(),
            improvement_type="owner_revision",
            expected_benefit="Reduce repeated replanning failures.",
            risk=RiskClassification.STANDARD,
            gate_plan=LearningGatePlan(
                policy_id="issue-694-planning",
                policy_version=1,
                require_evaluation=True,
                evaluation_suite_refs=("issue-694@1",),
            ),
            creator_ref="user:issue-694",
            proposed_change={"description": "Use bounded replanning."},
            project_id=project_id,
        )
    )


def test_planning_failure_pattern_binds_two_canonical_failures() -> None:
    project_id = new_id("project")
    task = _task(project_id)
    first = _proposal(task, trigger=PlanningTrigger.TERMINAL_FAILURE, revision=2)
    second = _proposal(task, trigger=PlanningTrigger.RETRY_EXHAUSTED, revision=3)
    planning = _PlanningStub(task, (first, second))
    bridge = LearningSourceBridge(
        _learning(),
        planning_failures=PlanningProposalFailureEvidenceResolver(planning),
    )

    candidate, created = _create_pattern(
        bridge,
        project_id,
        (
            PlanningFailureSourceRef(task.task_id, first.proposal.proposal_id),
            PlanningFailureSourceRef(task.task_id, second.proposal.proposal_id),
        ),
    )

    assert created is True
    assert candidate.source_type is LearningSourceType.PLANNING_FAILURE_PATTERN
    assert {reference.resource_id for reference in candidate.source_refs} == {
        first.proposal.proposal_id,
        second.proposal.proposal_id,
    }
    assert {reference.revision for reference in candidate.source_refs} == {"2", "3"}
    assert {reference.digest for reference in candidate.source_refs} == {
        first.proposal.digest,
        second.proposal.digest,
    }


def test_duplicate_planning_evidence_does_not_satisfy_pattern_minimum() -> None:
    project_id = new_id("project")
    task = _task(project_id)
    failed = _proposal(task)
    bridge = LearningSourceBridge(
        _learning(),
        planning_failures=PlanningProposalFailureEvidenceResolver(_PlanningStub(task, (failed,))),
    )
    reference = PlanningFailureSourceRef(task.task_id, failed.proposal.proposal_id)

    with pytest.raises(ContractError) as error:
        _create_pattern(bridge, project_id, (reference, reference))

    assert error.value.code is ErrorCode.INVALID_REQUEST


def test_missing_and_non_failure_planning_evidence_are_rejected() -> None:
    project_id = new_id("project")
    task = _task(project_id)
    manual = _proposal(task, trigger=PlanningTrigger.MANUAL)
    resolver = PlanningProposalFailureEvidenceResolver(_PlanningStub(task, (manual,)))

    with pytest.raises(ContractError) as missing:
        asyncio.run(
            resolver.resolve(
                PlanningFailureSourceRef(task.task_id, new_id("plan_proposal")),
                project_id=project_id,
            )
        )
    assert missing.value.code is ErrorCode.NOT_FOUND

    with pytest.raises(ContractError) as not_failure:
        asyncio.run(
            resolver.resolve(
                PlanningFailureSourceRef(task.task_id, manual.proposal.proposal_id),
                project_id=project_id,
            )
        )
    assert not_failure.value.code is ErrorCode.CONFLICT


def test_invalid_planning_record_is_qualifying_failure_evidence() -> None:
    project_id = new_id("project")
    task = _task(project_id)
    invalid = _proposal(
        task,
        trigger=PlanningTrigger.MANUAL,
        status=ProposalStatus.INVALID,
    )
    resolver = PlanningProposalFailureEvidenceResolver(_PlanningStub(task, (invalid,)))

    resolved = asyncio.run(
        resolver.resolve(
            PlanningFailureSourceRef(task.task_id, invalid.proposal.proposal_id),
            project_id=project_id,
        )
    )

    assert resolved.source.resource_id == invalid.proposal.proposal_id
    assert resolved.source.digest == invalid.proposal.digest


def test_planning_revision_digest_and_project_scope_are_verified() -> None:
    project_id = new_id("project")
    task = _task(project_id)
    failed = _proposal(task)
    resolver = PlanningProposalFailureEvidenceResolver(_PlanningStub(task, (failed,)))

    with pytest.raises(ContractError) as stale:
        asyncio.run(
            resolver.resolve(
                PlanningFailureSourceRef(
                    task.task_id,
                    failed.proposal.proposal_id,
                    revision=failed.revision + 1,
                ),
                project_id=project_id,
            )
        )
    assert stale.value.code is ErrorCode.CONFLICT

    with pytest.raises(ContractError) as digest:
        asyncio.run(
            resolver.resolve(
                PlanningFailureSourceRef(
                    task.task_id,
                    failed.proposal.proposal_id,
                    digest="sha256:wrong",
                ),
                project_id=project_id,
            )
        )
    assert digest.value.code is ErrorCode.CONFLICT

    with pytest.raises(ContractError) as scope:
        asyncio.run(
            resolver.resolve(
                PlanningFailureSourceRef(task.task_id, failed.proposal.proposal_id),
                project_id=new_id("project"),
            )
        )
    assert scope.value.code is ErrorCode.FORBIDDEN
