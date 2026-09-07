"""Platform-owned autonomous planning contracts for issue #439.

The types in this module describe planning intent and proposal state only. They never
represent durable Step execution, Worker placement, model-provider identity or capability
invocation state. Those responsibilities remain in their existing platform subsystems.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import new_id, validate_id
from ai_multi_agent_platform.models import ModelLocation, RoutingRequirements


def _now() -> datetime:
    return datetime.now(UTC)


def _require(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")


def _freeze_mapping(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    return MappingProxyType(dict(value))


def _validate_optional_id(value: str | None, prefix: str) -> None:
    if value is not None:
        validate_id(value, prefix)


class PlanningTrigger(StrEnum):
    INITIAL = "initial"
    MANUAL = "manual"
    TERMINAL_FAILURE = "terminal_failure"
    RETRY_EXHAUSTED = "retry_exhausted"
    VERIFICATION_CHANGES_REQUIRED = "verification_changes_required"
    VERIFICATION_FAILED = "verification_failed"
    VERIFICATION_INCONCLUSIVE = "verification_inconclusive"
    AGENT_UNAVAILABLE = "agent_unavailable"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    MODEL_UNAVAILABLE = "model_unavailable"
    TASK_CONSTRAINT_CHANGED = "task_constraint_changed"
    ASSUMPTION_INVALIDATED = "assumption_invalidated"
    FEASIBILITY_BLOCKER = "feasibility_blocker"


class ProposalStatus(StrEnum):
    VALIDATED = "validated"
    INVALID = "invalid"
    AWAITING_APPROVAL = "awaiting_approval"
    ACTIVATING = "activating"
    ACTIVATED = "activated"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class PlannerKind(StrEnum):
    DETERMINISTIC = "deterministic"
    MODEL = "model"
    ORCHESTRATOR = "orchestrator"


@dataclass(frozen=True, slots=True)
class PlannerDescriptor:
    planner_id: str
    kind: PlannerKind
    version: str = "1"

    def __post_init__(self) -> None:
        _require(self.planner_id, "planner_id")
        _require(self.version, "planner version")


@dataclass(frozen=True, slots=True)
class PlanningAgentCandidate:
    agent_id: str
    revision: int
    role: str
    enabled: bool = True
    project_id: str | None = None
    workspace_id: str | None = None
    allowed_capability_ids: tuple[str, ...] = ()
    denied_capability_ids: tuple[str, ...] = ()
    required_capability_ids: tuple[str, ...] = ()
    model_requirements: RoutingRequirements = field(default_factory=RoutingRequirements)

    def __post_init__(self) -> None:
        validate_id(self.agent_id, "agent")
        if self.revision < 1:
            raise ValueError("agent candidate revision must be >= 1")
        _require(self.role, "agent candidate role")
        _validate_optional_id(self.project_id, "project")
        _validate_optional_id(self.workspace_id, "workspace")


@dataclass(frozen=True, slots=True)
class PlanningTeamCandidate:
    team_id: str
    revision: int
    enabled: bool
    member_agent_ids: tuple[str, ...]
    project_id: str | None = None
    workspace_id: str | None = None
    shared_capability_ids: tuple[str, ...] = ()
    max_parallel_agents: int | None = None
    max_steps: int | None = None

    def __post_init__(self) -> None:
        validate_id(self.team_id, "team")
        if self.revision < 1:
            raise ValueError("team candidate revision must be >= 1")
        for agent_id in self.member_agent_ids:
            validate_id(agent_id, "agent")
        _validate_optional_id(self.project_id, "project")
        _validate_optional_id(self.workspace_id, "workspace")
        if self.max_parallel_agents is not None and self.max_parallel_agents < 1:
            raise ValueError("max_parallel_agents must be >= 1")
        if self.max_steps is not None and self.max_steps < 1:
            raise ValueError("max_steps must be >= 1")


@dataclass(frozen=True, slots=True)
class PlanningCapabilityCandidate:
    capability_id: str
    version: str
    available: bool
    features: tuple[str, ...] = ()
    required_permissions: tuple[str, ...] = ()
    required_approvals: tuple[str, ...] = ()
    safety: str = "standard"
    side_effects: str = "none"

    def __post_init__(self) -> None:
        _require(self.capability_id, "capability_id")
        _require(self.version, "capability version")


@dataclass(frozen=True, slots=True)
class PlanningModelCandidate:
    """Sanitized canonical model metadata; provider-native identity is deliberately absent."""

    model_config_id: str
    enabled: bool
    available: bool
    location: ModelLocation
    context_window: int | None = None
    tool_calling: bool = False
    structured_output: bool = False
    streaming: bool = False
    modalities: tuple[str, ...] = ("text",)
    reasoning: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require(self.model_config_id, "model_config_id")
        if self.context_window is not None and self.context_window <= 0:
            raise ValueError("context_window must be greater than zero")


@dataclass(frozen=True, slots=True)
class PlanningInventory:
    agents: tuple[PlanningAgentCandidate, ...] = ()
    teams: tuple[PlanningTeamCandidate, ...] = ()
    capabilities: tuple[PlanningCapabilityCandidate, ...] = ()
    models: tuple[PlanningModelCandidate, ...] = ()


@dataclass(frozen=True, slots=True)
class PriorPlanSnapshot:
    plan_id: str
    revision: int
    completed_step_ids: tuple[str, ...] = ()
    running_step_ids: tuple[str, ...] = ()
    failed_step_ids: tuple[str, ...] = ()
    not_started_step_ids: tuple[str, ...] = ()
    result_refs: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.plan_id, "plan")
        if self.revision < 1:
            raise ValueError("prior Plan revision must be >= 1")
        for step_id in (
            *self.completed_step_ids,
            *self.running_step_ids,
            *self.failed_step_ids,
            *self.not_started_step_ids,
        ):
            validate_id(step_id, "step")


@dataclass(frozen=True, slots=True)
class AgentAssignment:
    agent_id: str | None = None
    agent_revision: int | None = None
    team_id: str | None = None
    team_revision: int | None = None
    role_requirement: str | None = None
    rationale: str = ""

    def __post_init__(self) -> None:
        agent_selected = self.agent_id is not None or self.agent_revision is not None
        team_selected = self.team_id is not None or self.team_revision is not None
        if agent_selected and team_selected:
            raise ValueError("a Step cannot assign both an Agent and Agent Team")
        if self.agent_id is not None:
            validate_id(self.agent_id, "agent")
            if self.agent_revision is None or self.agent_revision < 1:
                raise ValueError("agent assignment requires revision >= 1")
        elif self.agent_revision is not None:
            raise ValueError("agent_revision requires agent_id")
        if self.team_id is not None:
            validate_id(self.team_id, "team")
            if self.team_revision is None or self.team_revision < 1:
                raise ValueError("team assignment requires revision >= 1")
        elif self.team_revision is not None:
            raise ValueError("team_revision requires team_id")
        if not agent_selected and not team_selected:
            if self.role_requirement is None or not self.role_requirement.strip():
                raise ValueError("assignment requires an exact Agent/Team or role requirement")
        if self.role_requirement is not None:
            _require(self.role_requirement, "role_requirement")


@dataclass(frozen=True, slots=True)
class CapabilityRequirement:
    capability_id: str
    exact_version: str | None = None
    required_features: tuple[str, ...] = ()
    required: bool = True

    def __post_init__(self) -> None:
        _require(self.capability_id, "capability_id")
        if self.exact_version is not None:
            _require(self.exact_version, "exact_version")
        if any(not feature.strip() for feature in self.required_features):
            raise ValueError("required_features must not contain blank values")


@dataclass(frozen=True, slots=True)
class PlanningStepDraft:
    key: str
    title: str
    objective: str = ""
    depends_on: tuple[str, ...] = ()
    assignment: AgentAssignment | None = None
    capability_requirements: tuple[CapabilityRequirement, ...] = ()
    model_requirements: RoutingRequirements = field(default_factory=RoutingRequirements)
    requires_model: bool = False
    workspace_id: str | None = None
    input_refs: tuple[str, ...] = ()
    output_refs: tuple[str, ...] = ()
    expected_evidence: tuple[str, ...] = ()
    verification_policy_refs: tuple[str, ...] = ()
    reuse_step_ids: tuple[str, ...] = ()
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require(self.key, "planning step key")
        _require(self.title, "planning step title")
        if self.key in self.depends_on:
            raise ValueError("planning Step cannot depend on itself")
        _validate_optional_id(self.workspace_id, "workspace")
        for step_id in self.reuse_step_ids:
            validate_id(step_id, "step")
        for values, name in (
            (self.input_refs, "input refs"),
            (self.output_refs, "output refs"),
            (self.expected_evidence, "expected evidence"),
            (self.verification_policy_refs, "verification policy refs"),
        ):
            if any(not value.strip() for value in values):
                raise ValueError(f"{name} must not contain blank values")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class PlanDraft:
    summary: str
    steps: tuple[PlanningStepDraft, ...]
    assumptions: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require(self.summary, "plan summary")
        keys = [step.key for step in self.steps]
        if len(keys) != len(set(keys)):
            raise ValueError("planning step keys must be unique")


@dataclass(frozen=True, slots=True)
class PlanningRequest:
    task_id: str
    task_revision: int
    objective: str
    context: OperationContext
    inventory: PlanningInventory
    trigger: PlanningTrigger = PlanningTrigger.INITIAL
    reason: str | None = None
    workspace_id: str | None = None
    prior_plan: PriorPlanSnapshot | None = None
    evidence_refs: tuple[str, ...] = ()
    task_constraints: tuple[str, ...] = ()
    granted_permissions: frozenset[str] = frozenset()
    available_worker_capabilities: frozenset[str] = frozenset()
    max_steps: int = 128
    max_parallel_steps: int | None = None

    def __post_init__(self) -> None:
        validate_id(self.task_id, "task")
        if self.task_revision < 1:
            raise ValueError("task_revision must be >= 1")
        _require(self.objective, "planning objective")
        if self.reason is not None:
            _require(self.reason, "planning reason")
        _validate_optional_id(self.workspace_id, "workspace")
        if self.max_steps < 1:
            raise ValueError("max_steps must be >= 1")
        if self.max_parallel_steps is not None and self.max_parallel_steps < 1:
            raise ValueError("max_parallel_steps must be >= 1")


@dataclass(frozen=True, slots=True)
class PlannerOutput:
    draft: PlanDraft
    planner: PlannerDescriptor
    model_config_id: str | None = None

    def __post_init__(self) -> None:
        if self.model_config_id is not None:
            _require(self.model_config_id, "model_config_id")


@dataclass(frozen=True, slots=True)
class PlanProposal:
    proposal_id: str
    task_id: str
    task_revision: int
    plan_revision: int
    trigger: PlanningTrigger
    summary: str
    steps: tuple[PlanningStepDraft, ...]
    planner: PlannerDescriptor
    base_plan_id: str | None = None
    reason: str | None = None
    assumptions: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    model_config_id: str | None = None
    supersedes_proposal_id: str | None = None
    created_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        validate_id(self.proposal_id, "plan_proposal")
        validate_id(self.task_id, "task")
        if self.task_revision < 1 or self.plan_revision < 1:
            raise ValueError("task/Plan revisions must be >= 1")
        _validate_optional_id(self.base_plan_id, "plan")
        if self.supersedes_proposal_id is not None:
            validate_id(self.supersedes_proposal_id, "plan_proposal")
        _require(self.summary, "proposal summary")
        if self.reason is not None:
            _require(self.reason, "proposal reason")
        if self.model_config_id is not None:
            _require(self.model_config_id, "model_config_id")
        keys = [step.key for step in self.steps]
        if len(keys) != len(set(keys)):
            raise ValueError("proposal Step keys must be unique")

    @property
    def digest(self) -> str:
        payload = {
            "proposal_id": self.proposal_id,
            "task_id": self.task_id,
            "task_revision": self.task_revision,
            "plan_revision": self.plan_revision,
            "base_plan_id": self.base_plan_id,
            "trigger": self.trigger.value,
            "reason": self.reason,
            "summary": self.summary,
            "steps": [_step_digest_payload(step) for step in self.steps],
            "assumptions": list(self.assumptions),
            "constraints": list(self.constraints),
            "evidence_refs": list(self.evidence_refs),
            "planner_id": self.planner.planner_id,
            "planner_version": self.planner.version,
            "model_config_id": self.model_config_id,
            "supersedes_proposal_id": self.supersedes_proposal_id,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ProposalValidation:
    valid: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    approval_required: bool = False

    def __post_init__(self) -> None:
        if self.valid and self.errors:
            raise ValueError("valid proposal cannot contain validation errors")
        if not self.valid and not self.errors:
            raise ValueError("invalid proposal must contain validation errors")


@dataclass(frozen=True, slots=True)
class ProposalRecord:
    proposal: PlanProposal
    status: ProposalStatus
    idempotency_key: str
    validation: ProposalValidation
    trigger_fingerprint: str
    revision: int = 1
    activation_plan_id: str | None = None
    approval_id: str | None = None
    failure_reason: str | None = None
    updated_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        _require(self.idempotency_key, "planning idempotency key")
        _require(self.trigger_fingerprint, "trigger_fingerprint")
        if self.revision < 1:
            raise ValueError("proposal record revision must be >= 1")
        _validate_optional_id(self.activation_plan_id, "plan")
        if self.approval_id is not None:
            _require(self.approval_id, "approval_id")
        if self.failure_reason is not None:
            _require(self.failure_reason, "failure_reason")


@dataclass(frozen=True, slots=True)
class ReplanPolicy:
    max_replans: int = 3

    def __post_init__(self) -> None:
        if self.max_replans < 0:
            raise ValueError("max_replans must be >= 0")


def new_plan_proposal_id() -> str:
    return new_id("plan_proposal")


def _routing_payload(requirements: RoutingRequirements) -> dict[str, JsonValue]:
    return {
        "explicit_model_id": requirements.explicit_model_id,
        "min_context_window": requirements.min_context_window,
        "tool_calling": requirements.tool_calling,
        "structured_output": requirements.structured_output,
        "streaming": requirements.streaming,
        "modalities": list(requirements.modalities),
        "reasoning": list(requirements.reasoning),
        "local_only": requirements.local_only,
        "self_hosted_only": requirements.self_hosted_only,
    }


def _step_digest_payload(step: PlanningStepDraft) -> dict[str, JsonValue]:
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
        "model_requirements": _routing_payload(step.model_requirements),
        "requires_model": step.requires_model,
        "workspace_id": step.workspace_id,
        "input_refs": list(step.input_refs),
        "output_refs": list(step.output_refs),
        "expected_evidence": list(step.expected_evidence),
        "verification_policy_refs": list(step.verification_policy_refs),
        "reuse_step_ids": list(step.reuse_step_ids),
        "metadata": dict(step.metadata),
    }
