from __future__ import annotations

from dataclasses import FrozenInstanceError
from hashlib import sha256
from pathlib import Path

import pytest

from ai_multi_agent_platform.agents.models import AgentRevisionRef, AgentTeamRevisionRef
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.handoffs import (
    AgentHandoff,
    HandoffContent,
    HandoffControlPlaneProjection,
    HandoffService,
    HandoffSourceKind,
    HandoffSourceRef,
    InMemoryHandoffAuditSink,
    InMemoryHandoffRepository,
    SQLiteHandoffRepository,
    handoff_from_dict,
    handoff_to_dict,
    participant_key,
)


def _digest(label: str) -> str:
    return sha256(label.encode()).hexdigest()


class _AgentDirectory:
    def __init__(self) -> None:
        self.agents: set[tuple[str, int]] = set()
        self.teams: set[tuple[str, int]] = set()

    def add_agent(self, ref: AgentRevisionRef) -> None:
        self.agents.add((ref.agent_id, ref.revision))

    def add_team(self, ref: AgentTeamRevisionRef) -> None:
        self.teams.add((ref.team_id, ref.revision))

    def remove_agent(self, ref: AgentRevisionRef) -> None:
        self.agents.remove((ref.agent_id, ref.revision))

    def get_agent_revision(self, agent_id: str, revision: int) -> object:
        if (agent_id, revision) not in self.agents:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"agent revision not found: {agent_id}@{revision}",
            )
        return object()

    def get_team_revision(self, team_id: str, revision: int) -> object:
        if (team_id, revision) not in self.teams:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"team revision not found: {team_id}@{revision}",
            )
        return object()


class _ReferenceGateway:
    def __init__(self) -> None:
        self.existing: set[tuple[str, str, str | None, str | None]] = set()
        self.denied: set[tuple[tuple[str, str, int], tuple[str, str, str | None, str | None]]] = (
            set()
        )

    def add(self, reference: HandoffSourceRef) -> None:
        self.existing.add(reference.identity)

    def deny(
        self, participant: AgentRevisionRef | AgentTeamRevisionRef, reference: HandoffSourceRef
    ) -> None:
        self.denied.add((participant_key(participant), reference.identity))

    def exists(self, reference: HandoffSourceRef) -> bool:
        return reference.identity in self.existing

    def can_read(
        self,
        participant: AgentRevisionRef | AgentTeamRevisionRef,
        reference: HandoffSourceRef,
    ) -> bool:
        return (participant_key(participant), reference.identity) not in self.denied


class _AllowRequirements:
    def accepts(
        self,
        requirements: tuple[str, ...],
        consumer: AgentRevisionRef | AgentTeamRevisionRef,
    ) -> bool:
        return bool(requirements) and participant_key(consumer)[0] == "agent"


class _ViewAuthorizer:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed

    def can_view(self, principal_ref: str, handoff: AgentHandoff) -> bool:
        return self.allowed and bool(principal_ref) and bool(handoff.handoff_id)


def _fixture_ids() -> dict[str, str]:
    return {
        "task": new_id("task"),
        "plan": new_id("plan"),
        "producer_step": new_id("step"),
        "consumer_step": new_id("step"),
        "producer_run": new_id("run"),
        "consumer_run": new_id("run"),
    }


def _content(
    producer: AgentRevisionRef | AgentTeamRevisionRef,
    consumer: AgentRevisionRef | AgentTeamRevisionRef | None,
    *,
    ids: dict[str, str] | None = None,
    source_refs: tuple[HandoffSourceRef, ...] = (),
    requirements: tuple[str, ...] = (),
    completed: str = "Implemented the bounded producer step.",
) -> HandoffContent:
    values = ids or _fixture_ids()
    return HandoffContent(
        task_id=values["task"],
        plan_id=values["plan"],
        producer_step_id=values["producer_step"],
        consumer_step_id=values["consumer_step"],
        producer_run_id=values["producer_run"],
        producer=producer,
        intended_consumer=consumer,
        consumer_requirements=requirements,
        objective="Transfer the completed work to the next canonical step.",
        completed_work_summary=completed,
        unresolved_questions=("Should the optional extension be enabled?",),
        blockers=("External fixture is not available yet.",),
        assumptions=("The task scope remains unchanged.",),
        constraints=("Do not widen permissions.",),
        source_refs=source_refs,
        recommended_next_action="Review the referenced result and continue the consumer step.",
        requested_output="A verified consumer result.",
    )


