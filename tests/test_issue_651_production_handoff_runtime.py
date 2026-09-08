"""Executable acceptance coverage for #651 production Agent Handoff runtime wiring.

These tests are committed for the later consolidated integration branch. The #651 work branch
intentionally does not execute them or trigger CI.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from ai_multi_agent_platform.agents import (
    AgentExecutionSpec,
    AgentInstructions,
    AgentProfile,
    AgentRevisionRef,
    AgentRuntime,
    AgentService,
    AgentTeamMember,
    AgentTeamProfile,
    AgentTeamRevisionRef,
    InMemoryAgentRepository,
    InstructionSource,
    JsonAgentRepository,
    OrchestratorMapping,
)
from ai_multi_agent_platform.context import (
    ContextAssemblyRequest,
    ContextAssemblyService,
    ContextBoundAgentRuntime,
    ContextBudget,
    ContextResolver,
    ContextSourceRequest,
    ContextSourceType,
    InMemoryContextBundleRepository,
    InMemoryContextRunBindingRepository,
    JsonContextBundleRepository,
    JsonContextRunBindingRepository,
    RenderedContext,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.coordination.models import CoordinationPhase, StepCoordinationRecord
from ai_multi_agent_platform.coordination.repository import InMemoryCoordinatorRepository
from ai_multi_agent_platform.coordination.sqlite_repository import SQLiteCoordinatorRepository
from ai_multi_agent_platform.domain import OwnerRef, Plan, Step, new_id
from ai_multi_agent_platform.handoffs import (
    CanonicalConsumerRequirementEvaluator,
    CanonicalHandoffReferenceGateway,
    DurableConsumedHandoffContextAdapter,
    HandoffContent,
    InMemoryHandoffAuditSink,
    InMemoryHandoffRepository,
    SQLiteHandoffRepository,
    build_production_handoff_runtime,
)
from ai_multi_agent_platform.research import InMemoryResearchRepository
from ai_multi_agent_platform.security import ActorIdentity, ActorType
from ai_multi_agent_platform.skills import InMemorySkillRepository
from ai_multi_agent_platform.testing import FakeAuthorizationProvider


class _Verification:
    async def resolve_subject(self, *, task_id: str, subject_type: str, subject_id: str):
        del task_id, subject_id
        return type(
            "Subject",
            (),
            {"revision": f"{subject_type}:r1", "digest": "a" * 64},
        )()

    async def resolve_context(self, *, task_id: str, subject_type: str, subject_id: str):
        del task_id, subject_type, subject_id
        raise AssertionError("resolve_context is not used by Handoff source validation")

    async def validate_evidence_artifacts(self, *, task_id: str, artifact_ids: tuple[str, ...]):
        del task_id
        return artifact_ids


class _AdapterA:
    adapter_id = "issue651-adapter-a"

    async def map_agent_with_context(
        self,
        spec: AgentExecutionSpec,
        bundle,
        rendered: RenderedContext,
    ) -> OrchestratorMapping:
        assert rendered.parts
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
        assert rendered.parts
        return OrchestratorMapping(
            adapter_id=self.adapter_id,
            runtime_ref=f"b:{spec.run_id}",
            metadata={
                "context_bundle_id": bundle.context_bundle_id,
                "context_bundle_digest": bundle.digest,
            },
        )


@dataclass(frozen=True, slots=True)
class _Ids:
    task_id: str
    plan_id: str
    producer_step_id: str
    consumer_step_id: str
    producer_run_id: str
    consumer_run_id: str
    producer: AgentRevisionRef
    consumer: AgentRevisionRef


@dataclass(slots=True)
class _RuntimeParts:
    runtime: object
    contexts: InMemoryContextBundleRepository
    bindings: InMemoryContextRunBindingRepository
    audit: InMemoryHandoffAuditSink
    authorization: FakeAuthorizationProvider


def _profile(name: str, role: str) -> AgentProfile:
    return AgentProfile(
        name=name,
        role=role,
        instructions=AgentInstructions(
            role=InstructionSource(content="Use only canonical issue #651 runtime context."),
        ),
    )


async def _bootstrap_domain(
    agent_repository,
    coordinator,
    *,
    create_producer_run: bool = True,
) -> tuple[AgentService, AgentRuntime, _Ids]:
    owner = OwnerRef(type="service", id="issue-651")
    agents = AgentService(agent_repository)
    producer_revision = agents.create_agent(_profile("Producer", "producer"), owner_ref=owner)
    consumer_revision = agents.create_agent(_profile("Consumer", "reviewer"), owner_ref=owner)
    producer = AgentRevisionRef(producer_revision.agent_id, producer_revision.revision)
    consumer = AgentRevisionRef(consumer_revision.agent_id, consumer_revision.revision)

    task_id = new_id("task")
    plan_id = new_id("plan")
    producer_step_id = new_id("step")
    consumer_step_id = new_id("step")
    producer_run_id = new_id("run")
    consumer_run_id = new_id("run")

    plan = Plan(id=plan_id, task_id=task_id, owner_ref=owner, revision=1, active=True)
    producer_step = Step(
        id=producer_step_id,
        plan_id=plan_id,
        title="Produce canonical work",
        owner_ref=owner,
    )
    consumer_step = Step(
        id=consumer_step_id,
        plan_id=plan_id,
        title="Consume canonical work",
        owner_ref=owner,
        depends_on=(producer_step_id,),
    )
    coordinator.create_plan(
        plan,
        (producer_step, consumer_step),
        (
            StepCoordinationRecord(
                task_id=task_id,
                plan_id=plan_id,
                plan_revision=1,
                step_id=producer_step_id,
                phase=CoordinationPhase.TERMINAL,
                latest_run_id=producer_run_id,
                current_attempt=1,
            ),
            StepCoordinationRecord(
                task_id=task_id,
                plan_id=plan_id,
                plan_revision=1,
                step_id=consumer_step_id,
                phase=CoordinationPhase.ATTEMPT_ACTIVE,
                dependency_ids=(producer_step_id,),
                satisfied_dependency_ids=(producer_step_id,),
                latest_run_id=consumer_run_id,
                current_attempt=1,
            ),
        ),
    )

    runtime = AgentRuntime(agents)
    if create_producer_run:
        await runtime.start_agent(
            task_id=task_id,
            run_id=producer_run_id,
            agent_id=producer.agent_id,
            revision=producer.revision,
        )
    return (
        agents,
        runtime,
        _Ids(
            task_id=task_id,
            plan_id=plan_id,
            producer_step_id=producer_step_id,
            consumer_step_id=consumer_step_id,
            producer_run_id=producer_run_id,
            consumer_run_id=consumer_run_id,
            producer=producer,
            consumer=consumer,
        ),
    )


def _compose_runtime(
    *,
    agent_repository,
    agent_runtime: AgentRuntime,
    coordinator,
    handoffs=None,
    contexts=None,
    bindings=None,
) -> _RuntimeParts:
    authorization = FakeAuthorizationProvider()
    context_repository = contexts or InMemoryContextBundleRepository()
    binding_repository = bindings or InMemoryContextRunBindingRepository()
    context_assembly = ContextAssemblyService(
        ContextResolver(authorization),
        context_repository,
    )
    context_runtime = ContextBoundAgentRuntime(
        agent_runtime,
        bundle_repository=context_repository,
        binding_repository=binding_repository,
    )
    references = CanonicalHandoffReferenceGateway(
        authorization=authorization,
        verification=_Verification(),  # type: ignore[arg-type]
        research=InMemoryResearchRepository(),
        skills=InMemorySkillRepository(),
        contexts=context_repository,
    )
    audit = InMemoryHandoffAuditSink()
    runtime = build_production_handoff_runtime(
        repository=handoffs or InMemoryHandoffRepository(),
        agents=agent_repository,
        references=references,
        coordinator=coordinator,
        context_assembly=context_assembly,
        context_runtime=context_runtime,
        audit=audit,
        consumer_requirements=CanonicalConsumerRequirementEvaluator(agent_repository),
    )
    return _RuntimeParts(runtime, context_repository, binding_repository, audit, authorization)


def _content(ids: _Ids, *, consumer=None) -> HandoffContent:
    return HandoffContent(
        task_id=ids.task_id,
        plan_id=ids.plan_id,
        producer_step_id=ids.producer_step_id,
        consumer_step_id=ids.consumer_step_id,
        producer_run_id=ids.producer_run_id,
        producer=ids.producer,
        intended_consumer=consumer or ids.consumer,
        objective="Transfer canonical producer work to the dependent consumer Step.",
        completed_work_summary="The producer completed its bounded canonical work.",
        recommended_next_action="Continue from the durable Agent Handoff.",
        requested_output="A canonical consumer result.",
    )


def _actor(agent: AgentRevisionRef) -> ActorIdentity:
    return ActorIdentity(
        f"agent:{agent.agent_id}@{agent.revision}",
        ActorType.AGENT,
    )


def _operation(ids: _Ids) -> OperationContext:
    return OperationContext(correlation_id=f"issue-651:{ids.task_id}")


@pytest.mark.asyncio
async def test_production_agent_a_to_b_handoff_reaches_context_bound_agent_run() -> None:
    agent_repository = InMemoryAgentRepository()
    coordinator = InMemoryCoordinatorRepository()
    _, agent_runtime, ids = await _bootstrap_domain(agent_repository, coordinator)
    parts = _compose_runtime(
        agent_repository=agent_repository,
        agent_runtime=agent_runtime,
        coordinator=coordinator,
    )

    handoff = await parts.runtime.create_handoff(  # type: ignore[attr-defined]
        _content(ids),
        idempotency_key="issue-651-agent-a-to-b",
        producer_actor=_actor(ids.producer),
        intended_consumer_actor=_actor(ids.consumer),
        operation=_operation(ids),
    )
    execution = await parts.runtime.start_consumer(  # type: ignore[attr-defined]
        handoff.handoff_id,
        handoff.revision,
        consuming_run_id=ids.consumer_run_id,
        consumer=ids.consumer,
        consumer_actor=_actor(ids.consumer),
        operation=_operation(ids),
        budget=ContextBudget(max_tokens=4096, max_bytes=16384, max_items=16),
    )

    assert execution.runtime_context.consumption.handoff_digest == handoff.content_digest
    assert execution.agent_run.agent == ids.consumer
    assert execution.agent_run.run_id == ids.consumer_run_id
    assert execution.context_binding.agent_run_id == execution.agent_run.agent_run_id
    assert execution.context_binding.context_bundle_id == execution.context_bundle.context_bundle_id
    assert execution.context_binding.context_bundle_digest == execution.context_bundle.digest
    assert execution.agent_run.verification_context["handoff_id"] == handoff.handoff_id
    assert execution.agent_run.verification_context["handoff_digest"] == handoff.content_digest

    entries = [
        entry
        for entry in execution.context_bundle.entries
        if entry.source.source_type is ContextSourceType.AGENT_HANDOFF
    ]
    assert len(entries) == 1
    assert entries[0].source.source_id == handoff.handoff_id
    assert entries[0].source.revision == str(handoff.revision)
    assert entries[0].source.digest == handoff.content_digest
    assert handoff.content.provenance is not None
    assert handoff.content.provenance.source == "agent-handoff-production-runtime/v1"
    assert [event.event_type for event in parts.audit.events] == [
        "handoff.created",
        "handoff.consumed",
    ]


@pytest.mark.asyncio
async def test_team_consumer_agent_run_retains_exact_team_revision() -> None:
    agent_repository = InMemoryAgentRepository()
    coordinator = InMemoryCoordinatorRepository()
    agents, agent_runtime, ids = await _bootstrap_domain(agent_repository, coordinator)
    team_revision = agents.create_team(
        AgentTeamProfile(
            name="Consumer team",
            members=(AgentTeamMember(agent=ids.consumer, role="reviewer"),),
        ),
        owner_ref=OwnerRef(type="service", id="issue-651"),
    )
    team = AgentTeamRevisionRef(team_revision.team_id, team_revision.revision)
    parts = _compose_runtime(
        agent_repository=agent_repository,
        agent_runtime=agent_runtime,
        coordinator=coordinator,
    )

    handoff = await parts.runtime.create_handoff(  # type: ignore[attr-defined]
        _content(ids, consumer=team),
        idempotency_key="issue-651-team-consumer",
        producer_actor=_actor(ids.producer),
        operation=_operation(ids),
    )
    execution = await parts.runtime.start_consumer(  # type: ignore[attr-defined]
        handoff.handoff_id,
        handoff.revision,
        consuming_run_id=ids.consumer_run_id,
        consumer=team,
        consumer_agent=ids.consumer,
        consumer_actor=_actor(ids.consumer),
        operation=_operation(ids),
        budget=ContextBudget(max_tokens=4096, max_bytes=16384, max_items=16),
    )

    assert execution.agent_run.agent == ids.consumer
    assert execution.agent_run.team == team
    assert execution.runtime_context.consumption.consumer == team


@pytest.mark.asyncio
async def test_producer_agent_run_identity_mismatch_is_rejected_before_creation() -> None:
    agent_repository = InMemoryAgentRepository()
    coordinator = InMemoryCoordinatorRepository()
    _, agent_runtime, ids = await _bootstrap_domain(
        agent_repository,
        coordinator,
        create_producer_run=False,
    )
    await agent_runtime.start_agent(
        task_id=ids.task_id,
        run_id=ids.producer_run_id,
        agent_id=ids.consumer.agent_id,
        revision=ids.consumer.revision,
    )
    parts = _compose_runtime(
        agent_repository=agent_repository,
        agent_runtime=agent_runtime,
        coordinator=coordinator,
    )

    with pytest.raises(ContractError) as mismatch:
        await parts.runtime.create_handoff(  # type: ignore[attr-defined]
            _content(ids),
            idempotency_key="issue-651-producer-mismatch",
            producer_actor=_actor(ids.producer),
            operation=_operation(ids),
        )
    assert mismatch.value.code is ErrorCode.CONFLICT


@pytest.mark.asyncio
async def test_existing_wrong_consumer_agent_run_is_rejected_before_consumption() -> None:
    agent_repository = InMemoryAgentRepository()
    coordinator = InMemoryCoordinatorRepository()
    _, agent_runtime, ids = await _bootstrap_domain(agent_repository, coordinator)
    parts = _compose_runtime(
        agent_repository=agent_repository,
        agent_runtime=agent_runtime,
        coordinator=coordinator,
    )
    handoff = await parts.runtime.create_handoff(  # type: ignore[attr-defined]
        _content(ids),
        idempotency_key="issue-651-consumer-mismatch",
        producer_actor=_actor(ids.producer),
        operation=_operation(ids),
    )
    await agent_runtime.start_agent(
        task_id=ids.task_id,
        run_id=ids.consumer_run_id,
        agent_id=ids.producer.agent_id,
        revision=ids.producer.revision,
    )

    with pytest.raises(ContractError) as mismatch:
        await parts.runtime.consume_handoff(  # type: ignore[attr-defined]
            handoff.handoff_id,
            handoff.revision,
            consuming_run_id=ids.consumer_run_id,
            consumer=ids.consumer,
            consumer_actor=_actor(ids.consumer),
            operation=_operation(ids),
        )
    assert mismatch.value.code is ErrorCode.CONFLICT


@pytest.mark.asyncio
async def test_restart_after_consumption_recovers_same_handoff_and_executes_consumer(
    tmp_path: Path,
) -> None:
    agents_path = tmp_path / "agents.json"
    coordination_path = tmp_path / "coordination.sqlite3"
    handoff_path = tmp_path / "handoffs.sqlite3"
    context_path = tmp_path / "context-bundles.json"
    binding_path = tmp_path / "context-bindings.json"

    agent_repository = JsonAgentRepository(agents_path)
    coordinator = SQLiteCoordinatorRepository(coordination_path)
    _, agent_runtime, ids = await _bootstrap_domain(agent_repository, coordinator)
    first = _compose_runtime(
        agent_repository=agent_repository,
        agent_runtime=agent_runtime,
        coordinator=coordinator,
        handoffs=SQLiteHandoffRepository(handoff_path),
        contexts=JsonContextBundleRepository(context_path),
        bindings=JsonContextRunBindingRepository(binding_path),
    )
    handoff = await first.runtime.create_handoff(  # type: ignore[attr-defined]
        _content(ids),
        idempotency_key="issue-651-restart",
        producer_actor=_actor(ids.producer),
        operation=_operation(ids),
    )
    consumed = await first.runtime.consume_handoff(  # type: ignore[attr-defined]
        handoff.handoff_id,
        handoff.revision,
        consuming_run_id=ids.consumer_run_id,
        consumer=ids.consumer,
        consumer_actor=_actor(ids.consumer),
        operation=_operation(ids),
    )

    restored_agent_repository = JsonAgentRepository(agents_path)
    restored_agent_runtime = AgentRuntime(AgentService(restored_agent_repository))
    restored_coordinator = SQLiteCoordinatorRepository(coordination_path)
    restored_handoffs = SQLiteHandoffRepository(handoff_path)
    restored_contexts = JsonContextBundleRepository(context_path)
    restored_bindings = JsonContextRunBindingRepository(binding_path)
    restored = _compose_runtime(
        agent_repository=restored_agent_repository,
        agent_runtime=restored_agent_runtime,
        coordinator=restored_coordinator,
        handoffs=restored_handoffs,
        contexts=restored_contexts,
        bindings=restored_bindings,
    )

    adapter = DurableConsumedHandoffContextAdapter(restored_handoffs, restored_agent_repository)
    candidates = await adapter.collect(
        ContextSourceRequest(
            task_id=ids.task_id,
            run_id=ids.consumer_run_id,
            agent_id=ids.consumer.agent_id,
            agent_revision=ids.consumer.revision,
            project_id=None,
            workspace_id=None,
            plan_id=ids.plan_id,
            step_id=ids.consumer_step_id,
        )
    )
    assert len(candidates) == 1
    assert candidates[0].source.source_id == handoff.handoff_id
    assert candidates[0].source.revision == str(handoff.revision)
    assert candidates[0].source.digest == handoff.content_digest

    execution = await restored.runtime.start_consumer(  # type: ignore[attr-defined]
        handoff.handoff_id,
        handoff.revision,
        consuming_run_id=ids.consumer_run_id,
        consumer=ids.consumer,
        consumer_actor=_actor(ids.consumer),
        operation=_operation(ids),
        budget=ContextBudget(max_tokens=4096, max_bytes=16384, max_items=16),
    )
    assert execution.runtime_context == consumed
    assert execution.context_binding.context_bundle_digest == execution.context_bundle.digest

    reopened_contexts = JsonContextBundleRepository(context_path)
    reopened_bindings = JsonContextRunBindingRepository(binding_path)
    assert reopened_contexts.get(execution.context_bundle.context_bundle_id).digest == (
        execution.context_bundle.digest
    )
    assert reopened_bindings.get(execution.agent_run.agent_run_id) == execution.context_binding


@pytest.mark.asyncio
async def test_two_real_context_orchestrators_receive_same_canonical_handoff_bundle() -> None:
    agent_repository = InMemoryAgentRepository()
    coordinator = InMemoryCoordinatorRepository()
    _, agent_runtime, ids = await _bootstrap_domain(agent_repository, coordinator)
    parts = _compose_runtime(
        agent_repository=agent_repository,
        agent_runtime=agent_runtime,
        coordinator=coordinator,
    )
    handoff = await parts.runtime.create_handoff(  # type: ignore[attr-defined]
        _content(ids),
        idempotency_key="issue-651-orchestrator-replacement",
        producer_actor=_actor(ids.producer),
        operation=_operation(ids),
    )
    await parts.runtime.consume_handoff(  # type: ignore[attr-defined]
        handoff.handoff_id,
        handoff.revision,
        consuming_run_id=ids.consumer_run_id,
        consumer=ids.consumer,
        consumer_actor=_actor(ids.consumer),
        operation=_operation(ids),
    )

    bundle = await ContextAssemblyService(
        ContextResolver(parts.authorization),
        parts.contexts,
    ).assemble(
        ContextAssemblyRequest(
            task_id=ids.task_id,
            run_id=ids.consumer_run_id,
            agent_id=ids.consumer.agent_id,
            agent_revision=ids.consumer.revision,
            actor=_actor(ids.consumer),
            operation=_operation(ids),
            candidates=(),
            budget=ContextBudget(max_tokens=4096, max_bytes=16384, max_items=16),
            plan_id=ids.plan_id,
            step_id=ids.consumer_step_id,
        ),
        adapters=(
            DurableConsumedHandoffContextAdapter(parts.runtime.repository, agent_repository),
        ),  # type: ignore[attr-defined]
    )

    runtime_a = ContextBoundAgentRuntime(
        agent_runtime,
        bundle_repository=parts.contexts,
        binding_repository=InMemoryContextRunBindingRepository(),
    )
    runtime_b = ContextBoundAgentRuntime(
        agent_runtime,
        bundle_repository=parts.contexts,
        binding_repository=InMemoryContextRunBindingRepository(),
    )
    record_a, binding_a = await runtime_a.start_agent(bundle=bundle, adapter=_AdapterA())
    record_b, binding_b = await runtime_b.start_agent(bundle=bundle, adapter=_AdapterB())

    assert record_a.orchestrator_adapter_id == _AdapterA.adapter_id
    assert record_b.orchestrator_adapter_id == _AdapterB.adapter_id
    assert binding_a.context_bundle_id == binding_b.context_bundle_id == bundle.context_bundle_id
    assert binding_a.context_bundle_digest == binding_b.context_bundle_digest == bundle.digest
    handoff_entries = [
        entry
        for entry in bundle.entries
        if entry.source.source_type is ContextSourceType.AGENT_HANDOFF
    ]
    assert len(handoff_entries) == 1
    assert handoff_entries[0].source.source_id == handoff.handoff_id
    assert handoff_entries[0].source.revision == str(handoff.revision)
    assert handoff_entries[0].source.digest == handoff.content_digest


def test_consumer_requirement_evaluator_uses_canonical_agent_and_team_facts() -> None:
    repository = InMemoryAgentRepository()
    service = AgentService(repository)
    owner = OwnerRef(type="service", id="issue-651")
    reviewer_revision = service.create_agent(_profile("Reviewer", "reviewer"), owner_ref=owner)
    reviewer = AgentRevisionRef(reviewer_revision.agent_id, reviewer_revision.revision)
    team_revision = service.create_team(
        AgentTeamProfile(
            name="Review team",
            members=(AgentTeamMember(agent=reviewer, role="reviewer"),),
        ),
        owner_ref=owner,
    )
    team = AgentTeamRevisionRef(team_revision.team_id, team_revision.revision)
    evaluator = CanonicalConsumerRequirementEvaluator(repository)

    assert evaluator.accepts(("role:reviewer",), reviewer)
    assert evaluator.accepts(("role:reviewer", f"team:{team.team_id}"), team)
    assert not evaluator.accepts(("role:producer",), reviewer)
