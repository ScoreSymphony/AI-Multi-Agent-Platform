"""Canonical source-owner and authorization acceptance coverage for #651."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from ai_multi_agent_platform.agents import AgentRevisionRef
from ai_multi_agent_platform.context import (
    ContextBudget,
    ContextBudgetUsage,
    ContextBundle,
    InMemoryContextBundleRepository,
    new_context_bundle_id,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.handoffs import (
    CanonicalHandoffReferenceGateway,
    HandoffSourceKind,
    HandoffSourceRef,
)
from ai_multi_agent_platform.research import (
    EvidenceRelation,
    InMemoryResearchRepository,
    ResearchClass,
    ResearchService,
    ResearchSourceType,
)
from ai_multi_agent_platform.security import ActorIdentity, ActorType
from ai_multi_agent_platform.skills import InMemorySkillRepository, SkillBundle, new_skill_bundle_id
from ai_multi_agent_platform.testing import FakeAuthorizationProvider


@dataclass(frozen=True, slots=True)
class _Subject:
    revision: str
    digest: str


class _Verification:
    def __init__(self, subjects: dict[tuple[str, str], _Subject] | None = None) -> None:
        self.subjects = subjects or {}

    async def resolve_subject(self, *, task_id: str, subject_type: str, subject_id: str):
        del task_id
        try:
            return self.subjects[(subject_type, subject_id)]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, "verification subject not found") from exc

    async def resolve_context(self, *, task_id: str, subject_type: str, subject_id: str):
        del task_id, subject_type, subject_id
        raise AssertionError("Handoff gateway resolves exact subjects, not verification context")

    async def validate_evidence_artifacts(self, *, task_id: str, artifact_ids: tuple[str, ...]):
        del task_id
        return artifact_ids


def _participant() -> tuple[AgentRevisionRef, ActorIdentity]:
    agent = AgentRevisionRef(new_id("agent"), 1)
    return agent, ActorIdentity(f"agent:{agent.agent_id}@1", ActorType.AGENT)


def _gateway(
    *,
    authorization=None,
    verification=None,
    research=None,
    skills=None,
    contexts=None,
) -> CanonicalHandoffReferenceGateway:
    return CanonicalHandoffReferenceGateway(
        authorization=authorization or FakeAuthorizationProvider(),
        verification=verification or _Verification(),  # type: ignore[arg-type]
        research=research or InMemoryResearchRepository(),
        skills=skills or InMemorySkillRepository(),
        contexts=contexts or InMemoryContextBundleRepository(),
    )


def _operation(task_id: str) -> OperationContext:
    return OperationContext(correlation_id=f"issue-651-source:{task_id}")


@pytest.mark.asyncio
async def test_artifact_and_result_use_verification_owner_and_canonical_authorization() -> None:
    task_id = new_id("task")
    artifact_id = new_id("artifact")
    result_id = new_id("result")
    participant, actor = _participant()
    authorization = FakeAuthorizationProvider()
    verification = _Verification(
        {
            ("artifact", artifact_id): _Subject("file-r1", "a" * 64),
            ("result", result_id): _Subject("run-r1", "b" * 64),
        }
    )
    gateway = _gateway(authorization=authorization, verification=verification)
    artifact = HandoffSourceRef(
        HandoffSourceKind.ARTIFACT,
        artifact_id,
        revision="file-r1",
        digest="a" * 64,
    )
    result = HandoffSourceRef(
        HandoffSourceKind.RESULT,
        result_id,
        revision="run-r1",
        digest="b" * 64,
    )

    token = gateway.begin()
    try:
        await gateway.prepare_read(
            participant,
            artifact,
            task_id=task_id,
            run_id=None,
            actor=actor,
            operation=_operation(task_id),
        )
        await gateway.prepare_read(
            participant,
            result,
            task_id=task_id,
            run_id=None,
            actor=actor,
            operation=_operation(task_id),
        )
        assert gateway.exists(artifact) and gateway.can_read(participant, artifact)
        assert gateway.exists(result) and gateway.can_read(participant, result)
    finally:
        gateway.reset(token)

    assert [call.action for call in authorization.calls] == ["read", "result:read"]
    assert [call.resource_ref for call in authorization.calls] == [artifact_id, result_id]


@pytest.mark.asyncio
async def test_research_claim_and_evidence_resolve_from_real_research_repository() -> None:
    task_id = new_id("task")
    participant, actor = _participant()
    repository = InMemoryResearchRepository()
    research = ResearchService(repository)
    item = await research.create_item(
        title="Handoff evidence",
        question="Which exact evidence is being transferred?",
        research_class=ResearchClass.TASK_RESEARCH,
        owner_ref=OwnerRef(type="service", id="issue-651"),
        task_id=task_id,
    )
    source = await research.add_source(
        item.research_item_id,
        source_type=ResearchSourceType.DOCUMENT,
        locator="document:issue-651",
        title="Issue 651 source",
    )
    observation = await research.observe_source(
        source.source_id,
        retrieved_at=datetime(2026, 9, 8, 12, tzinfo=UTC),
        content_digest="sha256:issue-651-source",
        identity_proven=True,
        idempotency_key="issue-651-source-observation",
    )
    claim = await research.add_claim(
        item.research_item_id,
        text="The source supports the transferred work.",
        category="architecture",
    )
    evidence = await research.add_evidence(
        claim.claim_id,
        observation.observation_id,
        relation=EvidenceRelation.SUPPORTS,
        location_ref="section:handoff",
    )
    gateway = _gateway(research=repository)
    claim_ref = HandoffSourceRef(
        HandoffSourceKind.RESEARCH_CLAIM,
        claim.claim_id,
        revision=str(claim.revision),
        digest=claim.digest.removeprefix("sha256:"),
    )
    evidence_ref = HandoffSourceRef(
        HandoffSourceKind.RESEARCH_EVIDENCE,
        evidence.evidence_id,
        revision="1",
        digest=evidence.digest.removeprefix("sha256:"),
    )

    token = gateway.begin()
    try:
        for reference in (claim_ref, evidence_ref):
            await gateway.prepare_read(
                participant,
                reference,
                task_id=task_id,
                run_id=None,
                actor=actor,
                operation=_operation(task_id),
            )
            assert gateway.exists(reference)
            assert gateway.can_read(participant, reference)
    finally:
        gateway.reset(token)


@pytest.mark.asyncio
async def test_skill_and_context_bundles_resolve_from_their_canonical_repositories() -> None:
    task_id = new_id("task")
    run_id = new_id("run")
    participant, actor = _participant()
    skills = InMemorySkillRepository()
    contexts = InMemoryContextBundleRepository()

    skill_bundle = SkillBundle(
        skill_bundle_id=new_skill_bundle_id(),
        digest="c" * 64,
        entries=(),
        resolver_version="issue-651-skill-resolver",
        policy_version="issue-651-skill-policy",
        run_id=run_id,
        task_id=task_id,
        agent_id=participant.agent_id,
        agent_revision=participant.revision,
    )
    skills.save_bundle(skill_bundle)
    context_bundle = ContextBundle(
        context_bundle_id=new_context_bundle_id(),
        task_id=task_id,
        run_id=run_id,
        agent_id=participant.agent_id,
        agent_revision=participant.revision,
        entries=(),
        omissions=(),
        budget=ContextBudget(max_items=0),
        usage=ContextBudgetUsage(),
        resolver_version="issue-651-context-resolver",
        policy_version="issue-651-context-policy",
        actor_ref=actor.actor_id,
    )
    contexts.put(context_bundle)
    gateway = _gateway(skills=skills, contexts=contexts)
    skill_ref = HandoffSourceRef(
        HandoffSourceKind.SKILL_BUNDLE,
        skill_bundle.skill_bundle_id,
        revision="1",
        digest=skill_bundle.digest,
    )
    context_ref = HandoffSourceRef(
        HandoffSourceKind.CONTEXT_BUNDLE,
        context_bundle.context_bundle_id,
        revision="1",
        digest=context_bundle.digest,
    )

    token = gateway.begin()
    try:
        for reference in (skill_ref, context_ref):
            await gateway.prepare_read(
                participant,
                reference,
                task_id=task_id,
                run_id=run_id,
                actor=actor,
                operation=_operation(task_id),
            )
            assert gateway.can_read(participant, reference)
    finally:
        gateway.reset(token)


@pytest.mark.asyncio
async def test_stale_digest_fails_before_reference_is_marked_readable() -> None:
    task_id = new_id("task")
    artifact_id = new_id("artifact")
    participant, actor = _participant()
    gateway = _gateway(
        verification=_Verification({("artifact", artifact_id): _Subject("file-r1", "d" * 64)})
    )
    stale = HandoffSourceRef(
        HandoffSourceKind.ARTIFACT,
        artifact_id,
        revision="file-r1",
        digest="e" * 64,
    )

    token = gateway.begin()
    try:
        with pytest.raises(ContractError) as error:
            await gateway.prepare_read(
                participant,
                stale,
                task_id=task_id,
                run_id=None,
                actor=actor,
                operation=_operation(task_id),
            )
        assert error.value.code is ErrorCode.NOT_FOUND
        assert not gateway.exists(stale)
        assert not gateway.can_read(participant, stale)
    finally:
        gateway.reset(token)


@pytest.mark.asyncio
async def test_authorization_denial_never_turns_discovery_into_read_permission() -> None:
    task_id = new_id("task")
    artifact_id = new_id("artifact")
    participant, actor = _participant()
    denied = FakeAuthorizationProvider(allowed=False)
    reference = HandoffSourceRef(
        HandoffSourceKind.ARTIFACT,
        artifact_id,
        revision="file-r1",
        digest="f" * 64,
    )
    gateway = _gateway(
        authorization=denied,
        verification=_Verification({("artifact", artifact_id): _Subject("file-r1", "f" * 64)}),
    )

    assert not gateway.exists(reference)
    assert not gateway.can_read(participant, reference)
    token = gateway.begin()
    try:
        with pytest.raises(ContractError) as error:
            await gateway.prepare_read(
                participant,
                reference,
                task_id=task_id,
                run_id=None,
                actor=actor,
                operation=_operation(task_id),
            )
        assert error.value.code is ErrorCode.FORBIDDEN
        assert not gateway.exists(reference)
        assert not gateway.can_read(participant, reference)
    finally:
        gateway.reset(token)
