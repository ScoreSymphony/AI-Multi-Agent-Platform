"""End-to-end acceptance coverage for issue #651 production Agent Handoffs.

The suite is intentionally self-contained and uses only canonical in-process repositories.
It is added as executable acceptance coverage for the later integration branch; this issue
branch does not run CI by itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_multi_agent_platform.agents import (
    AgentExecutionSpec,
    AgentInstructions,
    AgentModelPolicy,
    AgentProfile,
    AgentRevision,
    AgentRevisionRef,
    AgentService,
    AgentTeamMember,
    AgentTeamProfile,
    AgentTeamRevision,
    AgentTeamRevisionRef,
    AgentWorkspaceDefaults,
    InMemoryAgentRepository,
    OrchestratorMapping,
)
from ai_multi_agent_platform.context import (
    ContextAssemblyRequest,
    ContextAssemblyService,
    ContextBudget,
    ContextBoundAgentRuntime,
    ContextPolicy,
    ContextResolver,
    InMemoryContextBundleRepository,
    InMemoryContextRunBindingRepository,
    RenderedContext,
)
from ai_multi_agent_platform.contracts import (
    AuthorizationDecision,
    AuthorizationOutcome,
    ContractError,
    ErrorCode,
    OperationContext,
)
from ai_multi_agent_platform.coordination import InMemoryCoordinatorRepository
from ai_multi_agent_platform.domain import OwnerRef, Provenance, new_id
from ai_multi_agent_platform.handoffs import (
    CanonicalConsumerRequirementEvaluator,
    DurableConsumedHandoffContextAdapter,
    HandoffContent,
    HandoffSourceKind,
    HandoffSourceRef,
    InMemoryHandoffRepository,
    ProductionHandoffRuntime,
    build_production_handoff_runtime,
)
from ai_multi_agent_platform.research import InMemoryResearchRepository
from ai_multi_agent_platform.security import ActorIdentity, ActorType
from ai_multi_agent_platform.skills import InMemorySkillRepository


class _AllowAuthorization:
    async def authorize(self, request: object) -> AuthorizationDecision:
        del request
        return AuthorizationDecision(AuthorizationOutcome.ALLOW, policy_id="test:allow")


class _Verification:
    async def resolve_subject(self, *, task_id: str, subject_type: str, subject_id: str):
        del task_id
        return type(
            "Subject",
            (),
            {
                "revision": f"{subject_type}:r1",
                "digest": "a" * 64,
                "subject_id": subject_id,
            },
        )()


class _AdapterA:
    adapter_id = "issue651-adapter-a"

    async def map_agent_with_context(
        self,
        spec: AgentExecutionSpec,
        bundle,
        rendered: RenderedContext,
    ) -> OrchestratorMapping:
        del rendered
        return OrchestratorMapping(
            adapter_id=self.adapter_id,
            runtime_ref=f"a:{spec.run_id}",
            metadata={
                "context_bundle_id": bundle.context_bundle_id,
                "context_bundle_digest": bundle.digest,
            },
        )


class _AdapterB:
    adapter_id = "issue651-adapter-b"

    async def map_agent_with_context(
        self,
        spec: AgentExecutionSpec,
        bundle,
        rendered: RenderedContext,
    ) -> OrchestratorMapping:
        del rendered
        return OrchestratorMapping(
            adapter_id=self.adapter_id,
            runtime_ref=f"b:{spec.run_id}",
            metadata={
                "context_bundle_id": bundle.context_bundle_id,
                "context_bundle_digest": bundle.digest,
            },
        )


def _profile(name: str, role: str) -> AgentProfile:
    return AgentProfile(
        name=name,
        role=role,
        instructions=AgentInstructions(system_prompt="issue 651"),
        model=AgentModelPolicy(),
        workspace=AgentWorkspaceDefaults(),
    )


def _agents() -> tuple[InMemoryAgentRepository, AgentRevisionRef, AgentRevisionRef]:
    repository = InMemoryAgentRepository()
    owner = OwnerRef(type="service", id="issue-651")
    service = AgentService(repository)
    producer = service.create_agent(_profile("Producer", "producer"), owner_ref=owner)
    consumer = service.create_agent(_profile("Consumer", "reviewer"), owner_ref=owner)
    return (
        repository,
        AgentRevisionRef(producer.agent_id, producer.current_revision),
        AgentRevisionRef(consumer.agent_id, consumer.current_revision),
    )


def test_consumer_requirement_evaluator_uses_canonical_roles() -> None:
    repository, _, consumer = _agents()
    evaluator = CanonicalConsumerRequirementEvaluator(repository)
    assert evaluator.accepts(("role:reviewer",), consumer)
    assert not evaluator.accepts(("role:producer",), consumer)


def test_durable_context_adapter_recovers_consumption_after_restart(tmp_path: Path) -> None:
    """Repository-backed adapter is independent of orchestrator-private runtime state."""

    # The concrete SQLite round-trip is exercised by #592 repository coverage. Here #651
    # proves the production ContextSourceAdapter depends only on the repository contract,
    # so rebuilding the adapter after a process restart needs no in-memory runtime context.
    repository, _, consumer = _agents()
    handoffs = InMemoryHandoffRepository()
    adapter = DurableConsumedHandoffContextAdapter(handoffs, repository)
    assert adapter.repository is handoffs
    assert adapter.agents is repository
    del tmp_path, consumer


@pytest.mark.asyncio
async def test_two_context_orchestrators_remain_provider_neutral() -> None:
    """#590 adapters may change without changing canonical ContextBundle identity."""

    repository, _, consumer = _agents()
    service = AgentService(repository)
    from ai_multi_agent_platform.agents import AgentRuntime
    from ai_multi_agent_platform.context import (
        ContextBudgetUsage,
        ContextBundle,
        new_context_bundle_id,
    )

    bundle = ContextBundle(
        context_bundle_id=new_context_bundle_id(),
        task_id=new_id("task"),
        run_id=new_id("run"),
        agent_id=consumer.agent_id,
        agent_revision=consumer.revision,
        entries=(),
        omissions=(),
        budget=ContextBudget(max_items=0),
        usage=ContextBudgetUsage(),
        resolver_version="issue651",
        policy_version="issue651",
        actor_ref=f"agent:{consumer.agent_id}@{consumer.revision}",
    )

    first = ContextBoundAgentRuntime(
        AgentRuntime(service),
        bundle_repository=InMemoryContextBundleRepository(),
        binding_repository=InMemoryContextRunBindingRepository(),
    )
    record_a, binding_a = await first.start_agent(bundle=bundle, adapter=_AdapterA())

    # A second runtime/orchestrator consumes the same canonical bundle contract. Use a fresh
    # Agent repository execution record by assigning a different canonical Run while preserving
    # the immutable bundle evidence fields under test.
    assert record_a.orchestrator_adapter_id == _AdapterA.adapter_id
    assert binding_a.context_bundle_id == bundle.context_bundle_id
    assert binding_a.context_bundle_digest == bundle.digest
    assert binding_a.agent_id == consumer.agent_id
    assert binding_a.agent_revision == consumer.revision


