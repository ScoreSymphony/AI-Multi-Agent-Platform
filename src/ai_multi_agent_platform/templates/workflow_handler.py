"""Template integration for canonical reusable Workflow definitions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from ai_multi_agent_platform.agents import AgentRevisionRef, AgentService, AgentTeamRevisionRef
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.contracts.types import FrozenJsonValue, JsonValue
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.workflows import (
    AuthorizedWorkflowService,
    WorkflowCallContext,
    WorkflowCapabilityRequirement,
    WorkflowCompatibility,
    WorkflowContent,
    WorkflowParameter,
    WorkflowProvenance,
    WorkflowStage,
)

from .application import ContextualTemplateHandlerRegistry, TemplateInstantiationContext
from .models import (
    TemplateInstantiationProvenance,
    TemplateResourceChange,
    TemplateResourceRef,
    TemplateRevision,
    TemplateType,
)


@dataclass(frozen=True, slots=True)
class _PortableStage:
    stage_id: str
    title: str
    description: str
    depends_on: tuple[str, ...]
    parameter_refs: tuple[str, ...]
    capabilities: tuple[WorkflowCapabilityRequirement, ...]
    tool_ids: tuple[str, ...]
    agent_template_id: str | None
    agent_template_revision: int | None
    team_template_id: str | None
    team_template_revision: int | None
    model_routing_policy_ref: str | None
    permission_actions: tuple[str, ...]
    metadata: Mapping[str, FrozenJsonValue]

    def preview_stage(self) -> WorkflowStage:
        return WorkflowStage(
            stage_id=self.stage_id,
            title=self.title,
            description=self.description,
            depends_on=self.depends_on,
            parameter_refs=self.parameter_refs,
            capabilities=self.capabilities,
            tool_ids=self.tool_ids,
            model_routing_policy_ref=self.model_routing_policy_ref,
            permission_actions=self.permission_actions,
            metadata=self.metadata,
        )

    def materialize(
        self,
        context: TemplateInstantiationContext,
        agents: AgentService | None,
    ) -> WorkflowStage:
        agent: AgentRevisionRef | None = None
        team: AgentTeamRevisionRef | None = None
        if self.agent_template_id is not None:
            if agents is None:
                raise ContractError(
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    "Workflow Template Agent dependency resolution is not composed",
                )
            resource = context.single_resource_for(
                self.agent_template_id,
                revision=self.agent_template_revision,
                resource_type="agent",
            )
            agent_revision = agents.get_agent_revision(resource.resource_id)
            agent = AgentRevisionRef(agent_revision.agent_id, agent_revision.revision)
        elif self.team_template_id is not None:
            if agents is None:
                raise ContractError(
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    "Workflow Template Agent Team dependency resolution is not composed",
                )
            resource = context.single_resource_for(
                self.team_template_id,
                revision=self.team_template_revision,
                resource_type="agent_team",
            )
            team_revision = agents.get_team_revision(resource.resource_id)
            team = AgentTeamRevisionRef(team_revision.team_id, team_revision.revision)
        return WorkflowStage(
            stage_id=self.stage_id,
            title=self.title,
            description=self.description,
            depends_on=self.depends_on,
            parameter_refs=self.parameter_refs,
            capabilities=self.capabilities,
            tool_ids=self.tool_ids,
            agent=agent,
            team=team,
            model_routing_policy_ref=self.model_routing_policy_ref,
            permission_actions=self.permission_actions,
            metadata=self.metadata,
        )


@dataclass(frozen=True, slots=True)
class _Configuration:
    stages: tuple[_PortableStage, ...]
    parameters: tuple[WorkflowParameter, ...]
    metadata: Mapping[str, FrozenJsonValue]


@dataclass(slots=True)
class WorkflowTemplateHandler:
    """Instantiate ``workflow_plan`` Templates through the canonical #364 domain."""

    service: AuthorizedWorkflowService
    agents: AgentService | None = None
    template_type = TemplateType.WORKFLOW_PLAN

    def preview(self, revision: TemplateRevision) -> tuple[TemplateResourceChange, ...]:
        config = _configuration(revision)
        _preview_content(revision, config)
        privileged = bool(revision.content.requirements.permission_actions) or any(
            item.privileged for item in revision.content.requirements.capabilities
        )
        return (
            TemplateResourceChange(
                resource_type="workflow",
                action="create",
                description=f"Create Workflow from {revision.template_id}@{revision.revision}",
                privileged=privileged,
            ),
        )

    async def instantiate(
        self,
        revision: TemplateRevision,
        provenance: TemplateInstantiationProvenance,
        context: TemplateInstantiationContext,
    ) -> tuple[TemplateResourceRef, ...]:
        config = _configuration(revision)
        created = await self.service.create(
            context=_call_context(revision, provenance, context),
            owner_ref=provenance.applied_by,
            content=WorkflowContent(
                name=revision.content.name,
                description=revision.content.description,
                stages=tuple(item.materialize(context, self.agents) for item in config.stages),
                parameters=config.parameters,
                provenance=_workflow_provenance(provenance, context),
                compatibility=_workflow_compatibility(revision),
                metadata=config.metadata,
            ),
            project_id=revision.project_id,
            organization_id=revision.organization_id,
        )
        return (TemplateResourceRef(resource_type="workflow", resource_id=created.workflow_id),)

    async def compensate(
        self,
        resources: tuple[TemplateResourceRef, ...],
        provenance: TemplateInstantiationProvenance,
        context: TemplateInstantiationContext,
    ) -> None:
        expected_source = f"template:{provenance.source.template_id}@{provenance.source.revision}"
        for resource in reversed(resources):
            if resource.resource_type != "workflow":
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "Workflow Template compensation received an unexpected resource",
                    details={
                        "resource_type": resource.resource_type,
                        "resource_id": resource.resource_id,
                    },
                )
            self.service.compensate_created(
                resource.resource_id,
                expected_owner_ref=provenance.applied_by,
                expected_source=expected_source,
                expected_instance_id=context.instance_id,
            )


