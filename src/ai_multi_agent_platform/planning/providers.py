"""Replaceable planner implementations behind platform-owned planning contracts."""

from __future__ import annotations

import json
from typing import Any, Protocol

from ai_multi_agent_platform.contracts import (
    Capability,
    CapabilityKind,
    ContractError,
    ErrorCode,
    HealthStatus,
    JsonValue,
    ModelRequest,
    ModelRouter,
    Orchestrator,
    PlanRequest,
    PlanResponse,
    PlanStepProposal,
    ProviderDescriptor,
)
from ai_multi_agent_platform.contracts.types import AdapterMetadata
from ai_multi_agent_platform.models import ModelRegistry, RoutingRequirements

from .models import (
    AgentAssignment,
    CapabilityRequirement,
    PlanDraft,
    PlannerDescriptor,
    PlannerKind,
    PlannerOutput,
    PlanningRequest,
    PlanningStepDraft,
)
from .repository import PlanningRepository


class Planner(Protocol):
    @property
    def descriptor(self) -> PlannerDescriptor: ...

    async def propose(self, request: PlanningRequest) -> PlannerOutput: ...


class DeterministicReferencePlanner:
    """No-LLM reference planner used for local operation and contract fixtures."""

    def __init__(
        self, draft: PlanDraft | None = None, *, planner_id: str = "reference-planner"
    ) -> None:
        self._draft = draft
        self._descriptor = PlannerDescriptor(planner_id, PlannerKind.DETERMINISTIC)

    @property
    def descriptor(self) -> PlannerDescriptor:
        return self._descriptor

    async def propose(self, request: PlanningRequest) -> PlannerOutput:
        draft = self._draft
        if draft is None:
            assignment = None
            model_requirements = RoutingRequirements()
            enabled_agents = [
                candidate for candidate in request.inventory.agents if candidate.enabled
            ]
            if enabled_agents:
                selected = sorted(enabled_agents, key=lambda item: (item.agent_id, item.revision))[
                    0
                ]
                assignment = AgentAssignment(
                    agent_id=selected.agent_id,
                    agent_revision=selected.revision,
                    rationale="deterministic first compatible canonical Agent",
                )
                model_requirements = selected.model_requirements
            draft = PlanDraft(
                summary="Deterministic reference plan",
                steps=(
                    PlanningStepDraft(
                        key="step-1",
                        title="Complete task objective",
                        objective=request.objective,
                        assignment=assignment,
                        model_requirements=model_requirements,
                        requires_model=assignment is not None,
                    ),
                ),
                constraints=request.task_constraints,
            )
        return PlannerOutput(draft=draft, planner=self.descriptor)


class ModelBackedPlanner:
    """Model planner using only #10 canonical routing/provider APIs.

    Provider-native model names are never copied into the proposal. Provenance records only
    the canonical model configuration selected by the platform router.
    """

    def __init__(
        self,
        *,
        router: ModelRouter,
        registry: ModelRegistry,
        requirements: RoutingRequirements | None = None,
        planner_id: str = "model-planner",
    ) -> None:
        self._router = router
        self._registry = registry
        self._requirements = requirements or RoutingRequirements(
            structured_output=True,
            self_hosted_only=True,
        )
        self._descriptor = PlannerDescriptor(planner_id, PlannerKind.MODEL)

    @property
    def descriptor(self) -> PlannerDescriptor:
        return self._descriptor

    async def propose(self, request: PlanningRequest) -> PlannerOutput:
        model_request = ModelRequest(
            request_id=f"planning:{request.task_id}:{request.task_revision}",
            messages=(self._prompt(request),),
            context=request.context,
            requirements=_routing_requirements_payload(self._requirements),
        )
        selection = await self._router.select_provider(model_request)
        if selection.model_ref is None:
            raise ContractError(
                ErrorCode.NO_COMPATIBLE_ROUTE,
                "planning model route did not return a canonical model configuration",
            )
        configured = self._registry.get_model(selection.model_ref)
        if configured.provider_id != selection.provider_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "model router/provider registry disagreement for planning selection",
            )
        provider = self._registry.get_provider(selection.provider_id)
        response = await provider.generate(model_request)
        if response.request_id != model_request.request_id:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "planning model response request_id does not match request",
                provider_id=selection.provider_id,
            )
        try:
            raw = json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "planning model response is not valid JSON",
                provider_id=selection.provider_id,
            ) from exc
        if not isinstance(raw, dict):
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "planning model response must be a JSON object",
                provider_id=selection.provider_id,
            )
        try:
            draft = draft_from_mapping(raw)
        except (TypeError, ValueError) as exc:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                f"planning model returned an invalid canonical draft: {exc}",
                provider_id=selection.provider_id,
            ) from exc
        return PlannerOutput(
            draft=draft,
            planner=self.descriptor,
            model_config_id=configured.config_id,
        )

    def _prompt(self, request: PlanningRequest) -> str:
        inventory = {
            "agents": [
                {
                    "agent_id": item.agent_id,
                    "revision": item.revision,
                    "role": item.role,
                    "enabled": item.enabled,
                    "allowed_capability_ids": list(item.allowed_capability_ids),
                }
                for item in request.inventory.agents
            ],
            "teams": [
                {
                    "team_id": item.team_id,
                    "revision": item.revision,
                    "enabled": item.enabled,
                    "member_agent_ids": list(item.member_agent_ids),
                    "shared_capability_ids": list(item.shared_capability_ids),
                }
                for item in request.inventory.teams
            ],
            "capabilities": [
                {
                    "capability_id": item.capability_id,
                    "version": item.version,
                    "available": item.available,
                    "features": list(item.features),
                }
                for item in request.inventory.capabilities
            ],
            "models": [
                {
                    "model_config_id": item.model_config_id,
                    "available": item.available,
                    "location": item.location.value,
                    "context_window": item.context_window,
                    "tool_calling": item.tool_calling,
                    "structured_output": item.structured_output,
                    "modalities": list(item.modalities),
                    "reasoning": list(item.reasoning),
                }
                for item in request.inventory.models
            ],
        }
        prior = None
        if request.prior_plan is not None:
            prior = {
                "plan_id": request.prior_plan.plan_id,
                "revision": request.prior_plan.revision,
                "completed_step_ids": list(request.prior_plan.completed_step_ids),
                "running_step_ids": list(request.prior_plan.running_step_ids),
                "failed_step_ids": list(request.prior_plan.failed_step_ids),
                "not_started_step_ids": list(request.prior_plan.not_started_step_ids),
                "result_refs": list(request.prior_plan.result_refs),
                "artifact_refs": list(request.prior_plan.artifact_refs),
            }
        payload = {
            "objective": request.objective,
            "trigger": request.trigger.value,
            "reason": request.reason,
            "task_constraints": list(request.task_constraints),
            "workspace_id": request.workspace_id,
            "prior_plan": prior,
            "evidence_refs": list(request.evidence_refs),
            "inventory": inventory,
        }
        return (
            "Return only one JSON object describing a provider-neutral plan draft. "
            "Use only the canonical IDs supplied below. Never invent provider-native model, "
            "host, worker or tool identities. The object must have summary, steps, assumptions "
            "and constraints. Each step may contain key, title, objective, depends_on, assignment, "
            "capability_requirements, model_requirements, requires_model, workspace_id, "
            "input_refs, output_refs, expected_evidence, verification_policy_refs and "
            "reuse_step_ids.\n" + json.dumps(payload, sort_keys=True)
        )