def _service(
    repository: InMemoryHandoffRepository | SQLiteHandoffRepository,
    directory: _AgentDirectory,
    gateway: _ReferenceGateway,
    *,
    audit: InMemoryHandoffAuditSink | None = None,
    requirements: _AllowRequirements | None = None,
) -> HandoffService:
    return HandoffService(
        repository,
        agents=directory,  # type: ignore[arg-type]
        references=gateway,
        audit=audit,
        consumer_requirements=requirements,
    )


def test_agent_a_to_agent_b_handoff_is_immutable_and_consumable() -> None:
    producer = AgentRevisionRef(new_id("agent"), 1)
    consumer = AgentRevisionRef(new_id("agent"), 1)
    directory = _AgentDirectory()
    directory.add_agent(producer)
    directory.add_agent(consumer)
    gateway = _ReferenceGateway()
    audit = InMemoryHandoffAuditSink()
    service = _service(InMemoryHandoffRepository(), directory, gateway, audit=audit)
    ids = _fixture_ids()

    handoff = service.create_handoff(
        _content(producer, consumer, ids=ids),
        idempotency_key="a-to-b",
    )
    runtime = service.consume_handoff(
        handoff.handoff_id,
        handoff.revision,
        consuming_run_id=ids["consumer_run"],
        consumer=consumer,
    )

    assert runtime.handoff.content_digest == runtime.consumption.handoff_digest
    assert runtime.context_source.digest == handoff.content_digest
    assert [event.event_type for event in audit.events] == [
        "handoff.created",
        "handoff.consumed",
    ]
    with pytest.raises(FrozenInstanceError):
        handoff.content.objective = "mutated"  # type: ignore[misc]


def test_team_to_member_handoff_preserves_exact_revisions() -> None:
    producer_team = AgentTeamRevisionRef(new_id("team"), 3)
    consumer = AgentRevisionRef(new_id("agent"), 7)
    directory = _AgentDirectory()
    directory.add_team(producer_team)
    directory.add_agent(consumer)
    service = _service(InMemoryHandoffRepository(), directory, _ReferenceGateway())
    ids = _fixture_ids()

    handoff = service.create_handoff(
        _content(producer_team, consumer, ids=ids),
        idempotency_key="team-member",
    )
    runtime = service.consume_handoff(
        handoff.handoff_id,
        handoff.revision,
        consuming_run_id=ids["consumer_run"],
        consumer=consumer,
    )

    assert participant_key(runtime.handoff.producer) == ("team", producer_team.team_id, 3)
    assert participant_key(runtime.consumption.consumer) == ("agent", consumer.agent_id, 7)


def test_artifact_result_and_research_evidence_references_round_trip() -> None:
    producer = AgentRevisionRef(new_id("agent"), 1)
    consumer = AgentRevisionRef(new_id("agent"), 1)
    directory = _AgentDirectory()
    directory.add_agent(producer)
    directory.add_agent(consumer)
    refs = (
        HandoffSourceRef(
            HandoffSourceKind.ARTIFACT, new_id("artifact"), digest=_digest("artifact")
        ),
        HandoffSourceRef(HandoffSourceKind.RESULT, new_id("result"), digest=_digest("result")),
        HandoffSourceRef(
            HandoffSourceKind.RESEARCH_CLAIM,
            "research_claim_future-contract-1",
            revision="2",
            digest=_digest("claim"),
        ),
        HandoffSourceRef(
            HandoffSourceKind.RESEARCH_EVIDENCE,
            "research_evidence_future-contract-1",
            revision="4",
            digest=_digest("evidence"),
        ),
    )
    gateway = _ReferenceGateway()
    for reference in refs:
        gateway.add(reference)
    service = _service(InMemoryHandoffRepository(), directory, gateway)

    handoff = service.create_handoff(
        _content(producer, consumer, source_refs=refs),
        idempotency_key="source-refs",
    )
    decoded = handoff_from_dict(handoff_to_dict(handoff))

    assert decoded == handoff
    assert decoded.content.source_refs == refs
    assert decoded.content_digest == handoff.content_digest


