"""Portable export of canonical Agent Teams into reusable Template dependency graphs."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.agents import AgentProfile, AgentService
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import FrozenJsonValue, JsonValue
from ai_multi_agent_platform.control_plane.models import json_value
from ai_multi_agent_platform.domain import OwnerRef, new_id

from .agent_handlers import portable_agent_profile_payload
from .models import (
    CapabilityRequirement,
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
class AgentTeamTemplateExporter:
    """Export one Team plus portable, published Agent Template dependencies."""

    agents: AgentService
    templates: TemplateService

    def create_from_team(
        self,
        team_id: str,
        *,
        owner_ref: OwnerRef,
        author: str,
        revision: int | None = None,
        name: str | None = None,
    ) -> TemplateRevision:
        source = self.agents.get_team_revision(team_id, revision)
        if source.profile.shared_resource_refs:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "Agent Team Template export cannot preserve deployment-local shared resource refs",
                details={"team_id": source.team_id},
            )
        if source.profile.coordination_policy_ref is not None:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "Agent Team Template export requires a portable coordination-policy dependency",
                details={"team_id": source.team_id},
            )

        child_contents: list[tuple[str, int, TemplateContent]] = []
        for member in source.profile.members:
            agent = self.agents.get_agent_revision(
                member.agent.agent_id,
                member.agent.revision,
            )
            child_contents.append(
                (
                    agent.agent_id,
                    agent.revision,
                    _agent_template_content(agent.profile, agent.agent_id, agent.revision, author),
                )
            )

        for _, _, content in child_contents:
            validate_template_configuration(content.configuration)

        created_template_ids: list[str] = []
        try:
            published_agents: dict[str, TemplateRevision] = {}
            for agent_id, _, content in child_contents:
                child_template_id = new_id("template")
                # Register the planned identity before persistence so a repository that mutates
                # state and then raises can still be compensated deterministically.
                created_template_ids.append(child_template_id)
                draft = self.templates.create_draft(
                    owner_ref=owner_ref,
                    content=content,
                    template_id=child_template_id,
                )
                published_agents[agent_id] = self.templates.publish(
                    draft.template_id,
                    expected_revision=draft.revision,
                )

            portable_members: tuple[FrozenJsonValue, ...] = tuple(
                {
                    "agent_template_id": published_agents[member.agent.agent_id].template_id,
                    "agent_template_revision": published_agents[member.agent.agent_id].revision,
                    "role": member.role,
                    "required": member.required,
                    "can_delegate_to_template_ids": tuple(
                        published_agents[agent_id].template_id
                        for agent_id in member.can_delegate_to
                    ),
                }
                for member in source.profile.members
            )
            leader_template_id = (
                None
                if source.profile.leader_agent_id is None
                else published_agents[source.profile.leader_agent_id].template_id
            )
            profile: dict[str, FrozenJsonValue] = {
                "name": source.profile.name,
                "description": source.profile.description or None,
                "members": portable_members,
                "coordination_policy_ref": None,
                "leader_agent_template_id": leader_template_id,
                "shared_capability_ids": source.profile.shared_capability_ids,
                "shared_resource_refs": (),
                "max_parallel_agents": source.profile.max_parallel_agents,
                "max_steps": source.profile.max_steps,
                "unavailable_member_policy": source.profile.unavailable_member_policy.value,
                "enabled": source.profile.enabled,
                "metadata": _freeze_json(json_value(source.profile.metadata)),
            }
            dependencies = tuple(
                TemplateDependency(
                    template_id=published_agents[member.agent.agent_id].template_id,
                    revision=published_agents[member.agent.agent_id].revision,
                )
                for member in source.profile.members
            )
            requirements = TemplateRequirements(
                capabilities=tuple(
                    CapabilityRequirement(capability_id=capability_id)
                    for capability_id in source.profile.shared_capability_ids
                )
            )
            content = TemplateContent(
                name=name or source.profile.name,
                description=f"Template exported from Agent Team {source.team_id}@{source.revision}",
                template_type=TemplateType.AGENT_TEAM,
                configuration=TemplateConfiguration(
                    payload={
                        "profile": profile,
                        "project_id": None,
                        "workspace_id": None,
                    }
                ),
                dependencies=dependencies,
                requirements=requirements,
                provenance=TemplateProvenance(
                    author=author,
                    source="canonical-agent-team-export",
                    trust=TemplateTrust.LOCAL,
                    metadata={
                        "source_resource_type": "agent_team",
                        "source_resource_id": source.team_id,
                        "source_resource_revision": source.revision,
                        "source_project_id": source.project_id,
                        "source_workspace_id": source.workspace_id,
                    },
                ),
                tags=("agent-team", "exported"),
            )
            validate_template_configuration(content.configuration)
            parent_template_id = new_id("template")
            created_template_ids.append(parent_template_id)
            return self.templates.create_draft(
                owner_ref=owner_ref,
                content=content,
                template_id=parent_template_id,
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
        for template_id in reversed(created_template_ids):
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
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                (
                    "Agent Team Template export failed and partial Templates could not be "
                    "fully compensated"
                ),
                details={
                    "export_error_type": type(export_error).__name__,
                    "export_error": str(export_error),
                    "cleanup_failures": json_value(failures),
                },
            ) from export_error


def _agent_template_content(
    profile: AgentProfile,
    agent_id: str,
    revision: int,
    author: str,
) -> TemplateContent:
    profile_payload = portable_agent_profile_payload(profile)
    return TemplateContent(
        name=profile.name,
        description=f"Template exported from Agent {agent_id}@{revision}",
        template_type=TemplateType.AGENT,
        configuration=TemplateConfiguration(
            payload={
                "profile": profile_payload,
                "project_id": None,
                "workspace_id": None,
            }
        ),
        requirements=TemplateRequirements(
            capabilities=_capability_requirements(profile),
            model_policy_refs=(
                (profile.model.routing_profile_ref,)
                if profile.model.routing_profile_ref is not None
                else ()
            ),
        ),
        provenance=TemplateProvenance(
            author=author,
            source="canonical-agent-team-member-export",
            trust=TemplateTrust.LOCAL,
            metadata={
                "source_resource_type": "agent",
                "source_resource_id": agent_id,
                "source_resource_revision": revision,
                "source_default_project_id": profile.workspace_defaults.project_id,
                "source_default_workspace_id": profile.workspace_defaults.workspace_id,
            },
        ),
        tags=("agent", "team-member", "exported"),
    )


def _capability_requirements(profile: AgentProfile) -> tuple[CapabilityRequirement, ...]:
    result: list[CapabilityRequirement] = []
    for constraint in profile.capabilities.constraints:
        version_constraint: str | None = None
        if constraint.exact_version is not None:
            version_constraint = f"=={constraint.exact_version}"
        elif constraint.minimum_version is not None or constraint.maximum_version is not None:
            bounds: list[str] = []
            if constraint.minimum_version is not None:
                bounds.append(f">={constraint.minimum_version}")
            if constraint.maximum_version is not None:
                bounds.append(f"<={constraint.maximum_version}")
            version_constraint = ",".join(bounds)
        result.append(
            CapabilityRequirement(
                capability_id=constraint.capability_id,
                optional=not constraint.required,
                version_constraint=version_constraint,
                privileged=constraint.approval_ref is not None,
            )
        )
    return tuple(result)


def _freeze_json(value: JsonValue) -> FrozenJsonValue:
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, dict):
        return {key: _freeze_json(item) for key, item in value.items()}
    return value