class PlanningOrchestratorAdapter(Orchestrator):
    """Bridge an activation proposal into the existing Kernel Orchestrator seam.

    A fallback keeps ordinary pre-#439 ``kernel.plan_task`` behavior intact. Only while one
    proposal is in ``ACTIVATING`` state does this adapter substitute the validated #439 graph.
    """

    descriptor = ProviderDescriptor(
        provider_id="platform-planning-orchestrator",
        provider_type="orchestrator",
        supported_operations=("plan",),
        capabilities=(
            Capability(
                name="planning.proposal.activation",
                kind=CapabilityKind.ORCHESTRATION,
                supported_operations=("plan",),
            ),
        ),
        health=HealthStatus.HEALTHY,
        available=True,
    )

    def __init__(
        self,
        repository: PlanningRepository,
        *,
        fallback: Orchestrator | None = None,
    ) -> None:
        self._repository = repository
        self._fallback = fallback

    async def plan(self, request: PlanRequest) -> PlanResponse:
        record = self._repository.pending_activation(request.task_id)
        if record is None:
            if self._fallback is not None:
                return await self._fallback.plan(request)
            raise ContractError(
                ErrorCode.CONFLICT,
                "kernel planning was invoked without a validated activation proposal",
            )
        proposal = record.proposal
        reused_step_ids: list[JsonValue] = [
            step_id
            for step_id in sorted(
                {step_id for step in proposal.steps for step_id in step.reuse_step_ids}
            )
        ]
        return PlanResponse(
            summary=proposal.summary,
            steps=tuple(
                PlanStepProposal(
                    key=step.key,
                    title=step.title,
                    objective=step.objective,
                    depends_on=step.depends_on,
                    metadata=_step_metadata(step),
                )
                for step in proposal.steps
            ),
            adapter_metadata=(
                AdapterMetadata(
                    namespace="platform-planning",
                    values={
                        "proposal_id": proposal.proposal_id,
                        "proposal_digest": proposal.digest,
                        "plan_revision": proposal.plan_revision,
                        "base_plan_id": proposal.base_plan_id,
                        "trigger": proposal.trigger.value,
                        "reason": proposal.reason,
                        "planner_id": proposal.planner.planner_id,
                        "planner_kind": proposal.planner.kind.value,
                        "planner_version": proposal.planner.version,
                        "model_config_id": proposal.model_config_id,
                        "assumptions": list(proposal.assumptions),
                        "constraints": list(proposal.constraints),
                        "evidence_refs": list(proposal.evidence_refs),
                        "reused_step_ids": reused_step_ids,
                        "superseded_plan_id": proposal.base_plan_id,
                    },
                ),
            ),
        )