def test_unauthorized_reference_is_denied_again_at_consumption() -> None:
    producer = AgentRevisionRef(new_id("agent"), 1)
    consumer = AgentRevisionRef(new_id("agent"), 1)
    directory = _AgentDirectory()
    directory.add_agent(producer)
    directory.add_agent(consumer)
    reference = HandoffSourceRef(
        HandoffSourceKind.ARTIFACT,
        new_id("artifact"),
        digest=_digest("protected"),
    )
    gateway = _ReferenceGateway()
    gateway.add(reference)
    service = _service(InMemoryHandoffRepository(), directory, gateway)
    ids = _fixture_ids()
    handoff = service.create_handoff(
        _content(producer, consumer, ids=ids, source_refs=(reference,)),
        idempotency_key="authorization",
    )

    gateway.deny(consumer, reference)
    with pytest.raises(ContractError) as error:
        service.consume_handoff(
            handoff.handoff_id,
            handoff.revision,
            consuming_run_id=ids["consumer_run"],
            consumer=consumer,
        )

    assert error.value.code is ErrorCode.FORBIDDEN
    assert service.list_consumptions(handoff.handoff_id, handoff.revision) == ()


def test_sqlite_restart_preserves_handoff_and_consuming_run_binding(tmp_path: Path) -> None:
    producer = AgentRevisionRef(new_id("agent"), 1)
    consumer = AgentRevisionRef(new_id("agent"), 1)
    directory = _AgentDirectory()
    directory.add_agent(producer)
    directory.add_agent(consumer)
    gateway = _ReferenceGateway()
    database = tmp_path / "handoffs.sqlite3"
    ids = _fixture_ids()

    first = _service(SQLiteHandoffRepository(database), directory, gateway)
    handoff = first.create_handoff(
        _content(producer, consumer, ids=ids),
        idempotency_key="restart",
    )

    second = _service(SQLiteHandoffRepository(database), directory, gateway)
    runtime = second.consume_handoff(
        handoff.handoff_id,
        handoff.revision,
        consuming_run_id=ids["consumer_run"],
        consumer=consumer,
    )

    third = _service(SQLiteHandoffRepository(database), directory, gateway)
    restored = third.get_handoff(handoff.handoff_id, handoff.revision)
    consumptions = third.list_consumptions(handoff.handoff_id, handoff.revision)
    assert restored.content_digest == handoff.content_digest
    assert consumptions == (runtime.consumption,)


def test_duplicate_creation_is_idempotent_and_content_change_conflicts() -> None:
    producer = AgentRevisionRef(new_id("agent"), 1)
    consumer = AgentRevisionRef(new_id("agent"), 1)
    directory = _AgentDirectory()
    directory.add_agent(producer)
    directory.add_agent(consumer)
    service = _service(InMemoryHandoffRepository(), directory, _ReferenceGateway())
    content = _content(producer, consumer)

    first = service.create_handoff(content, idempotency_key="duplicate-create")
    second = service.create_handoff(content, idempotency_key="duplicate-create")
    assert second == first

    with pytest.raises(ContractError) as error:
        service.create_handoff(
            _content(producer, consumer, completed="Different completion claim."),
            idempotency_key="duplicate-create",
        )
    assert error.value.code is ErrorCode.CONFLICT


