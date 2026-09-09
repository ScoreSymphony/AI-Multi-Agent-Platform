from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest

from ai_multi_agent_platform.agents import AgentInstructions, AgentProfile, InstructionSource
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane import ActorContext, RequestContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef, Run, RunStatus, Task, TaskStatus, new_id
from ai_multi_agent_platform.kernel.models import RunState, TaskState
from ai_multi_agent_platform.learning import (
    InMemoryLearningRepository,
    KernelRunFailureEvidenceResolver,
    LearningGatePlan,
    LearningQualityGate,
    LearningReference,
    LearningService,
    LearningSourceBridge,
    LearningSourceType,
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
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
    RiskClassification,
)

OWNER = OwnerRef(type="user", id="issue-694-owner")
CREATOR = "user:issue-694"


class _KernelStub:
    def __init__(self) -> None:
        self.tasks: dict[str, TaskState] = {}
        self.runs: dict[tuple[str, str], RunState] = {}

    async def get_task(self, task_id: str) -> TaskState:
        try:
            return self.tasks[task_id]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, "canonical Task not found") from exc

    async def get_run(self, task_id: str, run_id: str) -> RunState:
        try:
            return self.runs[(task_id, run_id)]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, "canonical Run not found") from exc


class _PlanningStub:
    def __init__(self, kernel: _KernelStub, records: tuple[ProposalRecord, ...]) -> None:
        self.kernel = kernel
        self._records = records

    def history(self, task_id: str) -> tuple[ProposalRecord, ...]:
        return tuple(record for record in self._records if record.proposal.task_id == task_id)


