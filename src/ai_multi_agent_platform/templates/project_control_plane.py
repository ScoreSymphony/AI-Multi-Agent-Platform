"""Control Plane registration for creating Templates from canonical Projects."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import RequestContext

from .access import TemplateScopeAccess
from .control_plane import (
    TEMPLATE_COLLECTION,
    _actor_owner,
    _optional_string,
    _require_collection,
    _required_string,
    _template_resource,
)
from .project_handler import ProjectTemplateExporter
from .repository import TemplateRepository

PROJECT_TEMPLATE_EXPORT_COMMAND = "template.create-from-project"


@dataclass(slots=True)
class ProjectTemplateCommandHandler:
    repository: TemplateRepository
    exporter: ProjectTemplateExporter
    scope_access: TemplateScopeAccess

    async def create_from_project(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_collection(resource_ref, TEMPLATE_COLLECTION)
        project_id = _required_string(payload, "project_id")
        source = self.exporter.scopes.get_project(project_id)
        await self.scope_access.authorize(
            context,
            PROJECT_TEMPLATE_EXPORT_COMMAND,
            source.id,
            owner_ref=source.owner_ref,
            project_id=source.id,
        )
        revision = self.exporter.create_from_project(
            project_id,
            owner_ref=_actor_owner(context),
            author=context.actor.principal_ref,
            name=_optional_string(payload, "name"),
        )
        return _template_resource(self.repository, revision.template_id)


def register_project_template_control_plane(
    control_plane: ControlPlane,
    repository: TemplateRepository,
    exporter: ProjectTemplateExporter,
) -> None:
    """Register the exact Project-to-Template export command."""

    handler = ProjectTemplateCommandHandler(
        repository,
        exporter,
        TemplateScopeAccess(control_plane),
    )
    control_plane.register_command(
        PROJECT_TEMPLATE_EXPORT_COMMAND,
        handler.create_from_project,
    )