def test_duplicate_consumption_is_idempotent() -> None:
    producer = AgentRevisionRef(new_id("agent"), 1)
    consumer = AgentRevisionRef(new_id("agent"), 1)
    directory = _AgentDirectory()
    directory.add_agent(producer)
    directory.add_agent(consumer)
    service = _service(InMemoryHandoffRepository(), directory, _ReferenceGateway())
    ids = _fixture_ids()
    handoff = service.create_handoff(
        _content(producer, consumer, ids=ids),
        idempotency_key="duplicate-consume",
    )

    first = service.consume_handoff(
        handoff.handoff_id,
        1,
        consuming_run_id=ids["consumer_run"],
        consumer=consumer,
    )
    second = service.consume_handoff(
        handoff.handoff_id,
        1,
        consuming_run_id=ids["consumer_run"],
        consumer=consumer,
    )

    assert second.consumption == first.consumption
    assert len(service.list_consumptions(handoff.handoff_id, 1)) == 1


def test_stale_revision_cannot_replace_newer_handoff() -> None:
    producer = AgentRevisionRef(new_id("agent"), 1)
    consumer = AgentRevisionRef(new_id("agent"), 1)
    directory = _AgentDirectory()
    directory.add_agent(producer)
    directory.add_agent(consumer)
    service = _service(InMemoryHandoffRepository(), directory, _ReferenceGateway())
    handoff_id = new_id("handoff")
    ids = _fixture_ids()

    first = service.create_handoff(
        _content(producer, consumer, ids=ids),
        idempotency_key="revision-1",
        handoff_id=handoff_id,
    )
    second = service.create_handoff(
        _content(producer, consumer, ids=ids, completed="Reviewed completion."),
        idempotency_key="revision-2",
        handoff_id=handoff_id,
        expected_previous_revision=1,
    )
    assert first.revision == 1
    assert second.revision == 2

    with pytest.raises(ContractError) as error:
        service.create_handoff(
            _content(producer, consumer, ids=ids, completed="Stale rewrite."),
            idempotency_key="stale-revision",
            handoff_id=handoff_id,
            expected_previous_revision=1,
        )
    assert error.value.code is ErrorCode.CONFLICT
    assert service.get_handoff(handoff_id).content_digest == second.content_digest


def test_agent_revision_replacement_does_not_change_historical_consumer() -> None:
    producer_v1 = AgentRevisionRef(new_id("agent"), 1)
    consumer_v1 = AgentRevisionRef(new_id("agent"), 1)
    producer_v2 = AgentRevisionRef(producer_v1.agent_id, 2)
    consumer_v2 = AgentRevisionRef(consumer_v1.agent_id, 2)
    directory = _AgentDirectory()
    for reference in (producer_v1, consumer_v1, producer_v2, consumer_v2):
        directory.add_agent(reference)
    service = _service(InMemoryHandoffRepository(), directory, _ReferenceGateway())
    ids = _fixture_ids()
    handoff = service.create_handoff(
        _content(producer_v1, consumer_v1, ids=ids),
        idempotency_key="revision-pinning",
    )

    with pytest.raises(ContractError) as error:
        service.consume_handoff(
            handoff.handoff_id,
            1,
            consuming_run_id=ids["consumer_run"],
            consumer=consumer_v2,
        )
    assert error.value.code is ErrorCode.FORBIDDEN

    runtime = service.consume_handoff(
        handoff.handoff_id,
        1,
        consuming_run_id=ids["consumer_run"],
        consumer=consumer_v1,
    )
    assert runtime.handoff.intended_consumer == consumer_v1


def test_missing_pinned_consumer_revision_fails_explicitly() -> None:
    producer = AgentRevisionRef(new_id("agent"), 1)
    consumer = AgentRevisionRef(new_id("agent"), 1)
    directory = _AgentDirectory()
    directory.add_agent(producer)
    directory.add_agent(consumer)
    service = _service(InMemoryHandoffRepository(), directory, _ReferenceGateway())
    ids = _fixture_ids()
    handoff = service.create_handoff(
        _content(producer, consumer, ids=ids),
        idempotency_key="missing-revision",
    )
    directory.remove_agent(consumer)

    with pytest.raises(ContractError) as error:
        service.consume_handoff(
            handoff.handoff_id,
            1,
            consuming_run_id=ids["consumer_run"],
            consumer=consumer,
        )
    assert error.value.code is ErrorCode.NOT_FOUND