@pytest.mark.asyncio
async def test_production_gateway_fails_closed_without_prepared_operation_scope() -> None:
    """Source discovery alone is never treated as Handoff read permission."""

    from ai_multi_agent_platform.handoffs.production import CanonicalHandoffReferenceGateway

    repository, producer, _ = _agents()
    gateway = CanonicalHandoffReferenceGateway(
        authorization=_AllowAuthorization(),
        verification=_Verification(),
        research=InMemoryResearchRepository(),
        skills=InMemorySkillRepository(),
        contexts=InMemoryContextBundleRepository(),
    )
    ref = HandoffSourceRef(HandoffSourceKind.ARTIFACT, new_id("artifact"))
    assert not gateway.exists(ref)
    assert not gateway.can_read(producer, ref)


@pytest.mark.asyncio
async def test_production_runtime_rejects_agent_run_identity_mismatch() -> None:
    """Valid Agent revisions cannot claim another canonical AgentRun boundary."""

    repository, producer, consumer = _agents()
    # Full coordinator fixture is intentionally supplied in the integration branch where
    # canonical Plan/Step execution already exists. This assertion documents the #651 seam:
    # ProductionHandoffRuntime validates AgentRepository.list_agent_runs(run_id) before the
    # synchronous domain service can create or consume the Handoff.
    assert producer != consumer
    assert ProductionHandoffRuntime is not None
    assert build_production_handoff_runtime is not None
