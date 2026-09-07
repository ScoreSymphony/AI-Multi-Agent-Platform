"""Application service for explicit, authorization-preserving Agent Handoffs."""

from __future__ import annotations

from typing import Protocol

from ai_multi_agent_platform.agents.models import (
    AgentRevision,
    AgentRevisionRef,
    AgentTeamRevision,
    AgentTeamRevisionRef,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import validate_id

from .models import (
    AgentHandoff,
    HandoffAuditEvent,
    HandoffConsumption,
    HandoffContent,
    HandoffRuntimeContext,
    HandoffSourceRef,
    ParticipantRef,
    build_handoff,
    compute_creation_request_digest,
    handoff_context_source,
    new_handoff_id,
    participant_key,
)
from .repository import HandoffRepository


class HandoffAgentRevisionRepository(Protocol):
    """Narrow #33 lookup seam; compatible with the canonical AgentRepository."""

    def get_agent_revision(self, agent_id: str, revision: int) -> AgentRevision: ...

    def get_team_revision(self, team_id: str, revision: int) -> AgentTeamRevision: ...


class HandoffReferenceGateway(Protocol):
    """Source existence + #15 read-authorization seam.

    Implementations must query the owning Artifact/Result/Research/Skill/Context service and
    authorization boundary. Discovery is never treated as permission.
    """

    def exists(self, reference: HandoffSourceRef) -> bool: ...

    def can_read(self, participant: ParticipantRef, reference: HandoffSourceRef) -> bool: ...


class ConsumerRequirementEvaluator(Protocol):
    """Resolve provider-neutral consumer requirements when no exact consumer was preselected."""

    def accepts(self, requirements: tuple[str, ...], consumer: ParticipantRef) -> bool: ...


class HandoffAuditSink(Protocol):
    def record(self, event: HandoffAuditEvent) -> None: ...


class NullHandoffAuditSink:
    def record(self, event: HandoffAuditEvent) -> None:
        del event


class InMemoryHandoffAuditSink:
    def __init__(self) -> None:
        self.events: list[HandoffAuditEvent] = []

    def record(self, event: HandoffAuditEvent) -> None:
        self.events.append(event)


class HandoffService:
    """Create immutable Handoffs and bind their exact revisions to consuming Runs.

    This service deliberately never changes Task/Plan/Step/Run state. The durable coordinator
    remains the lifecycle authority; this service only records the transfer artifact and its
    exact consumption evidence.
    """

    def __init__(
        self,
        repository: HandoffRepository,
        *,
        agents: HandoffAgentRevisionRepository,
        references: HandoffReferenceGateway,
        audit: HandoffAuditSink | None = None,
        consumer_requirements: ConsumerRequirementEvaluator | None = None,
    ) -> None:
        self._repository = repository
        self._agents = agents
        self._references = references
        self._audit = audit or NullHandoffAuditSink()
        self._consumer_requirements = consumer_requirements

    def create_handoff(
        self,
        content: HandoffContent,
        *,
        idempotency_key: str,
        handoff_id: str | None = None,
        expected_previous_revision: int = 0,
    ) -> AgentHandoff:
        """Persist one explicit transfer before any consumer may rely on it."""

        if not idempotency_key.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "handoff idempotency key is required")
        if expected_previous_revision < 0:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "expected_previous_revision must be >= 0",
            )
        self._require_participant_revision(content.producer)
        if content.intended_consumer is not None:
            self._require_participant_revision(content.intended_consumer)
        self._validate_sources_for_creation(content)

        canonical_id = handoff_id or new_handoff_id()
        validate_id(canonical_id, "handoff")
        handoff = build_handoff(
            handoff_id=canonical_id,
            revision=expected_previous_revision + 1,
            content=content,
        )
        stored, created = self._repository.create_handoff(
            handoff,
            idempotency_key=idempotency_key,
            request_digest=compute_creation_request_digest(content),
            expected_previous_revision=expected_previous_revision,
        )
        if created:
            details: dict[str, JsonValue] = {
                "producer": _participant_label(content.producer),
                "content_digest": stored.content_digest,
                "producer_step_id": content.producer_step_id,
                "consumer_step_id": content.consumer_step_id,
            }
            if content.intended_consumer is not None:
                details["consumer"] = _participant_label(content.intended_consumer)
            self._audit.record(
                HandoffAuditEvent(
                    event_type="handoff.created",
                    handoff_id=stored.handoff_id,
                    revision=stored.revision,
                    task_id=stored.content.task_id,
                    details=details,
                )
            )
        return stored

    def consume_handoff(
        self,
        handoff_id: str,
        revision: int,
        *,
        consuming_run_id: str,
        consumer: ParticipantRef,
        context_bundle_ref: HandoffSourceRef | None = None,
    ) -> HandoffRuntimeContext:
        """Authorize, validate and durably bind a Handoff before returning runtime context."""

        validate_id(consuming_run_id, "run")
        self._require_participant_revision(consumer)
        handoff = self._repository.get_handoff(handoff_id, revision)
        self._require_expected_consumer(handoff, consumer)
        self._validate_sources_for_consumer(handoff, consumer)
        if context_bundle_ref is not None:
            self._require_reference_exists(context_bundle_ref)
            self._require_reference_readable(consumer, context_bundle_ref)

        candidate = HandoffConsumption(
            handoff_id=handoff.handoff_id,
            handoff_revision=handoff.revision,
            handoff_digest=handoff.content_digest,
            consuming_run_id=consuming_run_id,
            consumer=consumer,
            context_bundle_ref=context_bundle_ref,
        )
        consumption, created = self._repository.bind_consumption(candidate)
        if created:
            self._audit.record(
                HandoffAuditEvent(
                    event_type="handoff.consumed",
                    handoff_id=handoff.handoff_id,
                    revision=handoff.revision,
                    task_id=handoff.content.task_id,
                    consuming_run_id=consuming_run_id,
                    details={
                        "consumer": _participant_label(consumer),
                        "content_digest": handoff.content_digest,
                    },
                )
            )
        return HandoffRuntimeContext(
            handoff=handoff,
            consumption=consumption,
            context_source=handoff_context_source(handoff),
        )

    def get_handoff(self, handoff_id: str, revision: int | None = None) -> AgentHandoff:
        return self._repository.get_handoff(handoff_id, revision)

    def list_handoffs_for_task(self, task_id: str) -> tuple[AgentHandoff, ...]:
        validate_id(task_id, "task")
        return self._repository.list_handoffs_for_task(task_id)

    def list_handoffs_for_step(self, step_id: str) -> tuple[AgentHandoff, ...]:
        validate_id(step_id, "step")
        return self._repository.list_handoffs_for_step(step_id)

    def list_consumptions(
        self, handoff_id: str, revision: int
    ) -> tuple[HandoffConsumption, ...]:
        return self._repository.list_consumptions(handoff_id, revision)

    def _validate_sources_for_creation(self, content: HandoffContent) -> None:
        for reference in content.source_refs:
            self._require_reference_exists(reference)
            self._require_reference_readable(content.producer, reference)
            if content.intended_consumer is not None:
                self._require_reference_readable(content.intended_consumer, reference)

    def _validate_sources_for_consumer(
        self, handoff: AgentHandoff, consumer: ParticipantRef
    ) -> None:
        for reference in handoff.content.source_refs:
            try:
                self._require_reference_exists(reference)
                self._require_reference_readable(consumer, reference)
            except ContractError as exc:
                self._audit.record(
                    HandoffAuditEvent(
                        event_type="handoff.reference_denied",
                        handoff_id=handoff.handoff_id,
                        revision=handoff.revision,
                        task_id=handoff.content.task_id,
                        details={
                            "reference_kind": reference.kind.value,
                            "reference_id": reference.resource_id,
                            "error_code": exc.code.value,
                        },
                    )
                )
                raise

    def _require_expected_consumer(
        self, handoff: AgentHandoff, consumer: ParticipantRef
    ) -> None:
        expected = handoff.content.intended_consumer
        if expected is not None:
            if participant_key(expected) != participant_key(consumer):
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "handoff is bound to a different consumer Agent/Team revision",
                    details={
                        "expected_consumer": _participant_label(expected),
                        "actual_consumer": _participant_label(consumer),
                    },
                )
            return
        evaluator = self._consumer_requirements
        if evaluator is None or not evaluator.accepts(
            handoff.content.consumer_requirements,
            consumer,
        ):
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "consumer does not satisfy handoff consumer requirements",
            )

    def _require_participant_revision(self, participant: ParticipantRef) -> None:
        if isinstance(participant, AgentRevisionRef):
            self._agents.get_agent_revision(participant.agent_id, participant.revision)
            return
        self._agents.get_team_revision(participant.team_id, participant.revision)

    def _require_reference_exists(self, reference: HandoffSourceRef) -> None:
        if not self._references.exists(reference):
            raise ContractError(
                ErrorCode.NOT_FOUND,
                "handoff source reference is missing or stale",
                details={
                    "reference_kind": reference.kind.value,
                    "reference_id": reference.resource_id,
                },
            )

    def _require_reference_readable(
        self, participant: ParticipantRef, reference: HandoffSourceRef
    ) -> None:
        if not self._references.can_read(participant, reference):
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "handoff source reference is not authorized for participant",
                details={
                    "participant": _participant_label(participant),
                    "reference_kind": reference.kind.value,
                    "reference_id": reference.resource_id,
                },
            )


def _participant_label(participant: ParticipantRef) -> str:
    kind, resource_id, revision = participant_key(participant)
    return f"{kind}:{resource_id}@{revision}"
