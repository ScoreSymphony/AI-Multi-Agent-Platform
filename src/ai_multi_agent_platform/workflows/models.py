"""Provider-neutral reusable workflow definitions for issue #364.

These models describe durable reusable intent. They are deliberately distinct from the
canonical task-bound ``Plan``/``Step`` lifecycle in :mod:`ai_multi_agent_platform.domain`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType

from ai_multi_agent_platform.agents import AgentRevisionRef, AgentTeamRevisionRef
from ai_multi_agent_platform.contracts.types import FrozenJsonValue
from ai_multi_agent_platform.domain import OwnerRef, new_id, validate_id

WORKFLOW_SCHEMA_VERSION = "1"


def utc_now() -> datetime:
    return datetime.now(UTC)


def _require_nonblank(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")


def _unique_nonblank(values: tuple[str, ...], name: str) -> None:
    for value in values:
        _require_nonblank(value, name)
    if len(values) != len(set(values)):
        raise ValueError(f"{name} values must be unique")


def _freeze_value(value: FrozenJsonValue) -> FrozenJsonValue:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})
    if isinstance(value, tuple):
        return tuple(_freeze_value(item) for item in value)
    return value


def _freeze_mapping(value: Mapping[str, FrozenJsonValue]) -> Mapping[str, FrozenJsonValue]:
    return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})


@dataclass(frozen=True, slots=True)
class WorkflowRevisionRef:
    workflow_id: str
    revision: int

    def __post_init__(self) -> None:
        validate_id(self.workflow_id, "workflow")
        if self.revision < 1:
            raise ValueError("workflow revision must be >= 1")


@dataclass(frozen=True, slots=True)
class WorkflowParameter:
    name: str
    required: bool = True
    secret_reference: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        _require_nonblank(self.name, "workflow parameter name")


@dataclass(frozen=True, slots=True)
class WorkflowCapabilityRequirement:
    capability_id: str
    optional: bool = False
    version_constraint: str | None = None

    def __post_init__(self) -> None:
        _require_nonblank(self.capability_id, "capability_id")
        if self.version_constraint is not None:
            _require_nonblank(self.version_constraint, "capability version constraint")


@dataclass(frozen=True, slots=True)
class WorkflowStage:
    """Reusable stage intent; ``stage_id`` is local to one workflow revision."""

    stage_id: str
    title: str
    description: str = ""
    depends_on: tuple[str, ...] = ()
    parameter_refs: tuple[str, ...] = ()
    capabilities: tuple[WorkflowCapabilityRequirement, ...] = ()
    tool_ids: tuple[str, ...] = ()
    agent: AgentRevisionRef | None = None
    team: AgentTeamRevisionRef | None = None
    model_routing_policy_ref: str | None = None
    permission_actions: tuple[str, ...] = ()
    metadata: Mapping[str, FrozenJsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonblank(self.stage_id, "workflow stage_id")
        _require_nonblank(self.title, "workflow stage title")
        _unique_nonblank(self.depends_on, "workflow dependency")
        _unique_nonblank(self.parameter_refs, "workflow parameter reference")
        _unique_nonblank(self.tool_ids, "workflow tool ID")
        for tool_id in self.tool_ids:
            validate_id(tool_id, "tool")
        _unique_nonblank(self.permission_actions, "workflow permission action")
        if self.stage_id in self.depends_on:
            raise ValueError("workflow stage cannot depend on itself")
        if self.agent is not None and self.team is not None:
            raise ValueError("workflow stage may reference an Agent or Agent Team, not both")
        capability_ids = tuple(item.capability_id for item in self.capabilities)
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("workflow capability requirements must use unique capability IDs")
        if self.model_routing_policy_ref is not None:
            _require_nonblank(self.model_routing_policy_ref, "model routing policy reference")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class WorkflowCompatibility:
    schema_version: str = WORKFLOW_SCHEMA_VERSION
    platform_version_range: str | None = None
    contract_versions: Mapping[str, str] = field(default_factory=dict)
    provider_agnostic: bool = True
    orchestrator_agnostic: bool = True
    metadata: Mapping[str, FrozenJsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonblank(self.schema_version, "workflow schema version")
        if self.platform_version_range is not None:
            _require_nonblank(self.platform_version_range, "platform version range")
        if not self.provider_agnostic:
            raise ValueError("canonical workflow compatibility must remain provider agnostic")
        if not self.orchestrator_agnostic:
            raise ValueError("canonical workflow compatibility must remain orchestrator agnostic")
        versions = dict(self.contract_versions)
        for key, value in versions.items():
            _require_nonblank(key, "workflow contract name")
            _require_nonblank(value, "workflow contract version")
        object.__setattr__(self, "contract_versions", MappingProxyType(versions))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class WorkflowProvenance:
    creator: str
    source: str
    metadata: Mapping[str, FrozenJsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonblank(self.creator, "workflow creator")
        _require_nonblank(self.source, "workflow source")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class WorkflowContent:
    name: str
    description: str
    stages: tuple[WorkflowStage, ...]
    parameters: tuple[WorkflowParameter, ...] = ()
    provenance: WorkflowProvenance = field(
        default_factory=lambda: WorkflowProvenance(creator="local", source="local")
    )
    compatibility: WorkflowCompatibility = field(default_factory=WorkflowCompatibility)
    metadata: Mapping[str, FrozenJsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonblank(self.name, "workflow name")
        if not self.stages:
            raise ValueError("workflow must contain at least one stage")
        stage_ids = tuple(stage.stage_id for stage in self.stages)
        if len(stage_ids) != len(set(stage_ids)):
            raise ValueError("workflow stage IDs must be unique")
        parameter_names = tuple(parameter.name for parameter in self.parameters)
        if len(parameter_names) != len(set(parameter_names)):
            raise ValueError("workflow parameter names must be unique")
        self._validate_stage_graph(set(stage_ids), set(parameter_names))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    def _validate_stage_graph(self, stage_ids: set[str], parameter_names: set[str]) -> None:
        graph = {stage.stage_id: stage.depends_on for stage in self.stages}
        for stage in self.stages:
            missing = set(stage.depends_on) - stage_ids
            if missing:
                raise ValueError(
                    "workflow stage dependencies reference unknown stages: "
                    + ", ".join(sorted(missing))
                )
            missing_parameters = set(stage.parameter_refs) - parameter_names
            if missing_parameters:
                raise ValueError(
                    "workflow stage references undeclared parameters: "
                    + ", ".join(sorted(missing_parameters))
                )

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(stage_id: str) -> None:
            if stage_id in visiting:
                raise ValueError("workflow stage dependency graph must be acyclic")
            if stage_id in visited:
                return
            visiting.add(stage_id)
            for dependency in graph[stage_id]:
                visit(dependency)
            visiting.remove(stage_id)
            visited.add(stage_id)

        for stage_id in stage_ids:
            visit(stage_id)


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    workflow_id: str
    owner_ref: OwnerRef
    current_revision: int
    project_id: str | None = None
    organization_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_id(self.workflow_id, "workflow")
        if self.current_revision < 1:
            raise ValueError("current_revision must be >= 1")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if self.organization_id is not None:
            _require_nonblank(self.organization_id, "organization_id")


@dataclass(frozen=True, slots=True)
class WorkflowRevision:
    workflow_id: str
    revision: int
    owner_ref: OwnerRef
    content: WorkflowContent
    project_id: str | None = None
    organization_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_id(self.workflow_id, "workflow")
        if self.revision < 1:
            raise ValueError("workflow revision must be >= 1")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if self.organization_id is not None:
            _require_nonblank(self.organization_id, "organization_id")

    @property
    def ref(self) -> WorkflowRevisionRef:
        return WorkflowRevisionRef(self.workflow_id, self.revision)


def new_workflow_id() -> str:
    return new_id("workflow")