def draft_from_mapping(raw: dict[str, Any]) -> PlanDraft:
    summary = _required_string(raw, "summary")
    raw_steps = raw.get("steps")
    if not isinstance(raw_steps, list):
        raise ValueError("steps must be a list")
    steps = tuple(_step_from_mapping(item) for item in raw_steps)
    return PlanDraft(
        summary=summary,
        steps=steps,
        assumptions=_string_tuple(raw.get("assumptions", []), "assumptions"),
        constraints=_string_tuple(raw.get("constraints", []), "constraints"),
    )


def _step_from_mapping(raw_value: Any) -> PlanningStepDraft:
    if not isinstance(raw_value, dict):
        raise ValueError("each planning Step must be an object")
    raw: dict[str, Any] = raw_value
    assignment = _assignment_from_mapping(raw.get("assignment"))
    capability_requirements = _capabilities_from_mapping(raw.get("capability_requirements", []))
    model_raw = raw.get("model_requirements", {})
    if not isinstance(model_raw, dict):
        raise ValueError("model_requirements must be an object")
    metadata = raw.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    return PlanningStepDraft(
        key=_required_string(raw, "key"),
        title=_required_string(raw, "title"),
        objective=_optional_text(raw, "objective", ""),
        depends_on=_string_tuple(raw.get("depends_on", []), "depends_on"),
        assignment=assignment,
        capability_requirements=capability_requirements,
        model_requirements=RoutingRequirements(
            explicit_model_id=_optional_string(model_raw, "model_config_id"),
            min_context_window=_optional_int(model_raw, "min_context_window"),
            tool_calling=_bool(model_raw, "tool_calling"),
            structured_output=_bool(model_raw, "structured_output"),
            streaming=_bool(model_raw, "streaming"),
            modalities=_string_tuple(model_raw.get("modalities", []), "modalities"),
            reasoning=_string_tuple(model_raw.get("reasoning", []), "reasoning"),
            local_only=_bool(model_raw, "local_only"),
            self_hosted_only=_bool(model_raw, "self_hosted_only"),
        ),
        requires_model=_bool(raw, "requires_model"),
        workspace_id=_optional_string(raw, "workspace_id"),
        input_refs=_string_tuple(raw.get("input_refs", []), "input_refs"),
        output_refs=_string_tuple(raw.get("output_refs", []), "output_refs"),
        expected_evidence=_string_tuple(raw.get("expected_evidence", []), "expected_evidence"),
        verification_policy_refs=_string_tuple(
            raw.get("verification_policy_refs", []),
            "verification_policy_refs",
        ),
        reuse_step_ids=_string_tuple(raw.get("reuse_step_ids", []), "reuse_step_ids"),
        metadata=metadata,
    )


def _assignment_from_mapping(raw_value: Any) -> AgentAssignment | None:
    if raw_value is None:
        return None
    if not isinstance(raw_value, dict):
        raise ValueError("assignment must be an object")
    return AgentAssignment(
        agent_id=_optional_string(raw_value, "agent_id"),
        agent_revision=_optional_int(raw_value, "agent_revision"),
        team_id=_optional_string(raw_value, "team_id"),
        team_revision=_optional_int(raw_value, "team_revision"),
        role_requirement=_optional_string(raw_value, "role_requirement"),
        rationale=_optional_text(raw_value, "rationale", ""),
    )


def _capabilities_from_mapping(raw_value: Any) -> tuple[CapabilityRequirement, ...]:
    if not isinstance(raw_value, list):
        raise ValueError("capability_requirements must be a list")
    parsed: list[CapabilityRequirement] = []
    for item in raw_value:
        if not isinstance(item, dict):
            raise ValueError("capability requirement must be an object")
        parsed.append(
            CapabilityRequirement(
                capability_id=_required_string(item, "capability_id"),
                exact_version=_optional_string(item, "exact_version"),
                required_features=_string_tuple(
                    item.get("required_features", []),
                    "required_features",
                ),
                required=_bool(item, "required", True),
            )
        )
    return tuple(parsed)


def _step_metadata(step: PlanningStepDraft) -> dict[str, JsonValue]:
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
        "model_requirements": _routing_requirements_payload(step.model_requirements),
        "requires_model": step.requires_model,
        "workspace_id": step.workspace_id,
        "input_refs": list(step.input_refs),
        "output_refs": list(step.output_refs),
        "expected_evidence": list(step.expected_evidence),
        "verification_policy_refs": list(step.verification_policy_refs),
        "reuse_step_ids": list(step.reuse_step_ids),
        **dict(step.metadata),
    }


def _routing_requirements_payload(requirements: RoutingRequirements) -> dict[str, JsonValue]:
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


def _required_string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string")
    return value


def _optional_string(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string when supplied")
    return value


def _optional_text(raw: dict[str, Any], key: str, default: str) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _optional_int(raw: dict[str, Any], key: str) -> int | None:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _bool(raw: dict[str, Any], key: str, default: bool = False) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _string_tuple(raw_value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(raw_value, list):
        raise ValueError(f"{name} must be a list")
    if any(not isinstance(item, str) or not item.strip() for item in raw_value):
        raise ValueError(f"{name} must contain non-blank strings")
    return tuple(raw_value)
