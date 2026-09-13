from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

from ai_multi_agent_platform.domain import OwnerRef, Run, RunStatus, Task, TaskStatus, new_id
from ai_multi_agent_platform.kernel.models import RunState, TaskState
from ai_multi_agent_platform.learning import (
    InMemoryLearningRepository,
    KernelRunFailureEvidenceResolver,
    LearningGatePlan,
    LearningQualityGate,
    LearningService,
    LearningSourceBridge,
    LearningTarget,
    LearningTargetType,
    PlanningFailureSourceRef,
    PlanningProposalFailureEvidenceResolver,
    PromotionRegistry,
    RunFailureSourceRef,
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
from ai_multi_agent_platform.research import ResearchService
from ai_multi_agent_platform.research.models import (
    Claim,
    EvidenceRecord,
    EvidenceRelation,
    ResearchClass,
    ResearchItem,
    ResearchSourceType,
    SourceObservation,
    SourceRecord,
)
from ai_multi_agent_platform.security import (
    AuthorizationGate,
    LocalAuthorizationProvider,
    RiskClassification,
)

OWNER = OwnerRef(type="user", id="issue-694-immutability")


class _Kernel:
    def __init__(self, task: TaskState, runs: tuple[RunState, ...]) -> None:
        self.task = task
        self.runs = {run.run_id: run for run in runs}

    async def get_task(self, task_id: str) -> TaskState:
        assert task_id == self.task.task_id
        return self.task

    async def get_run(self, task_id: str, run_id: str) -> RunState:
        assert task_id == self.task.task_id
        return self.runs[run_id]


class _Planning:
    def __init__(self, kernel: _Kernel, records: tuple[ProposalRecord, ...]) -> None:
        self.kernel = kernel
        self.records = records

    def history(self, task_id: str) -> tuple[ProposalRecord, ...]:
        assert task_id == self.kernel.task.task_id
        return self.records


class _ResearchRepository:
    def __init__(
        self,
        item: ResearchItem,
        claim: Claim,
        source: SourceRecord,
        observation: SourceObservation,
        evidence: EvidenceRecord,
    ) -> None:
        self.item = item
        self.claim = claim
        self.source = source
        self.observation = observation
        self.evidence = evidence

    def get_item(self, resource_id: str) -> ResearchItem:
        assert resource_id == self.item.research_item_id
        return self.item

    def get_claim(self, resource_id: str) -> Claim:
        assert resource_id == self.claim.claim_id
        return self.claim

    def get_source(self, resource_id: str) -> SourceRecord:
        assert resource_id == self.source.source_id
        return self.source

    def get_observation(self, resource_id: str) -> SourceObservation:
        assert resource_id == self.observation.observation_id
        return self.observation

    def get_evidence(self, resource_id: str) -> EvidenceRecord:
        assert resource_id == self.evidence.evidence_id
        return self.evidence


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


def _gate() -> LearningGatePlan:
    return LearningGatePlan(
        policy_id="issue-694-immutability",
        policy_version=1,
        require_evaluation=True,
        evaluation_suite_refs=("issue-694@1",),
    )


def test_canonical_run_planning_and_research_sources_are_read_only() -> None:
    project_id = new_id("project")
    task = TaskState(
        task=Task(
            title="immutable source task",
            owner_ref=OWNER,
            status=TaskStatus.RUNNING,
            project_id=project_id,
        ),
        revision=2,
    )
    run = RunState(
        run=Run(
            subject_type="task",
            subject_id=task.task_id,
            owner_ref=OWNER,
            correlation_id=task.task_id,
            status=RunStatus.FAILED,
            project_id=project_id,
        ),
        revision=3,
    )
    kernel = _Kernel(task, (run,))
    run_snapshot = (run.revision, run.status, run.run.updated_at, run.run.finished_at)

    asyncio.run(
        KernelRunFailureEvidenceResolver(kernel).resolve(
            RunFailureSourceRef(task.task_id, run.run_id),
            project_id=project_id,
        )
    )
    assert (run.revision, run.status, run.run.updated_at, run.run.finished_at) == run_snapshot

    proposal = PlanProposal(
        proposal_id=new_id("plan_proposal"),
        task_id=task.task_id,
        task_revision=task.revision,
        plan_revision=2,
        trigger=PlanningTrigger.TERMINAL_FAILURE,
        summary="immutable planning evidence",
        steps=(),
        planner=PlannerDescriptor(
            planner_id="issue-694",
            kind=PlannerKind.DETERMINISTIC,
        ),
        reason="canonical failed run",
        evidence_refs=(run.run_id,),
    )
    record = ProposalRecord(
        proposal=proposal,
        status=ProposalStatus.VALIDATED,
        idempotency_key="issue-694-planning",
        validation=ProposalValidation(valid=True),
        trigger_fingerprint="issue-694-planning-fingerprint",
        revision=2,
    )
    planning = _Planning(kernel, (record,))
    planning_snapshot = (record.revision, record.status, record.proposal.digest)

    asyncio.run(
        PlanningProposalFailureEvidenceResolver(planning).resolve(
            PlanningFailureSourceRef(task.task_id, proposal.proposal_id),
            project_id=project_id,
        )
    )
    assert (record.revision, record.status, record.proposal.digest) == planning_snapshot

    item_id = new_id("research_item")
    claim_id = new_id("research_claim")
    source_id = new_id("research_source")
    observation_id = new_id("research_observation")
    evidence_id = new_id("research_evidence")
    item = ResearchItem(
        title="immutable research",
        question="is the evidence unchanged?",
        research_class=ResearchClass.PROJECT_RESEARCH,
        owner_ref=OWNER,
        research_item_id=item_id,
        project_id=project_id,
        source_ids=(source_id,),
        claim_ids=(claim_id,),
        evidence_ids=(evidence_id,),
    )
    claim = Claim(
        research_item_id=item_id,
        text="The source remains immutable.",
        category="immutability",
        claim_id=claim_id,
        evidence_ids=(evidence_id,),
    )
    source = SourceRecord(
        research_item_id=item_id,
        source_type=ResearchSourceType.DOCUMENT,
        locator="artifact://issue-694-immutable",
        title="immutable source",
        source_id=source_id,
        current_observation_id=observation_id,
        observation_ids=(observation_id,),
    )
    observation = SourceObservation(
        source_id=source_id,
        research_item_id=item_id,
        retrieved_at=datetime.now(UTC),
        observation_id=observation_id,
        revision="r1",
        content_digest="sha256:immutable-observation",
    )
    evidence = EvidenceRecord(
        research_item_id=item_id,
        source_id=source_id,
        source_observation_id=observation_id,
        claim_id=claim_id,
        relation=EvidenceRelation.SUPPORTS,
        retrieved_at=datetime.now(UTC),
        evidence_id=evidence_id,
    )
    repository = _ResearchRepository(item, claim, source, observation, evidence)
    research = cast(ResearchService, SimpleNamespace(repository=repository))
    research_snapshot = (
        item.digest,
        claim.digest,
        evidence.digest,
        observation.binding,
        source.observation_ids,
    )

    LearningSourceBridge(_learning(), research=research).from_research_evidence(
        evidence.evidence_id,
        problem="immutable source evidence",
        target=_target(),
        improvement_type="owner_revision",
        expected_benefit="retain evidence history",
        risk=RiskClassification.STANDARD,
        gate_plan=_gate(),
        creator_ref="user:issue-694",
        proposed_change={"description": "read-only evidence"},
        project_id=project_id,
    )
    assert (
        item.digest,
        claim.digest,
        evidence.digest,
        observation.binding,
        source.observation_ids,
    ) == research_snapshot
