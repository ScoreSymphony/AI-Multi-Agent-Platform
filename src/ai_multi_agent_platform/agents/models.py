"""Canonical Agent, Agent Team and runtime models for issue #33.

These types are platform-owned. Orchestrator/provider-private session schemas must
map from these snapshots and must never become the canonical Agent definition.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.data.models import MemoryScope
from ai_multi_agent_platform.domain import OwnerRef, Provenance, new_id, validate_id
from ai_multi_agent_platform.models.types import RoutingRequirements


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_nonblank(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")


def _freeze_mapping(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    return MappingProxyType(dict(value))


def _freeze_string_mapping(value: Mapping[str, str], name: str) -> Mapping[str, str]:
    frozen = dict(value)
    for key, item in frozen.items():
        _require_nonblank(key, f"{name} key")
        _require_nonblank(item, f"{name} value")
    return MappingProxyType(frozen)


def _validate_optional_canonical_id(value: str | None, prefix: str) -> None:
    if value is not None:
        validate_id(value, prefix)


class ModelFallbackPolicy(StrEnum):
    FAIL = "fail"
    ROUTE = "route"


class UnavailableMemberPolicy(StrEnum):
    FAIL = "fail"
    SKIP_OPTIONAL = "skip_optional"


class AgentRunStatus(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class InstructionSource:
    """Versionable instruction content or a provider-neutral content reference."""

    content: str | None = None
    ref: str | None = None
    version: str | None = None

    def __post_init__(self) -> None:
        if (self.content is None) == (self.ref is None):
            raise ValueError("instruction source requires exactly one of content or ref")
        if self.content is not None:
            _require_nonblank(self.content, "instruction content")
        if self.ref is not None:
            _require_nonblank(self.ref, "instruction ref")
        if self.version is not None:
            _require_nonblank(self.version, "instruction version")


@dataclass(frozen=True, slots=True)
class AgentInstructions:
    """Prompt layers owned by the Agent profile, not an orchestrator session format."""

    role: InstructionSource
    platform_constraint_refs: tuple[str, ...] = ()
    project_instruction_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value in (*self.platform_constraint_refs, *self.project_instruction_refs):
            _require_nonblank(value, "instruction reference")
        if len(set(self.platform_constraint_refs)) != len(self.platform_constraint_refs):
            raise ValueError("platform constraint references must be unique")
        if len(set(self.project_instruction_refs)) != len(self.project_instruction_refs):
            raise ValueError("project instruction references must be unique")


@dataclass(frozen=True, slots=True)
class AgentModelPolicy:
    """Canonical model requirements/preferences consumed by the model router."""

    requirements: RoutingRequirements = field(default_factory=RoutingRequirements)
    routing_profile_ref: str | None = None
    allow_task_override: bool = False
    fallback: ModelFallbackPolicy = ModelFallbackPolicy.FAIL

    def __post_init__(self) -> None:
        if self.routing_profile_ref is not None:
            _require_nonblank(self.routing_profile_ref, "routing_profile_ref")


@dataclass(frozen=True, slots=True)
class CapabilityConstraint:
    """One canonical capability requirement with optional version compatibility."""

    capability_id: str
    required: bool = True
    exact_version: str | None = None
    minimum_version: str | None = None
    maximum_version: str | None = None
    required_features: tuple[str, ...] = ()
    approval_ref: str | None = None

    def __post_init__(self) -> None:
        _require_nonblank(self.capability_id, "capability_id")
        if self.exact_version is not None:
            _require_nonblank(self.exact_version, "exact_version")
        if self.minimum_version is not None:
            _require_nonblank(self.minimum_version, "minimum_version")
        if self.maximum_version is not None:
            _require_nonblank(self.maximum_version, "maximum_version")
        if self.exact_version is not None and (
            self.minimum_version is not None
            or self.maximum_version is not None
            or self.required_features
        ):
            raise ValueError("exact capability version cannot be combined with compatibility rules")
        for feature in self.required_features:
            _require_nonblank(feature, "required capability feature")
        if len(set(self.required_features)) != len(self.required_features):
            raise ValueError("required capability features must be unique")
        if self.approval_ref is not None:
            _require_nonblank(self.approval_ref, "approval_ref")


@dataclass(frozen=True, slots=True)
class AgentCapabilityPolicy:
    allowed: tuple[str, ...] = ()
    denied: tuple[str, ...] = ()
    constraints: tuple[CapabilityConstraint, ...] = ()

    def __post_init__(self) -> None:
        for capability_id in (*self.allowed, *self.denied):
            _require_nonblank(capability_id, "capability_id")
        if len(set(self.allowed)) != len(self.allowed):
            raise ValueError("allowed capabilities must be unique")
        if len(set(self.denied)) != len(self.denied):
            raise ValueError("denied capabilities must be unique")
        overlap = set(self.allowed).intersection(self.denied)
        if overlap:
            raise ValueError(f"capabilities cannot be both allowed and denied: {sorted(overlap)!r}")
        constraint_ids = [item.capability_id for item in self.constraints]
        if len(set(constraint_ids)) != len(constraint_ids):
            raise ValueError("capability constraints must use unique capability IDs")
        denied_required = {
            item.capability_id for item in self.constraints if item.required
        }.intersection(self.denied)
        if denied_required:
            raise ValueError(f"required capabilities cannot be denied: {sorted(denied_required)!r}")
        if self.allowed:
            outside_allowlist = set(constraint_ids) - set(self.allowed)
            if outside_allowlist:
                raise ValueError(
                    "capability constraints must be included in the allowlist when one is set"
                )

    @property
    def required_ids(self) -> tuple[str, ...]:
        return tuple(item.capability_id for item in self.constraints if item.required)


@dataclass(frozen=True, slots=True)
class AgentDataAccess:
    """Backend-neutral memory/knowledge scope assignment for one Agent revision."""

    memory_scopes: tuple[MemoryScope, ...] = ()
    memory_config_refs: tuple[str, ...] = ()
    knowledge_source_ids: tuple[str, ...] = ()
    allow_user_memory: bool = False

    def __post_init__(self) -> None:
        if len(set(self.memory_scopes)) != len(self.memory_scopes):
            raise ValueError("memory scopes must be unique")
        if MemoryScope.USER in self.memory_scopes and not self.allow_user_memory:
            raise ValueError("user memory scope requires allow_user_memory=True")
        for ref in self.memory_config_refs:
            _require_nonblank(ref, "memory configuration reference")
        if len(set(self.memory_config_refs)) != len(self.memory_config_refs):
            raise ValueError("memory configuration references must be unique")
        for source_id in self.knowledge_source_ids:
            validate_id(source_id, "knowledge_source")
        if len(set(self.knowledge_source_ids)) != len(self.knowledge_source_ids):
            raise ValueError("knowledge source references must be unique")


@dataclass(frozen=True, slots=True)
class AgentWorkspaceDefaults:
    project_id: str | None = None
    workspace_id: str | None = None

    def __post_init__(self) -> None:
        _validate_optional_canonical_id(self.project_id, "project")
        _validate_optional_canonical_id(self.workspace_id, "workspace")


@dataclass(frozen=True, slots=True)
class AgentPolicyHooks:
    authorization_profile_ref: str | None = None
    verification_policy_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.authorization_profile_ref is not None:
            _require_nonblank(self.authorization_profile_ref, "authorization profile reference")
        for ref in self.verification_policy_refs:
            _require_nonblank(ref, "verification policy reference")
        if len(set(self.verification_policy_refs)) != len(self.verification_policy_refs):
            raise ValueError("verification policy references must be unique")


@dataclass(frozen=True, slots=True)
class AgentProfile:
    name: str
    role: str
    instructions: AgentInstructions
    description: str = ""
    model: AgentModelPolicy = field(default_factory=AgentModelPolicy)
    capabilities: AgentCapabilityPolicy = field(default_factory=AgentCapabilityPolicy)
    data_access: AgentDataAccess = field(default_factory=AgentDataAccess)
    workspace_defaults: AgentWorkspaceDefaults = field(default_factory=AgentWorkspaceDefaults)
    policy_hooks: AgentPolicyHooks = field(default_factory=AgentPolicyHooks)
    resource_hints: Mapping[str, JsonValue] = field(default_factory=dict)
    enabled: bool = True
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonblank(self.name, "agent name")
        _require_nonblank(self.role, "agent role")
        object.__setattr__(self, "resource_hints", _freeze_mapping(self.resource_hints))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    """Stable Agent identity pointing at the latest immutable configuration revision."""

    agent_id: str
    owner_ref: OwnerRef
    current_revision: int
    project_id: str | None = None
    workspace_id: str | None = None
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        validate_id(self.agent_id, "agent")
        if self.current_revision < 1:
            raise ValueError("agent current_revision must be >= 1")
        _validate_optional_canonical_id(self.project_id, "project")
        _validate_optional_canonical_id(self.workspace_id, "workspace")
        if self.updated_at < self.created_at:
            raise ValueError("agent updated_at cannot precede created_at")


@dataclass(frozen=True, slots=True)
class AgentRevision:
    agent_id: str
    revision: int
    profile: AgentProfile
    owner_ref: OwnerRef
    project_id: str | None = None
    workspace_id: str | None = None
    created_at: datetime = field(default_factory=_utc_now)
    provenance: Provenance | None = None

    def __post_init__(self) -> None:
        validate_id(self.agent_id, "agent")
        if self.revision < 1:
            raise ValueError("agent revision must be >= 1")
        _validate_optional_canonical_id(self.project_id, "project")
        _validate_optional_canonical_id(self.workspace_id, "workspace")


@dataclass(frozen=True, slots=True)
class AgentRevisionRef:
    agent_id: str
    revision: int

    def __post_init__(self) -> None:
        validate_id(self.agent_id, "agent")
        if self.revision < 1:
            raise ValueError("agent revision reference must be >= 1")


@dataclass(frozen=True, slots=True)
class AgentTeamMember:
    agent: AgentRevisionRef
    role: str
    required: bool = True
    can_delegate_to: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_nonblank(self.role, "team member role")
        for agent_id in self.can_delegate_to:
            validate_id(agent_id, "agent")
        if len(set(self.can_delegate_to)) != len(self.can_delegate_to):
            raise ValueError("delegation targets must be unique")


@dataclass(frozen=True, slots=True)
class AgentTeamProfile:
    name: str
    members: tuple[AgentTeamMember, ...]
    description: str = ""
    coordination_policy_ref: str | None = None
    leader_agent_id: str | None = None
    shared_capability_ids: tuple[str, ...] = ()
    shared_resource_refs: tuple[str, ...] = ()
    max_parallel_agents: int | None = None
    max_steps: int | None = None
    unavailable_member_policy: UnavailableMemberPolicy = UnavailableMemberPolicy.FAIL
    enabled: bool = True
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonblank(self.name, "agent team name")
        if not self.members:
            raise ValueError("agent team requires at least one member")
        member_ids = [member.agent.agent_id for member in self.members]
        if len(set(member_ids)) != len(member_ids):
            raise ValueError("agent team members must have unique agent IDs")
        if self.coordination_policy_ref is not None:
            _require_nonblank(self.coordination_policy_ref, "coordination policy reference")
        if self.leader_agent_id is not None:
            validate_id(self.leader_agent_id, "agent")
            if self.leader_agent_id not in member_ids:
                raise ValueError("team leader must be a configured member")
        for capability_id in self.shared_capability_ids:
            _require_nonblank(capability_id, "shared capability ID")
        if len(set(self.shared_capability_ids)) != len(self.shared_capability_ids):
            raise ValueError("shared capabilities must be unique")
        for resource_ref in self.shared_resource_refs:
            _require_nonblank(resource_ref, "shared resource reference")
        if len(set(self.shared_resource_refs)) != len(self.shared_resource_refs):
            raise ValueError("shared resource references must be unique")
        if self.max_parallel_agents is not None and self.max_parallel_agents < 1:
            raise ValueError("max_parallel_agents must be >= 1")
        if self.max_steps is not None and self.max_steps < 1:
            raise ValueError("max_steps must be >= 1")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class AgentTeamDefinition:
    team_id: str
    owner_ref: OwnerRef
    current_revision: int
    project_id: str | None = None
    workspace_id: str | None = None
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        validate_id(self.team_id, "team")
        if self.current_revision < 1:
            raise ValueError("team current_revision must be >= 1")
        _validate_optional_canonical_id(self.project_id, "project")
        _validate_optional_canonical_id(self.workspace_id, "workspace")
        if self.updated_at < self.created_at:
            raise ValueError("team updated_at cannot precede created_at")


@dataclass(frozen=True, slots=True)
class AgentTeamRevision:
    team_id: str
    revision: int
    profile: AgentTeamProfile
    owner_ref: OwnerRef
    project_id: str | None = None
    workspace_id: str | None = None
    created_at: datetime = field(default_factory=_utc_now)
    provenance: Provenance | None = None

    def __post_init__(self) -> None:
        validate_id(self.team_id, "team")
        if self.revision < 1:
            raise ValueError("team revision must be >= 1")
        _validate_optional_canonical_id(self.project_id, "project")
        _validate_optional_canonical_id(self.workspace_id, "workspace")


@dataclass(frozen=True, slots=True)
class AgentTeamRevisionRef:
    team_id: str
    revision: int

    def __post_init__(self) -> None:
        validate_id(self.team_id, "team")
        if self.revision < 1:
            raise ValueError("team revision reference must be >= 1")


@dataclass(frozen=True, slots=True)
class AgentRunRecord:
    """Runtime snapshot that pins the exact Agent revision used by one canonical Run."""

    agent_run_id: str
    run_id: str
    task_id: str
    agent: AgentRevisionRef
    status: AgentRunStatus
    team: AgentTeamRevisionRef | None = None
    selected_model_config_id: str | None = None
    selected_provider_id: str | None = None
    capability_ids: tuple[str, ...] = ()
    capability_versions: Mapping[str, str] = field(default_factory=dict)
    orchestrator_adapter_id: str | None = None
    orchestrator_runtime_ref: str | None = None
    artifact_ids: tuple[str, ...] = ()
    result_ids: tuple[str, ...] = ()
    model_call_refs: tuple[str, ...] = ()
    tool_invocation_refs: tuple[str, ...] = ()
    error: str | None = None
    telemetry: Mapping[str, JsonValue] = field(default_factory=dict)
    verification_context: Mapping[str, JsonValue] = field(default_factory=dict)
    started_at: datetime = field(default_factory=_utc_now)
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        validate_id(self.agent_run_id, "agent_run")
        validate_id(self.run_id, "run")
        validate_id(self.task_id, "task")
        if set(self.capability_versions) - set(self.capability_ids):
            raise ValueError("capability versions must refer to recorded capability IDs")
        for artifact_id in self.artifact_ids:
            validate_id(artifact_id, "artifact")
        for result_id in self.result_ids:
            validate_id(result_id, "result")
        for value, name in (
            (self.selected_model_config_id, "selected model configuration ID"),
            (self.selected_provider_id, "selected provider ID"),
            (self.orchestrator_adapter_id, "orchestrator adapter ID"),
            (self.orchestrator_runtime_ref, "orchestrator runtime reference"),
            (self.error, "agent run error"),
        ):
            if value is not None:
                _require_nonblank(value, name)
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("agent run finished_at cannot precede started_at")
        object.__setattr__(
            self,
            "capability_versions",
            _freeze_string_mapping(self.capability_versions, "capability version"),
        )
        object.__setattr__(self, "telemetry", _freeze_mapping(self.telemetry))
        object.__setattr__(self, "verification_context", _freeze_mapping(self.verification_context))


@dataclass(frozen=True, slots=True)
class AgentExecutionSpec:
    """Canonical input mapped into a private orchestrator runtime representation."""

    task_id: str
    run_id: str
    agent_revision: AgentRevision
    capability_ids: tuple[str, ...]
    capability_versions: Mapping[str, str] = field(default_factory=dict)
    selected_model_config_id: str | None = None
    selected_provider_id: str | None = None
    team_revision: AgentTeamRevision | None = None
    task_context: Mapping[str, JsonValue] = field(default_factory=dict)
    project_context: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.task_id, "task")
        validate_id(self.run_id, "run")
        if set(self.capability_versions) - set(self.capability_ids):
            raise ValueError("capability versions must refer to selected capability IDs")
        object.__setattr__(
            self,
            "capability_versions",
            _freeze_string_mapping(self.capability_versions, "capability version"),
        )
        object.__setattr__(self, "task_context", _freeze_mapping(self.task_context))
        object.__setattr__(self, "project_context", _freeze_mapping(self.project_context))


@dataclass(frozen=True, slots=True)
class OrchestratorMapping:
    adapter_id: str
    runtime_ref: str
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonblank(self.adapter_id, "orchestrator adapter ID")
        _require_nonblank(self.runtime_ref, "orchestrator runtime reference")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


def new_agent_id() -> str:
    return new_id("agent")


def new_team_id() -> str:
    return new_id("team")


def new_agent_run_id() -> str:
    return new_id("agent_run")
