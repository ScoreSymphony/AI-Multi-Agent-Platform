"""Portable export of canonical Workflow revisions into reusable Template graphs."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.agents import AgentRevisionRef, AgentService, AgentTeamRevisionRef
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import FrozenJsonValue, JsonValue
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.workflows import (
    AuthorizedWorkflowService,
    WorkflowCallContext,
    WorkflowCapabilityRequirement,
    WorkflowRevision,
    WorkflowRevisionRef,
    WorkflowStage,
)

from .agent_handlers import AgentTemplateExporter
from .agent_team_exporter import AgentTeamTemplateExporter
from .models import (
    CapabilityRequirement,
    TemplateCompatibility,
    TemplateConfiguration,
    TemplateContent,
    TemplateDependency,
    TemplateProvenance,
    TemplateRequirements,
    TemplateRevision,
    TemplateTrust,
    TemplateType,
)
from .service import TemplateService, validate_template_configuration


@dataclass(slots=True)
class WorkflowTemplateExporter:
    """Export one authorized #364 revision without retaining deployment-local Agent IDs."""

    workflows: AuthorizedWorkflowService
    agents: AgentService
    templates: TemplateService

    async def create_from_workflow(
        self,
        workflow_id: str,
        *,
        context: WorkflowCallContext,
        owner_ref: OwnerRef,
        author: str,
        revision: int | None = None,
        name: str | None = None,
    ) -> TemplateRevision:
        if revision is None:
            definition = await self.workflows.get(workflow_id, context=context)
            revision = definition.current_revision
        source = await self.workflows.resolve(
            WorkflowRevisionRef(workflow_id, revision),
            context=context,
        )
        requirements = _requirements(source)

        created_template_ids: list[str] = []
        try:
            agent_templates: dict[tuple[str, int], TemplateRevision] = {}
            team_templates: dict[tuple[str, int], TemplateRevision] = {}
            agent_exporter = AgentTemplateExporter(self.agents, self.templates)
            team_exporter = AgentTeamTemplateExporter(self.agents, self.templates)

            for stage in source.content.stages:
                if stage.agent is not None:
                    key = (stage.agent.agent_id, stage.agent.revision)
                    if key not in agent_templates:
                        draft = agent_exporter.create_from_agent(
                            stage.agent.agent_id,
                            owner_ref=owner_ref,
                            author=author,
                            revision=stage.agent.revision,
                        )
                        created_template_ids.append(draft.template_id)
                        agent_templates[key] = self.templates.publish(
                            draft.template_id,
                            expected_revision=draft.revision,
                        )
                elif stage.team is not None:
                    key = (stage.team.team_id, stage.team.revision)
                    if key not in team_templates:
                        draft = team_exporter.create_from_team(
                            stage.team.team_id,
                            owner_ref=owner_ref,
                            author=author,
                            revision=stage.team.revision,
                        )
                        created_template_ids.extend(
                            dependency.template_id for dependency in draft.content.dependencies
                        )
                        created_template_ids.append(draft.template_id)
                        team_templates[key] = self.templates.publish(
                            draft.template_id,
                            expected_revision=draft.revision,
                        )

            direct_dependencies = _direct_dependencies(
                source,
                agent_templates=agent_templates,
                team_templates=team_templates,
            )
            content = TemplateContent(
                name=name or source.content.name,
                description=(
                    f"Template exported from canonical Workflow "
                    f"{source.workflow_id}@{source.revision}"
                ),
                template_type=TemplateType.WORKFLOW_PLAN,
                configuration=TemplateConfiguration(
                    payload={
                        "stages": tuple(
                            _stage_payload(
                                stage,
                                agent_templates=agent_templates,
                                team_templates=team_templates,
                            )
                            for stage in source.content.stages
                        ),
                        "parameters": tuple(
                            {
                                "name": parameter.name,
                                "required": parameter.required,
                                "secret_reference": parameter.secret_reference,
                                "description": parameter.description,
                            }
                            for parameter in source.content.parameters
                        ),
                        "metadata": source.content.metadata,
                    }
                ),
                dependencies=direct_dependencies,
                requirements=requirements,
                compatibility=TemplateCompatibility(
                    platform_version_range=source.content.compatibility.platform_version_range,
                    contract_versions=source.content.compatibility.contract_versions,
                    orchestrator_agnostic=source.content.compatibility.orchestrator_agnostic,
                    provider_agnostic=source.content.compatibility.provider_agnostic,
                    metadata=source.content.compatibility.metadata,
                ),
                provenance=TemplateProvenance(
                    author=author,
                    source="canonical-workflow-export",
                    trust=TemplateTrust.LOCAL,
                    metadata={
                        "source_resource_type": "workflow",
                        "source_resource_id": source.workflow_id,
                        "source_resource_revision": source.revision,
                        "source_project_id": source.project_id,
                        "source_organization_id": source.organization_id,
                    },
                ),
                tags=("workflow", "exported"),
            )
            validate_template_configuration(content.configuration)
            template_id = new_id("template")
            created_template_ids.append(template_id)
            return self.templates.create_draft(
                owner_ref=owner_ref,
                content=content,
                template_id=template_id,
            )
        except Exception as export_error:
            self._compensate_partial_export(created_template_ids, export_error)
            raise

    def _compensate_partial_export(
        self,
        created_template_ids: list[str],
        export_error: Exception,
    ) -> None:
        failures: list[dict[str, JsonValue]] = []
        seen: set[str] = set()
        for template_id in reversed(created_template_ids):
            if template_id in seen:
                continue
            seen.add(template_id)
            try:
                self.templates.repository.delete_template(template_id)
            except ContractError as cleanup_error:
                if cleanup_error.code is ErrorCode.NOT_FOUND:
                    continue
                failures.append(
                    {
                        "template_id": template_id,
                        "error_type": type(cleanup_error).__name__,
                        "error": str(cleanup_error),
                    }
                )
            except Exception as cleanup_error:
                failures.append(
                    {
                        "template_id": template_id,
                        "error_type": type(cleanup_error).__name__,
                        "error": str(cleanup_error),
                    }
                )
        if failures:
            cleanup_failures: list[JsonValue] = [dict(failure) for failure in failures]
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "Workflow Template export failed and partial Templates could not be fully compensated",
                details={
                    "export_error_type": type(export_error).__name__,
                    "export_error": str(export_error),
                    "cleanup_failures": cleanup_failures,
                },
            ) from export_error


