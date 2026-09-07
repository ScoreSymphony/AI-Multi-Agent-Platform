"""Control Plane projection and commands for canonical planning proposals."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import CommandHandler
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.models import RoutingRequirements
from ai_multi_agent_platform.security import ActorIdentity, ActorType

from .models import PlanningStepDraft, PlanningTrigger, ProposalRecord
from .service import PlanningService


class PlanningProposalResourceService:
    def __init__(self, planning: PlanningService) -> None:
        self._planning = planning

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(_record_resource(record) for record in self._planning.repository.list_all())

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        return _record_resource(self._planning.repository.get(resource_id))


class PlanningCommandHandlers:
    def __init__(self, planning: PlanningService) -> None:
        self._planning = planning

    async def propose(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if context.idempotency_key is None:
            raise ContractError(ErrorCode.INVALID_REQUEST, "idempotency key is required")
        trigger_raw = payload.get("trigger", PlanningTrigger.INITIAL.value)
        if not isinstance(trigger_raw, str):
            raise ContractError(ErrorCode.INVALID_REQUEST, "planning trigger must be a string")
        try:
            trigger = PlanningTrigger(trigger_raw)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"unsupported planning trigger: {trigger_raw}",
            ) from exc
        reason = _optional_string(payload, "reason")
        workspace_id = _optional_string(payload, "workspace_id")
        evidence_refs = _string_tuple(payload, "evidence_refs")
        task_constraints = _string_tuple(payload, "task_constraints")
        max_steps = _positive_int(payload, "max_steps", 128)
        max_parallel_steps = _optional_positive_int(payload, "max_parallel_steps")
        record = await self._planning.propose(
            task_id=resource_ref,
            idempotency_key=context.idempotency_key,
            trigger=trigger,
            reason=reason,
            workspace_id=workspace_id,
            evidence_refs=evidence_refs,
            task_constraints=task_constraints,
            max_steps=max_steps,
            max_parallel_steps=max_parallel_steps,
        )
        return _record_resource(record)

    async def activate(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if context.idempotency_key is None:
            raise ContractError(ErrorCode.INVALID_REQUEST, "idempotency key is required")
        actor_type = context.actor.actor_type or ActorType.SERVICE.value
        try:
            canonical_actor_type = ActorType(actor_type)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"unsupported planning actor type: {actor_type}",
            ) from exc
        actor = ActorIdentity(
            actor_id=context.actor.principal_ref,
            actor_type=canonical_actor_type,
        )
        record = await self._planning.activate(
            resource_ref,
            idempotency_key=context.idempotency_key,
            actor=actor,
            approval_id=_optional_string(payload, "approval_id"),
        )
        return _record_resource(record)

    async def reject(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        record = await self._planning.reject(
            resource_ref,
            reason=_optional_string(payload, "reason"),
        )
        return _record_resource(record)


def planning_resource_services(
    planning: PlanningService,
) -> dict[str, PlanningProposalResourceService]:
    return {"planning-proposals": PlanningProposalResourceService(planning)}


def planning_command_handlers(planning: PlanningService) -> dict[str, CommandHandler]:
    handlers = PlanningCommandHandlers(planning)
    return {
        "planning.propose": handlers.propose,
        "planning.activate": handlers.activate,
        "planning.reject": handlers.reject,
    }


def _record_resource(record: ProposalRecord) -> dict[str, JsonValue]:
    proposal = record.proposal
    return {
        "id": proposal.proposal_id,
        "task_id": proposal.task_id,
        "task_revision": proposal.task_revision,
        "plan_revision": proposal.plan_revision,
        "base_plan_id": proposal.base_plan_id,
        "trigger": proposal.trigger.value,
        "reason": proposal.reason,
        "summary": proposal.summary,
        "status": record.status.value,
        "proposal_digest": proposal.digest,
        "planner": {
            "planner_id": proposal.planner.planner_id,
            "kind": proposal.planner.kind.value,
            "version": proposal.planner.version,
            "model_config_id": proposal.model_config_id,
        },
        "assumptions": list(proposal.assumptions),
        "constraints": list(proposal.constraints),
        "evidence_refs": list(proposal.evidence_refs),
        "steps": [_step_resource(step) for step in proposal.steps],
        "validation": {
            "valid": record.validation.valid,
            "errors": list(record.validation.errors),
            "warnings": list(record.validation.warnings),
            "approval_required": record.validation.approval_required,
        },
        "activation_plan_id": record.activation_plan_id,
        "approval_id": record.approval_id,
        "failure_reason": record.failure_reason,
        "record_revision": record.revision,
        "created_at": proposal.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _step_resource(step: PlanningStepDraft) -> dict[str, JsonValue]:
    assignment: dict[str, JsonValue] | None = None
    if step.assignment is not None:
        assignment = {
            "agent_id": step.assignment.agent_id,
            "agent_revision": step.assignment.agent_revision,
            "team_id": step.assignment.team_id,
            "team_revision": step.assignment.team_revision,
            "role_requirement": step.assignment.role_requirement,
            "rationale": step.assignment.rationale,
        }
    return {
        "key": step.key,
        "title": step.title,
        "objective": step.objective,
        "depends_on": list(step.depends_on),
        "assignment": assignment,
        "capability_requirements": [
            {
                "capability_id": item.capability_id,
                "exact_version": item.exact_version,
                "required_features": list(item.required_features),
                "required": item.required,
            }
            for item in step.capability_requirements
        ],
        "model_requirements": _requirements_resource(step.model_requirements),
        "requires_model": step.requires_model,
        "workspace_id": step.workspace_id,
        "input_refs": list(step.input_refs),
        "output_refs": list(step.output_refs),
        "expected_evidence": list(step.expected_evidence),
        "verification_policy_refs": list(step.verification_policy_refs),
        "reuse_step_ids": list(step.reuse_step_ids),
        "metadata": dict(step.metadata),
    }


def _requirements_resource(requirements: RoutingRequirements) -> dict[str, JsonValue]:
    return {
        "model_config_id": requirements.explicit_model_id,
        "min_context_window": requirements.min_context_window,
        "tool_calling": requirements.tool_calling,
        "structured_output": requirements.structured_output,
        "streaming": requirements.streaming,
        "modalities": list(requirements.modalities),
        "reasoning": list(requirements.reasoning),
        "local_only": requirements.local_only,
        "self_hosted_only": requirements.self_hosted_only,
    }


def _optional_string(payload: dict[str, JsonValue], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-blank string")
    return value


def _string_tuple(payload: dict[str, JsonValue], key: str) -> tuple[str, ...]:
    value = payload.get(key, [])
    if not isinstance(value, list):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{key} must be a list of non-blank strings",
        )
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"{key} must be a list of non-blank strings",
            )
        result.append(item)
    return tuple(result)


def _positive_int(payload: dict[str, JsonValue], key: str, default: int) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a positive integer")
    return value


def _optional_positive_int(payload: dict[str, JsonValue], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a positive integer")
    return value
