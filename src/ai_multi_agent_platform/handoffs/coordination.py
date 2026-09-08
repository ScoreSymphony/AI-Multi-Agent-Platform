"""Read-only #384 integration for canonical Agent Handoff execution references."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.coordination.models import PlanRuntimeState
from ai_multi_agent_platform.coordination.repository import CoordinatorRepository

from .models import (
    AgentHandoff,
    HandoffConsumption,
    HandoffContent,
    HandoffRuntimeContext,
    HandoffSourceRef,
    ParticipantRef,
)
from .service import HandoffService


class CoordinatedHandoffService:
    """Validate Handoff execution bindings against the durable #384 coordinator.

    The wrapper is deliberately read-only with respect to coordination state. It never creates,
    progresses, retries, assigns, cancels or repairs a Step/Run. It only proves that the
    references persisted in a Handoff describe the canonical execution boundary that #384
    already owns.
    """

    def __init__(self, handoffs: HandoffService, coordinator: CoordinatorRepository) -> None:
        self._handoffs = handoffs
        self._coordinator = coordinator

    def create_handoff(
        self,
        content: HandoffContent,
        *,
        idempotency_key: str,
        handoff_id: str | None = None,
        expected_previous_revision: int = 0,
    ) -> AgentHandoff:
        self._require_creation_binding(content)
        return self._handoffs.create_handoff(
            content,
            idempotency_key=idempotency_key,
            handoff_id=handoff_id,
            expected_previous_revision=expected_previous_revision,
        )

    def consume_handoff(
        self,
        handoff_id: str,
        revision: int,
        *,
        consuming_run_id: str,
        consumer: ParticipantRef,
        context_bundle_ref: HandoffSourceRef | None = None,
    ) -> HandoffRuntimeContext:
        handoff = self._handoffs.get_handoff(handoff_id, revision)
        self._require_consumption_binding(handoff, consuming_run_id)
        return self._handoffs.consume_handoff(
            handoff_id,
            revision,
            consuming_run_id=consuming_run_id,
            consumer=consumer,
            context_bundle_ref=context_bundle_ref,
        )

    def get_handoff(self, handoff_id: str, revision: int | None = None) -> AgentHandoff:
        return self._handoffs.get_handoff(handoff_id, revision)

    def list_handoffs_for_task(self, task_id: str) -> tuple[AgentHandoff, ...]:
        return self._handoffs.list_handoffs_for_task(task_id)

    def list_handoffs_for_step(self, step_id: str) -> tuple[AgentHandoff, ...]:
        return self._handoffs.list_handoffs_for_step(step_id)

    def list_consumptions(self, handoff_id: str, revision: int) -> tuple[HandoffConsumption, ...]:
        return self._handoffs.list_consumptions(handoff_id, revision)

    def _require_creation_binding(self, content: HandoffContent) -> None:
        state = self._coordinator.get_plan(content.plan_id)
        if state.plan.task_id != content.task_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "handoff Task does not match the canonical coordination Plan",
                details={"task_id": content.task_id, "plan_id": content.plan_id},
            )
        self._require_step(state, content.producer_step_id, "producer")
        self._require_step(state, content.consumer_step_id, "consumer")
        producer = self._coordinator.get_step_record(content.producer_step_id)
        if producer.task_id != content.task_id or producer.plan_id != content.plan_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "producer Step coordination binding does not match the Handoff",
                details={"producer_step_id": content.producer_step_id},
            )
        if producer.latest_run_id != content.producer_run_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "producer Run is not the canonical latest Run for the producer Step",
                details={
                    "producer_step_id": content.producer_step_id,
                    "producer_run_id": content.producer_run_id,
                    "canonical_run_id": producer.latest_run_id,
                },
            )

    def _require_consumption_binding(self, handoff: AgentHandoff, consuming_run_id: str) -> None:
        content = handoff.content
        state = self._coordinator.get_plan(content.plan_id)
        if state.plan.task_id != content.task_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "stored Handoff no longer resolves to its canonical coordination Task",
                details={"handoff_id": handoff.handoff_id},
            )
        self._require_step(state, content.consumer_step_id, "consumer")
        consumer = self._coordinator.get_step_record(content.consumer_step_id)
        if consumer.task_id != content.task_id or consumer.plan_id != content.plan_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "consumer Step coordination binding does not match the Handoff",
                details={"consumer_step_id": content.consumer_step_id},
            )
        if consumer.latest_run_id != consuming_run_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "consuming Run is not the canonical latest Run for the consumer Step",
                details={
                    "consumer_step_id": content.consumer_step_id,
                    "consuming_run_id": consuming_run_id,
                    "canonical_run_id": consumer.latest_run_id,
                },
            )

    @staticmethod
    def _require_step(state: PlanRuntimeState, step_id: str, role: str) -> None:
        try:
            state.step(step_id)
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"{role} Step is not part of the canonical coordination Plan",
                details={"step_id": step_id},
            ) from exc