def _stage_payload(
    stage: WorkflowStage,
    *,
    agent_templates: dict[tuple[str, int], TemplateRevision],
    team_templates: dict[tuple[str, int], TemplateRevision],
) -> dict[str, FrozenJsonValue]:
    payload: dict[str, FrozenJsonValue] = {
        "stage_id": stage.stage_id,
        "title": stage.title,
        "description": stage.description,
        "depends_on": stage.depends_on,
        "parameter_refs": stage.parameter_refs,
        "capabilities": tuple(
            {
                "capability_id": requirement.capability_id,
                "optional": requirement.optional,
                "version_constraint": requirement.version_constraint,
            }
            for requirement in stage.capabilities
        ),
        "tool_ids": stage.tool_ids,
        "model_routing_policy_ref": stage.model_routing_policy_ref,
        "permission_actions": stage.permission_actions,
        "metadata": stage.metadata,
    }
    if stage.agent is not None:
        template = agent_templates[(stage.agent.agent_id, stage.agent.revision)]
        payload["agent_template_id"] = template.template_id
        payload["agent_template_revision"] = template.revision
    elif stage.team is not None:
        template = team_templates[(stage.team.team_id, stage.team.revision)]
        payload["team_template_id"] = template.template_id
        payload["team_template_revision"] = template.revision
    return payload


def _direct_dependencies(
    source: WorkflowRevision,
    *,
    agent_templates: dict[tuple[str, int], TemplateRevision],
    team_templates: dict[tuple[str, int], TemplateRevision],
) -> tuple[TemplateDependency, ...]:
    result: list[TemplateDependency] = []
    seen: set[str] = set()
    for stage in source.content.stages:
        template: TemplateRevision | None = None
        if stage.agent is not None:
            template = agent_templates[(stage.agent.agent_id, stage.agent.revision)]
        elif stage.team is not None:
            template = team_templates[(stage.team.team_id, stage.team.revision)]
        if template is None or template.template_id in seen:
            continue
        seen.add(template.template_id)
        result.append(TemplateDependency(template.template_id, template.revision))
    return tuple(result)


def _requirements(source: WorkflowRevision) -> TemplateRequirements:
    capabilities: dict[str, WorkflowCapabilityRequirement] = {}
    model_policy_refs: list[str] = []
    permission_actions: list[str] = []

    for stage in source.content.stages:
        for requirement in stage.capabilities:
            existing = capabilities.get(requirement.capability_id)
            if existing is not None and (
                existing.optional != requirement.optional
                or existing.version_constraint != requirement.version_constraint
            ):
                raise ContractError(
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    "Workflow Template export cannot losslessly collapse differing capability requirements",
                    details={"capability_id": requirement.capability_id},
                )
            capabilities[requirement.capability_id] = requirement
        if (
            stage.model_routing_policy_ref is not None
            and stage.model_routing_policy_ref not in model_policy_refs
        ):
            model_policy_refs.append(stage.model_routing_policy_ref)
        for action in stage.permission_actions:
            if action not in permission_actions:
                permission_actions.append(action)

    return TemplateRequirements(
        capabilities=tuple(
            CapabilityRequirement(
                capability_id=requirement.capability_id,
                optional=requirement.optional,
                version_constraint=requirement.version_constraint,
            )
            for requirement in capabilities.values()
        ),
        model_policy_refs=tuple(model_policy_refs),
        permission_actions=tuple(permission_actions),
    )
