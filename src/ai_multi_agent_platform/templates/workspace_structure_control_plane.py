"""Control Plane registration for creating Templates from canonical Workspace structures."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import RequestContext

from .control_plane import (
    TEMPLATE_COLLECTION,
    _actor_owner,
    _optional_positive_int,
    _optional_string,
    _require_collection,
    _required,
    _required_string,
    _template_resource,
)
from .repository import TemplateRepository
from .workspace_structure_handler import WorkspaceStructureTemplateExporter

WORKSPACE_STRUCTURE_TEMPLATE_EXPORT_COMMAND = "template.create-from-workspaces"


@dataclass(slots=True)
class WorkspaceStructureTemplateCommandHandler:
    repository: TemplateRepository
    exporter: WorkspaceStructureTemplateExporter

    async def create_from_workspaces(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_collection(resource_ref, TEMPLATE_COLLECTION)
        workspace_ids = _workspace_ids(_required(payload, "workspace_ids"))
        revision = await self.exporter.create_from_workspaces(
            workspace_ids,
            owner_ref=_actor_owner(context),
            author=context.actor.principal_ref,
            name=_required_string(payload, "name"),
            project_template_id=_optional_string(payload, "project_template_id"),
            project_template_revision=_optional_positive_int(
                payload,
                "project_template_revision",
            ),
        )
        return _template_resource(self.repository, revision.template_id)


def register_workspace_structure_template_control_plane(
    control_plane: ControlPlane,
    repository: TemplateRepository,
    exporter: WorkspaceStructureTemplateExporter,
) -> None:
    handler = WorkspaceStructureTemplateCommandHandler(repository, exporter)
    control_plane.register_command(
        WORKSPACE_STRUCTURE_TEMPLATE_EXPORT_COMMAND,
        handler.create_from_workspaces,
    )


def _workspace_ids(value: JsonValue) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "workspace_ids must be a non-empty array",
        )
    result_items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "workspace_ids must contain only non-blank strings",
            )
        result_items.append(item)
    result = tuple(result_items)
    if len(set(result)) != len(result):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "workspace_ids must be unique",
        )
    return result