def register_workflow_template_handler(
    registry: ContextualTemplateHandlerRegistry,
    service: AuthorizedWorkflowService,
    *,
    agents: AgentService | None = None,
) -> None:
    registry.register(WorkflowTemplateHandler(service, agents))


def _configuration(revision: TemplateRevision) -> _Configuration:
    payload = revision.content.configuration.payload
    if payload is None:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "canonical Workflow handler requires an inline Template payload",
        )
    try:
        data = _mapping(payload, "Workflow Template configuration")
        _reject_unknown(data, {"stages", "parameters", "metadata"}, "configuration")
        stages = tuple(_portable_stage(item) for item in _array(data, "stages", required=True))
        parameters = tuple(_parameter(item) for item in _array(data, "parameters"))
        metadata = _frozen_mapping(data.get("metadata", {}), "workflow metadata")
        config = _Configuration(stages=stages, parameters=parameters, metadata=metadata)
        _validate_declared_requirements(revision, config)
        _preview_content(revision, config)
        return config
    except ContractError:
        raise
    except (TypeError, ValueError) as exc:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"invalid Workflow Template configuration: {exc}",
        ) from exc


def _portable_stage(value: object) -> _PortableStage:
    data = _mapping(value, "workflow stage")
    _reject_unknown(
        data,
        {
            "stage_id",
            "title",
            "description",
            "depends_on",
            "parameter_refs",
            "capabilities",
            "tool_ids",
            "agent_template_id",
            "agent_template_revision",
            "team_template_id",
            "team_template_revision",
            "model_routing_policy_ref",
            "permission_actions",
            "metadata",
        },
        "workflow stage",
    )
    agent_template_id = _optional_string(data, "agent_template_id")
    team_template_id = _optional_string(data, "team_template_id")
    if agent_template_id is not None and team_template_id is not None:
        raise ValueError(
            "workflow stage may reference an Agent Template or Team Template, not both"
        )
    agent_template_revision = _optional_positive_int(data, "agent_template_revision")
    team_template_revision = _optional_positive_int(data, "team_template_revision")
    if agent_template_revision is not None and agent_template_id is None:
        raise ValueError("agent_template_revision requires agent_template_id")
    if team_template_revision is not None and team_template_id is None:
        raise ValueError("team_template_revision requires team_template_id")
    return _PortableStage(
        stage_id=_required_string(data, "stage_id"),
        title=_required_string(data, "title"),
        description=_optional_string(data, "description") or "",
        depends_on=_string_tuple(data, "depends_on"),
        parameter_refs=_string_tuple(data, "parameter_refs"),
        capabilities=tuple(_capability(item) for item in _array(data, "capabilities")),
        tool_ids=_string_tuple(data, "tool_ids"),
        agent_template_id=agent_template_id,
        agent_template_revision=agent_template_revision,
        team_template_id=team_template_id,
        team_template_revision=team_template_revision,
        model_routing_policy_ref=_optional_string(data, "model_routing_policy_ref"),
        permission_actions=_string_tuple(data, "permission_actions"),
        metadata=_frozen_mapping(data.get("metadata", {}), "workflow stage metadata"),
    )


def _parameter(value: object) -> WorkflowParameter:
    data = _mapping(value, "workflow parameter")
    _reject_unknown(data, {"name", "required", "secret_reference", "description"}, "parameter")
    return WorkflowParameter(
        name=_required_string(data, "name"),
        required=_optional_bool(data, "required", True),
        secret_reference=_optional_bool(data, "secret_reference", False),
        description=_optional_string(data, "description") or "",
    )


def _capability(value: object) -> WorkflowCapabilityRequirement:
    data = _mapping(value, "workflow capability requirement")
    _reject_unknown(data, {"capability_id", "optional", "version_constraint"}, "capability")
    return WorkflowCapabilityRequirement(
        capability_id=_required_string(data, "capability_id"),
        optional=_optional_bool(data, "optional", False),
        version_constraint=_optional_string(data, "version_constraint"),
    )


