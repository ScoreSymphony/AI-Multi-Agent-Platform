"""Control Plane composition for canonical Workflow -> Template export."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.workflows import WorkflowCallContext
from ai_multi_agent_platform.workflows.control_plane import (
    register_workflow_resource_control_plane,
)

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
from .workflow_exporter import WorkflowTemplateExporter

WORKFLOW_TEMPLATE_EXPORT_COMMAND = "template.create-from-workflow"


@dataclass(slots=True)
class WorkflowTemplateCommandHandler:
    repository: TemplateRepository
    exporter: WorkflowTemplateExporter

    async def create_from_workflow(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_collection(resource_ref, TEMPLATE_COLLECTION)
        workflow_id = _required_string(payload, "workflow_id")
        source_revision = _optional_positive_int(payload, "revision")
        revision = await self.exporter.create_from_workflow(
            workflow_id,
            context=_workflow_context(context),
            owner_ref=_actor_owner(context),
            author=context.actor.principal_ref,
            revision=source_revision,
            name=_optional_string(payload, "name"),
        )
        return _template_resource(self.repository, revision.template_id)


def register_workflow_template_export_control_plane(
    control_plane: ControlPlane,
    repository: TemplateRepository,
    exporter: WorkflowTemplateExporter,
) -> None:
    register_workflow_resource_control_plane(control_plane, exporter.workflows)
    handler = WorkflowTemplateCommandHandler(repository, exporter)
    control_plane.register_command(
        WORKFLOW_TEMPLATE_EXPORT_COMMAND,
        handler.create_from_workflow,
    )


def _workflow_context(context: RequestContext) -> WorkflowCallContext:
    return WorkflowCallContext(
        operation=OperationContext(
            correlation_id=context.correlation_id,
            owner_type=context.actor.owner_type,
            owner_id=context.actor.owner_id,
        ),
        actor_ref=context.actor.principal_ref,
    )
