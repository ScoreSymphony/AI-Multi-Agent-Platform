"""Control Plane registration for creating Templates from canonical Agent Teams."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import RequestContext

from .access import TemplateScopeAccess
from .agent_team_exporter import AgentTeamTemplateExporter
from .control_plane import (
    TEMPLATE_COLLECTION,
    _actor_owner,
    _optional_positive_int,
    _optional_string,
    _require_collection,
    _required_string,
    _template_resource,
)
from .repository import TemplateRepository

AGENT_TEAM_TEMPLATE_EXPORT_COMMAND = "template.create-from-agent-team"


@dataclass(slots=True)
class AgentTeamTemplateCommandHandler:
    repository: TemplateRepository
    exporter: AgentTeamTemplateExporter
    scope_access: TemplateScopeAccess

    async def create_from_team(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_collection(resource_ref, TEMPLATE_COLLECTION)
        team_id = _required_string(payload, "team_id")
        source_revision = _optional_positive_int(payload, "revision")
        source = self.exporter.agents.get_team_revision(team_id, source_revision)
        await self.scope_access.authorize(
            context,
            AGENT_TEAM_TEMPLATE_EXPORT_COMMAND,
            source.team_id,
            owner_ref=source.owner_ref,
            project_id=source.project_id,
        )
        revision = self.exporter.create_from_team(
            team_id,
            owner_ref=_actor_owner(context),
            author=context.actor.principal_ref,
            revision=source_revision,
            name=_optional_string(payload, "name"),
        )
        return _template_resource(self.repository, revision.template_id)


def register_agent_team_template_control_plane(
    control_plane: ControlPlane,
    repository: TemplateRepository,
    exporter: AgentTeamTemplateExporter,
) -> None:
    handler = AgentTeamTemplateCommandHandler(
        repository,
        exporter,
        TemplateScopeAccess(control_plane),
    )
    control_plane.register_command(
        AGENT_TEAM_TEMPLATE_EXPORT_COMMAND,
        handler.create_from_team,
    )