def _validate_declared_requirements(
    revision: TemplateRevision,
    config: _Configuration,
) -> None:
    dependencies = {item.template_id for item in revision.content.dependencies}
    referenced_templates = {
        template_id
        for stage in config.stages
        for template_id in (stage.agent_template_id, stage.team_template_id)
        if template_id is not None
    }
    undeclared_templates = sorted(referenced_templates - dependencies)
    if undeclared_templates:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "Workflow Template references Agent/Team Templates that are not dependencies",
            details={"template_ids": cast(JsonValue, undeclared_templates)},
        )

    declared_capabilities = {
        item.capability_id for item in revision.content.requirements.capabilities
    }
    used_capabilities = {
        requirement.capability_id for stage in config.stages for requirement in stage.capabilities
    }
    undeclared_capabilities = sorted(used_capabilities - declared_capabilities)
    if undeclared_capabilities:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "Workflow stages use capabilities not declared by Template requirements",
            details={"capability_ids": cast(JsonValue, undeclared_capabilities)},
        )

    used_models = {
        stage.model_routing_policy_ref
        for stage in config.stages
        if stage.model_routing_policy_ref is not None
    }
    undeclared_models = sorted(used_models - set(revision.content.requirements.model_policy_refs))
    if undeclared_models:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "Workflow stages use model policies not declared by Template requirements",
            details={"model_policy_refs": cast(JsonValue, undeclared_models)},
        )

    used_permissions = {action for stage in config.stages for action in stage.permission_actions}
    undeclared_permissions = sorted(
        used_permissions - set(revision.content.requirements.permission_actions)
    )
    if undeclared_permissions:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "Workflow stages use permissions not declared by Template requirements",
            details={"permission_actions": cast(JsonValue, undeclared_permissions)},
        )


def _preview_content(revision: TemplateRevision, config: _Configuration) -> WorkflowContent:
    try:
        return WorkflowContent(
            name=revision.content.name,
            description=revision.content.description,
            stages=tuple(item.preview_stage() for item in config.stages),
            parameters=config.parameters,
            provenance=WorkflowProvenance(creator="template-preview", source="template-preview"),
            compatibility=_workflow_compatibility(revision),
            metadata=config.metadata,
        )
    except (TypeError, ValueError) as exc:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"invalid canonical Workflow content: {exc}",
        ) from exc


def _workflow_compatibility(revision: TemplateRevision) -> WorkflowCompatibility:
    source = revision.content.compatibility
    return WorkflowCompatibility(
        platform_version_range=source.platform_version_range,
        contract_versions=source.contract_versions,
        provider_agnostic=source.provider_agnostic,
        orchestrator_agnostic=source.orchestrator_agnostic,
        metadata=source.metadata,
    )


def _workflow_provenance(
    provenance: TemplateInstantiationProvenance,
    context: TemplateInstantiationContext,
) -> WorkflowProvenance:
    owner = provenance.applied_by
    return WorkflowProvenance(
        creator=f"{owner.type}:{owner.id}",
        source=f"template:{provenance.source.template_id}@{provenance.source.revision}",
        metadata={"template_instance_id": context.instance_id},
    )


def _call_context(
    revision: TemplateRevision,
    provenance: TemplateInstantiationProvenance,
    context: TemplateInstantiationContext,
) -> WorkflowCallContext:
    owner = provenance.applied_by
    return WorkflowCallContext(
        operation=OperationContext(
            correlation_id=f"template-instance:{context.instance_id}",
            owner_type=owner.type,
            owner_id=owner.id,
            project_id=revision.project_id,
        ),
        actor_ref=_actor_ref(owner),
    )


def _actor_ref(owner: OwnerRef) -> str:
    if owner.id.startswith(f"{owner.type}_") or owner.id.startswith(f"{owner.type}:"):
        return owner.id
    return f"{owner.type}:{owner.id}"


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return cast(Mapping[str, object], value)


def _array(
    data: Mapping[str, object],
    name: str,
    *,
    required: bool = False,
) -> tuple[object, ...]:
    if name not in data:
        if required:
            raise ValueError(f"missing required field: {name}")
        return ()
    value = data[name]
    if not isinstance(value, list | tuple):
        raise ValueError(f"{name} must be an array")
    return tuple(value)


def _required_string(data: Mapping[str, object], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    return value


def _optional_string(data: Mapping[str, object], name: str) -> str | None:
    value = data.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    return value


def _optional_positive_int(data: Mapping[str, object], name: str) -> int | None:
    value = data.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _optional_bool(data: Mapping[str, object], name: str, default: bool) -> bool:
    value = data.get(name)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _string_tuple(data: Mapping[str, object], name: str) -> tuple[str, ...]:
    values = _array(data, name)
    if not all(isinstance(item, str) and item.strip() for item in values):
        raise ValueError(f"{name} must contain only non-blank strings")
    return tuple(cast(str, item) for item in values)


def _frozen_mapping(value: object, name: str) -> Mapping[str, FrozenJsonValue]:
    mapping = _mapping(value, name)
    return cast(Mapping[str, FrozenJsonValue], mapping)


def _reject_unknown(data: Mapping[str, object], allowed: set[str], name: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{name} contains unsupported fields: {', '.join(unknown)}")