def test_context_bundle_binding_is_progressive_and_provider_neutral() -> None:
    producer = AgentRevisionRef(new_id("agent"), 1)
    consumer = AgentRevisionRef(new_id("agent"), 1)
    directory = _AgentDirectory()
    directory.add_agent(producer)
    directory.add_agent(consumer)
    gateway = _ReferenceGateway()
    context_bundle = HandoffSourceRef(
        HandoffSourceKind.CONTEXT_BUNDLE,
        "context_bundle_future-contract-1",
        revision="1",
        digest=_digest("context"),
    )
    gateway.add(context_bundle)
    service = _service(InMemoryHandoffRepository(), directory, gateway)
    ids = _fixture_ids()
    handoff = service.create_handoff(
        _content(producer, consumer, ids=ids),
        idempotency_key="context-source",
    )
    runtime = service.consume_handoff(
        handoff.handoff_id,
        1,
        consuming_run_id=ids["consumer_run"],
        consumer=consumer,
        context_bundle_ref=context_bundle,
    )

    assert runtime.context_source.source_type == "agent_handoff"
    assert runtime.context_source.selection_reason == "intentional_agent_handoff"
    assert runtime.context_source.digest == handoff.content_digest
    assert runtime.consumption.context_bundle_ref == context_bundle


def test_consumer_requirements_allow_late_binding_without_new_assignment_system() -> None:
    producer = AgentRevisionRef(new_id("agent"), 1)
    consumer = AgentRevisionRef(new_id("agent"), 4)
    directory = _AgentDirectory()
    directory.add_agent(producer)
    directory.add_agent(consumer)
    service = _service(
        InMemoryHandoffRepository(),
        directory,
        _ReferenceGateway(),
        requirements=_AllowRequirements(),
    )
    ids = _fixture_ids()
    handoff = service.create_handoff(
        _content(
            producer,
            None,
            ids=ids,
            requirements=("role:reviewer", "read-only"),
        ),
        idempotency_key="requirements",
    )

    runtime = service.consume_handoff(
        handoff.handoff_id,
        1,
        consuming_run_id=ids["consumer_run"],
        consumer=consumer,
    )
    assert runtime.consumption.consumer == consumer


def test_orchestrator_replacement_preserves_canonical_handoff_identity() -> None:
    producer = AgentRevisionRef(new_id("agent"), 1)
    consumer = AgentRevisionRef(new_id("agent"), 1)
    directory = _AgentDirectory()
    directory.add_agent(producer)
    directory.add_agent(consumer)
    service = _service(InMemoryHandoffRepository(), directory, _ReferenceGateway())
    handoff = service.create_handoff(
        _content(producer, consumer),
        idempotency_key="orchestrator-neutral",
    )

    first_render = ("reference-orchestrator", handoff.handoff_id, handoff.content_digest)
    second_render = ("replacement-orchestrator", handoff.handoff_id, handoff.content_digest)
    assert first_render[1:] == second_render[1:]
    assert "orchestrator" not in handoff_to_dict(handoff)["content"]


def test_control_plane_projection_is_read_only_and_permission_aware() -> None:
    producer = AgentRevisionRef(new_id("agent"), 1)
    consumer = AgentRevisionRef(new_id("agent"), 1)
    directory = _AgentDirectory()
    directory.add_agent(producer)
    directory.add_agent(consumer)
    service = _service(InMemoryHandoffRepository(), directory, _ReferenceGateway())
    handoff = service.create_handoff(
        _content(producer, consumer),
        idempotency_key="projection",
    )

    allowed = HandoffControlPlaneProjection(service, authorization=_ViewAuthorizer(True))
    denied = HandoffControlPlaneProjection(service, authorization=_ViewAuthorizer(False))
    projected = allowed.get_handoff("user:reader", handoff.handoff_id, 1)
    assert projected["content_digest"] == handoff.content_digest
    assert "permissions" not in projected

    with pytest.raises(ContractError) as error:
        denied.get_handoff("user:denied", handoff.handoff_id, 1)
    assert error.value.code is ErrorCode.FORBIDDEN