class _ResearchRepositoryStub:
    def __init__(
        self,
        *,
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

    def get_item(self, research_item_id: str) -> ResearchItem:
        assert research_item_id == self.item.research_item_id
        return self.item

    def get_claim(self, claim_id: str) -> Claim:
        assert claim_id == self.claim.claim_id
        return self.claim

    def get_source(self, source_id: str) -> SourceRecord:
        assert source_id == self.source.source_id
        return self.source

    def get_observation(self, observation_id: str) -> SourceObservation:
        assert observation_id == self.observation.observation_id
        return self.observation

    def get_evidence(self, evidence_id: str) -> EvidenceRecord:
        assert evidence_id == self.evidence.evidence_id
        return self.evidence


def _learning() -> LearningService:
    return LearningService(
        InMemoryLearningRepository(),
        quality_gate=LearningQualityGate(),
        promotion_registry=PromotionRegistry(()),
        authorization_gate=AuthorizationGate(LocalAuthorizationProvider(())),
    )


def _gate_plan() -> LearningGatePlan:
    return LearningGatePlan(
        policy_id="issue-694",
        policy_version=1,
        require_evaluation=True,
        evaluation_suite_refs=("issue-694-suite@1",),
    )


def _target() -> LearningTarget:
    return LearningTarget(
        resource_type=LearningTargetType.AGENT,
        resource_id=new_id("agent"),
        revision=1,
    )


def _task_state(*, project_id: str) -> TaskState:
    task = Task(
        title="Issue 694 canonical task",
        owner_ref=OWNER,
        status=TaskStatus.RUNNING,
        project_id=project_id,
    )
    return TaskState(task=task, revision=3)


def _run_state(
    task: TaskState,
    *,
    status: RunStatus = RunStatus.FAILED,
    project_id: str | None = None,
    revision: int = 4,
) -> RunState:
    run = Run(
        subject_type="task",
        subject_id=task.task_id,
        owner_ref=OWNER,
        correlation_id=task.task_id,
        status=status,
        project_id=task.task.project_id if project_id is None else project_id,
    )
    return RunState(run=run, revision=revision)


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
        reason="canonical failure evidence",
        evidence_refs=("canonical-failure",),
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


def _candidate_kwargs(project_id: str) -> dict[str, object]:
    return {
        "problem": "Repeated canonical failure pattern.",
        "target": _target(),
        "improvement_type": "owner_revision",
        "expected_benefit": "Reduce repeated failures.",
        "risk": RiskClassification.STANDARD,
        "gate_plan": _gate_plan(),
        "creator_ref": CREATOR,
        "proposed_change": {"description": "Use the bounded method."},
        "project_id": project_id,
    }


def test_run_failure_pattern_binds_two_canonical_failures() -> None:
    project_id = new_id("project")
    task = _task_state(project_id=project_id)
    first = _run_state(task)
    second = _run_state(task, revision=5)
    kernel = _KernelStub()
    kernel.tasks[task.task_id] = task
    kernel.runs[(task.task_id, first.run_id)] = first
    kernel.runs[(task.task_id, second.run_id)] = second
    learning = _learning()
    bridge = LearningSourceBridge(
        learning,
        run_failures=KernelRunFailureEvidenceResolver(kernel),
    )

    candidate, created = asyncio.run(
        bridge.from_run_failure_pattern(
            source_refs=(
                RunFailureSourceRef(task.task_id, first.run_id),
                RunFailureSourceRef(task.task_id, second.run_id),
            ),
            **_candidate_kwargs(project_id),
        )
    )

    assert created is True
    assert candidate.source_type is LearningSourceType.RUN_FAILURE_PATTERN
    assert {reference.resource_id for reference in candidate.source_refs} == {
        first.run_id,
        second.run_id,
    }
    assert {reference.revision for reference in candidate.source_refs} == {"4", "5"}
    assert all(reference.digest and reference.digest.startswith("sha256:") for reference in candidate.source_refs)
    assert any(
        reference.kind == "task"
        and reference.resource_id == task.task_id
        and reference.revision == str(task.revision)
        for reference in candidate.evidence_refs
    )


def test_duplicate_run_evidence_does_not_satisfy_pattern_minimum() -> None:
    project_id = new_id("project")
    task = _task_state(project_id=project_id)
    failed = _run_state(task)
    kernel = _KernelStub()
    kernel.tasks[task.task_id] = task
    kernel.runs[(task.task_id, failed.run_id)] = failed
    bridge = LearningSourceBridge(
        _learning(),
        run_failures=KernelRunFailureEvidenceResolver(kernel),
    )
    reference = RunFailureSourceRef(task.task_id, failed.run_id)

    with pytest.raises(ContractError) as error:
        asyncio.run(
            bridge.from_run_failure_pattern(
                source_refs=(reference, reference),
                **_candidate_kwargs(project_id),
            )
        )

    assert error.value.code is ErrorCode.INVALID_REQUEST


def test_missing_and_non_failure_run_evidence_fail_closed() -> None:
    project_id = new_id("project")
    task = _task_state(project_id=project_id)
    succeeded = _run_state(task, status=RunStatus.SUCCEEDED)
    kernel = _KernelStub()
    kernel.tasks[task.task_id] = task
    kernel.runs[(task.task_id, succeeded.run_id)] = succeeded
    resolver = KernelRunFailureEvidenceResolver(kernel)

    with pytest.raises(ContractError) as missing:
        asyncio.run(
            resolver.resolve(
                RunFailureSourceRef(task.task_id, new_id("run")),
                project_id=project_id,
            )
        )
    assert missing.value.code is ErrorCode.NOT_FOUND

    with pytest.raises(ContractError) as not_failure:
        asyncio.run(
            resolver.resolve(
                RunFailureSourceRef(task.task_id, succeeded.run_id),
                project_id=project_id,
            )
        )
    assert not_failure.value.code is ErrorCode.CONFLICT


def test_run_revision_digest_and_project_scope_are_verified() -> None:
    project_id = new_id("project")
    task = _task_state(project_id=project_id)
    failed = _run_state(task)
    kernel = _KernelStub()
    kernel.tasks[task.task_id] = task
    kernel.runs[(task.task_id, failed.run_id)] = failed
    resolver = KernelRunFailureEvidenceResolver(kernel)

    with pytest.raises(ContractError) as stale_revision:
        asyncio.run(
            resolver.resolve(
                RunFailureSourceRef(task.task_id, failed.run_id, revision=failed.revision + 1),
                project_id=project_id,
            )
        )
    assert stale_revision.value.code is ErrorCode.CONFLICT

    with pytest.raises(ContractError) as wrong_digest:
        asyncio.run(
            resolver.resolve(
                RunFailureSourceRef(task.task_id, failed.run_id, digest="sha256:wrong"),
                project_id=project_id,
            )
        )
    assert wrong_digest.value.code is ErrorCode.CONFLICT

    with pytest.raises(ContractError) as cross_project:
        asyncio.run(
            resolver.resolve(
                RunFailureSourceRef(task.task_id, failed.run_id),
                project_id=new_id("project"),
            )
        )
    assert cross_project.value.code is ErrorCode.FORBIDDEN


def test_mixed_valid_invalid_run_pattern_fails_as_a_whole() -> None:
    project_id = new_id("project")
    task = _task_state(project_id=project_id)
    failed = _run_state(task)
    succeeded = _run_state(task, status=RunStatus.SUCCEEDED)
    kernel = _KernelStub()
    kernel.tasks[task.task_id] = task
    kernel.runs[(task.task_id, failed.run_id)] = failed
    kernel.runs[(task.task_id, succeeded.run_id)] = succeeded
    bridge = LearningSourceBridge(
        _learning(),
        run_failures=KernelRunFailureEvidenceResolver(kernel),
    )

    with pytest.raises(ContractError) as error:
        asyncio.run(
            bridge.from_run_failure_pattern(
                source_refs=(
                    RunFailureSourceRef(task.task_id, failed.run_id),
                    RunFailureSourceRef(task.task_id, succeeded.run_id),
                ),
                **_candidate_kwargs(project_id),
            )
        )

    assert error.value.code is ErrorCode.CONFLICT


def test_planning_failure_pattern_binds_canonical_replanning_history() -> None:
    project_id = new_id("project")
    task = _task_state(project_id=project_id)
    kernel = _KernelStub()
    kernel.tasks[task.task_id] = task
    first = _proposal(task, trigger=PlanningTrigger.TERMINAL_FAILURE, revision=2)
    second = _proposal(task, trigger=PlanningTrigger.RETRY_EXHAUSTED, revision=3)
    planning = _PlanningStub(kernel, (first, second))
    learning = _learning()
    bridge = LearningSourceBridge(
        learning,
        planning_failures=PlanningProposalFailureEvidenceResolver(planning),
    )

    candidate, created = asyncio.run(
        bridge.from_planning_failure_pattern(
            source_refs=(
                PlanningFailureSourceRef(task.task_id, first.proposal.proposal_id),
                PlanningFailureSourceRef(task.task_id, second.proposal.proposal_id),
            ),
            **_candidate_kwargs(project_id),
        )
    )

    assert created is True
    assert candidate.source_type is LearningSourceType.PLANNING_FAILURE_PATTERN
    assert {reference.resource_id for reference in candidate.source_refs} == {
        first.proposal.proposal_id,
        second.proposal.proposal_id,
    }
    assert {reference.digest for reference in candidate.source_refs} == {
        first.proposal.digest,
        second.proposal.digest,
    }


def test_duplicate_and_non_failure_planning_evidence_are_rejected() -> None:
    project_id = new_id("project")
    task = _task_state(project_id=project_id)
    kernel = _KernelStub()
    kernel.tasks[task.task_id] = task
    failed = _proposal(task)
    manual = _proposal(task, trigger=PlanningTrigger.MANUAL)
    planning = _PlanningStub(kernel, (failed, manual))
    resolver = PlanningProposalFailureEvidenceResolver(planning)
    bridge = LearningSourceBridge(_learning(), planning_failures=resolver)
    duplicate = PlanningFailureSourceRef(task.task_id, failed.proposal.proposal_id)

    with pytest.raises(ContractError) as duplicate_error:
        asyncio.run(
            bridge.from_planning_failure_pattern(
                source_refs=(duplicate, duplicate),
                **_candidate_kwargs(project_id),
            )
        )
    assert duplicate_error.value.code is ErrorCode.INVALID_REQUEST

    with pytest.raises(ContractError) as unrelated:
        asyncio.run(
            resolver.resolve(
                PlanningFailureSourceRef(task.task_id, manual.proposal.proposal_id),
                project_id=project_id,
            )
        )
    assert unrelated.value.code is ErrorCode.CONFLICT


def test_planning_revision_digest_missing_and_project_scope_fail_closed() -> None:
    project_id = new_id("project")
    task = _task_state(project_id=project_id)
    kernel = _KernelStub()
    kernel.tasks[task.task_id] = task
    failed = _proposal(task)
    resolver = PlanningProposalFailureEvidenceResolver(_PlanningStub(kernel, (failed,)))

    with pytest.raises(ContractError) as missing:
        asyncio.run(
            resolver.resolve(
                PlanningFailureSourceRef(task.task_id, new_id("plan_proposal")),
                project_id=project_id,
            )
        )
    assert missing.value.code is ErrorCode.NOT_FOUND

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
                    digest="wrong",
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


def _research_fixture(project_id: str) -> tuple[ResearchService, EvidenceRecord]:
    item_id = new_id("research_item")
    claim_id = new_id("research_claim")
    source_id = new_id("research_source")
    observation_id = new_id("research_observation")
    evidence_id = new_id("research_evidence")
    item = ResearchItem(
        title="Issue 694 research",
        question="Which bounded method is supported?",
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
        text="Method B is better supported.",
        category="method",
        claim_id=claim_id,
        evidence_ids=(evidence_id,),
    )
    source = SourceRecord(
        research_item_id=item_id,
        source_type=ResearchSourceType.DOCUMENT,
        locator="artifact://issue-694-source",
        title="Issue 694 source",
        source_id=source_id,
        current_observation_id=observation_id,
        observation_ids=(observation_id,),
    )
    observation = SourceObservation(
        source_id=source_id,
        research_item_id=item_id,
        retrieved_at=datetime.now(UTC),
        observation_id=observation_id,
        revision="r7",
        content_digest="sha256:observation-694",
        identity_proven=True,
    )
    evidence = EvidenceRecord(
        research_item_id=item_id,
        source_id=source_id,
        source_observation_id=observation_id,
        claim_id=claim_id,
        relation=EvidenceRelation.SUPPORTS,
        retrieved_at=datetime.now(UTC),
        evidence_id=evidence_id,
        source_revision="r7",
        source_content_digest="sha256:observation-694",
    )
    repository = _ResearchRepositoryStub(
        item=item,
        claim=claim,
        source=source,
        observation=observation,
        evidence=evidence,
    )
    return cast(ResearchService, SimpleNamespace(repository=repository)), evidence


def test_research_source_preserves_full_canonical_chain() -> None:
    project_id = new_id("project")
    research, evidence = _research_fixture(project_id)
    bridge = LearningSourceBridge(_learning(), research=research)

    candidate, created = bridge.from_research_evidence(
        evidence.evidence_id,
        **_candidate_kwargs(project_id),
    )

    assert created is True
    assert candidate.source_type is LearningSourceType.RESEARCH_EVIDENCE
    refs = {reference.kind: reference for reference in candidate.evidence_refs}
    assert refs["research_evidence"].digest == evidence.digest
    assert refs["research_item"].revision == "1"
    assert refs["research_claim"].revision == "1"
    assert refs["research_source"].resource_id == evidence.source_id
    assert refs["research_source_observation"].revision == "r7"
    assert refs["research_source_observation"].digest == "sha256:observation-694"


def test_broken_research_chain_and_project_mismatch_are_rejected() -> None:
    project_id = new_id("project")
    research, evidence = _research_fixture(project_id)
    repository = cast(_ResearchRepositoryStub, research.repository)
    repository.claim = replace(repository.claim, research_item_id=new_id("research_item"))
    bridge = LearningSourceBridge(_learning(), research=research)

    with pytest.raises(ContractError) as broken:
        bridge.from_research_evidence(
            evidence.evidence_id,
            **_candidate_kwargs(project_id),
        )
    assert broken.value.code is ErrorCode.CONTRACT_VIOLATION

    research, evidence = _research_fixture(project_id)
    bridge = LearningSourceBridge(_learning(), research=research)
    kwargs = _candidate_kwargs(project_id)
    kwargs["project_id"] = new_id("project")
    with pytest.raises(ContractError) as wrong_project:
        bridge.from_research_evidence(evidence.evidence_id, **kwargs)
    assert wrong_project.value.code is ErrorCode.FORBIDDEN


def test_operator_proposal_cannot_impersonate_trusted_source_kind() -> None:
    bridge = LearningSourceBridge(_learning())
    project_id = new_id("project")

    with pytest.raises(ContractError) as spoofed:
        bridge.operator_proposal(
            proposal_ref=LearningReference(
                kind="verification_result",
                resource_id="verification-result-spoof",
            ),
            **_candidate_kwargs(project_id),
        )
    assert spoofed.value.code is ErrorCode.INVALID_REQUEST

    candidate, created = bridge.operator_proposal(
        proposal_ref=LearningReference(kind="operator_proposal", resource_id="proposal-694"),
        **_candidate_kwargs(project_id),
    )
    assert created is True
    assert candidate.source_type is LearningSourceType.OPERATOR_PROPOSAL
    assert candidate.source_refs[0].kind == "operator_proposal"


def test_public_learning_propose_ignores_source_type_spoofing(tmp_path) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "public-boundary", secure_cookie=False)
        )
        project = deployment.scopes.create_project(
            key="issue-694-project",
            name="Issue 694 project",
            owner_type="user",
            owner_id="issue-694-owner",
        )
        agent = deployment.agents.create_agent(
            AgentProfile(
                name="Issue 694 Agent",
                role="worker",
                description="Source-boundary target.",
                instructions=AgentInstructions(
                    role=InstructionSource(content="Use the bounded method.", version="1")
                ),
            ),
            owner_ref=OWNER,
            project_id=project.id,
        )
        principal = "user:issue-694-operator"
        deployment.authorization.register(
            LocalPrincipalPolicy(
                principal_ref=principal,
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.CREATE, AuthorizationAction.MODIFY}),
                resource_types=frozenset({ResourceType.GENERIC}),
                project_ids=frozenset({project.id}),
            )
        )
        context = RequestContext(
            request_id="issue-694-propose",
            correlation_id="issue-694-propose",
            idempotency_key="issue-694-propose",
            actor=ActorContext(principal_ref=principal, actor_type=ActorType.HUMAN.value),
        )

        created = await deployment.control_plane.execute_command(
            context,
            "learning.propose",
            "learning-candidates",
            {
                "source_type": "verification",
                "problem": "Operator-authored issue 694 proposal.",
                "target": {
                    "resource_type": "agent",
                    "resource_id": agent.agent_id,
                    "revision": agent.revision,
                },
                "improvement_type": "owner_revision",
                "expected_benefit": "Preserve source authority boundaries.",
                "risk": "standard",
                "gate_plan": {
                    "policy_id": "issue-694",
                    "policy_version": 1,
                    "require_evaluation": True,
                    "require_verification": False,
                    "require_regression_free": True,
                    "evaluation_suite_refs": ["issue-694-suite@1"],
                    "verification_policy_refs": [],
                },
                "source_refs": [
                    {"kind": "operator_proposal", "resource_id": "public-proposal-694"}
                ],
                "proposed_change": {"description": "Bounded change."},
                "project_id": project.id,
            },
        )

        assert created["source_type"] == LearningSourceType.OPERATOR_PROPOSAL.value
        candidate = deployment.learning.service.get_candidate(str(created["id"]))
        assert candidate.source_type is LearningSourceType.OPERATOR_PROPOSAL

    asyncio.run(scenario())
