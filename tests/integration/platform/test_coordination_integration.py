from __future__ import annotations

import pytest

from ai_multi_agent_platform.agents.models import AgentRevisionRef
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.coordination.models import CoordinationPhase, StepCoordinationRecord
from ai_multi_agent_platform.coordination.repository import InMemoryCoordinatorRepository
from ai_multi_agent_platform.domain import OwnerRef, Plan, Step, new_id
from ai_multi_agent_platform.handoffs import (
    CoordinatedHandoffService,
    HandoffContent,
    HandoffService,
    InMemoryHandoffRepository,
)


class _AgentDirectory:
    def __init__(self, *agents: AgentRevisionRef) -> None:
        self._agents = {(agent.agent_id, agent.revision) for agent in agents}

    def get_agent_revision(self, agent_id: str, revision: int) -> object:
        if (agent_id, revision) not in self._agents:
            raise ContractError(ErrorCode.NOT_FOUND, "agent revision not found")
        return object()

    def get_team_revision(self, team_id: str, revision: int) -> object:
        del team_id, revision
        raise ContractError(ErrorCode.NOT_FOUND, "team revision not found")


class _NoSources:
    def exists(self, reference: object) -> bool:
        del reference
        return False

    def can_read(self, participant: object, reference: object) -> bool:
        del participant, reference
        return False


def _fixture() -> tuple[
    CoordinatedHandoffService,
    HandoffService,
    HandoffContent,
    AgentRevisionRef,
    str,
]:
    task_id = new_id("task")
    plan_id = new_id("plan")
    producer_step_id = new_id("step")
    consumer_step_id = new_id("step")
    producer_run_id = new_id("run")
    consumer_run_id = new_id("run")
    owner = OwnerRef(type="service", id="handoff-coordination-test")
    producer = AgentRevisionRef(new_id("agent"), 1)
    consumer = AgentRevisionRef(new_id("agent"), 1)

    plan = Plan(id=plan_id, task_id=task_id, owner_ref=owner, revision=1, active=True)
    producer_step = Step(
        id=producer_step_id,
        plan_id=plan_id,
        title="Produce bounded result",
        owner_ref=owner,
    )
    consumer_step = Step(
        id=consumer_step_id,
        plan_id=plan_id,
        title="Consume bounded result",
        owner_ref=owner,
        depends_on=(producer_step_id,),
    )
    producer_record = StepCoordinationRecord(
        task_id=task_id,
        plan_id=plan_id,
        plan_revision=1,
        step_id=producer_step_id,
        phase=CoordinationPhase.TERMINAL,
        latest_run_id=producer_run_id,
        current_attempt=1,
    )
    consumer_record = StepCoordinationRecord(
        task_id=task_id,
        plan_id=plan_id,
        plan_revision=1,
        step_id=consumer_step_id,
        phase=CoordinationPhase.ATTEMPT_ACTIVE,
        dependency_ids=(producer_step_id,),
        satisfied_dependency_ids=(producer_step_id,),
        latest_run_id=consumer_run_id,
        current_attempt=1,
    )
    coordinator = InMemoryCoordinatorRepository()
    coordinator.create_plan(
        plan,
        (producer_step, consumer_step),
        (producer_record, consumer_record),
    )

    base = HandoffService(
        InMemoryHandoffRepository(),
        agents=_AgentDirectory(producer, consumer),  # type: ignore[arg-type]
        references=_NoSources(),  # type: ignore[arg-type]
    )
    service = CoordinatedHandoffService(base, coordinator)
    content = HandoffContent(
        task_id=task_id,
        plan_id=plan_id,
        producer_step_id=producer_step_id,
        consumer_step_id=consumer_step_id,
        producer_run_id=producer_run_id,
        producer=producer,
        intended_consumer=consumer,
        objective="Transfer canonical producer work to the dependent consumer Step.",
        completed_work_summary="The producer Step completed its bounded work.",
        recommended_next_action="Consume the handoff in the assigned consumer Run.",
        requested_output="A canonical consumer result.",
    )
    return service, base, content, consumer, consumer_run_id


def test_coordination_binding_accepts_canonical_producer_and_consumer_runs() -> None:
    service, _, content, consumer, consumer_run_id = _fixture()
    handoff = service.create_handoff(content, idempotency_key="coordination-binding")

    runtime = service.consume_handoff(
        handoff.handoff_id,
        handoff.revision,
        consuming_run_id=consumer_run_id,
        consumer=consumer,
    )

    assert runtime.handoff == handoff
    assert runtime.consumption.consuming_run_id == consumer_run_id


def test_coordination_binding_rejects_noncanonical_producer_run() -> None:
    service, _, content, _, _ = _fixture()
    invalid = HandoffContent(
        task_id=content.task_id,
        plan_id=content.plan_id,
        producer_step_id=content.producer_step_id,
        consumer_step_id=content.consumer_step_id,
        producer_run_id=new_id("run"),
        producer=content.producer,
        intended_consumer=content.intended_consumer,
        objective=content.objective,
        completed_work_summary=content.completed_work_summary,
        recommended_next_action=content.recommended_next_action,
        requested_output=content.requested_output,
    )

    with pytest.raises(ContractError) as error:
        service.create_handoff(invalid, idempotency_key="wrong-producer-run")

    assert error.value.code is ErrorCode.CONFLICT


def test_coordination_binding_rejects_noncanonical_consumer_run_before_binding() -> None:
    service, base, content, consumer, _ = _fixture()
    handoff = service.create_handoff(content, idempotency_key="wrong-consumer-run")

    with pytest.raises(ContractError) as error:
        service.consume_handoff(
            handoff.handoff_id,
            handoff.revision,
            consuming_run_id=new_id("run"),
            consumer=consumer,
        )

    assert error.value.code is ErrorCode.CONFLICT
    assert base.list_consumptions(handoff.handoff_id, handoff.revision) == ()
