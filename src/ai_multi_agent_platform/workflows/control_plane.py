"""Read-only Control Plane projection for canonical reusable Workflow definitions."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext, json_object

from .authorization import AuthorizedWorkflowService, WorkflowCallContext
from .models import WorkflowDefinition, WorkflowRevision, WorkflowRevisionRef

WORKFLOW_COLLECTION = "workflows"


class WorkflowResourceService:
    """Authorized northbound read projection over the #364 owning service."""

    search_indexable = False

    def __init__(self, service: AuthorizedWorkflowService) -> None:
        self.service = service

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        call_context = _workflow_context(context)
        project_filter = None
        organization_filter = None
        if query.filters is not None:
            project_filter = query.filters.get("project_id")
            organization_filter = query.filters.get("organization_id")

        resources: list[dict[str, JsonValue]] = []
        for definition in await self.service.list(context=call_context):
            if project_filter is not None and definition.project_id != project_filter:
                continue
            if (
                organization_filter is not None
                and definition.organization_id != organization_filter
            ):
                continue
            revision = await self.service.resolve(
                WorkflowRevisionRef(definition.workflow_id, definition.current_revision),
                context=call_context,
            )
            resources.append(_workflow_resource(definition, revision))
        return tuple(resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        call_context = _workflow_context(context)
        definition = await self.service.get(resource_id, context=call_context)
        revision = await self.service.resolve(
            WorkflowRevisionRef(definition.workflow_id, definition.current_revision),
            context=call_context,
        )
        return _workflow_resource(definition, revision)


def register_workflow_resource_control_plane(
    control_plane: ControlPlane,
    service: AuthorizedWorkflowService,
) -> None:
    """Expose #364 reads when the canonical Workflow owner service is composed."""

    if WORKFLOW_COLLECTION in control_plane.registered_collections:
        return
    control_plane.register_resource_service(WORKFLOW_COLLECTION, WorkflowResourceService(service))


def _workflow_context(context: RequestContext) -> WorkflowCallContext:
    return WorkflowCallContext(
        operation=OperationContext(
            correlation_id=context.correlation_id,
            causation_id=context.request_id,
            owner_type=context.actor.owner_type,
            owner_id=context.actor.owner_id,
        ),
        actor_ref=context.actor.principal_ref,
    )


def _workflow_resource(
    definition: WorkflowDefinition,
    revision: WorkflowRevision,
) -> dict[str, JsonValue]:
    return {
        "id": definition.workflow_id,
        "type": "workflow",
        "workflow_id": definition.workflow_id,
        "current_revision": definition.current_revision,
        "owner_ref": json_object(definition.owner_ref),
        "project_id": definition.project_id,
        "organization_id": definition.organization_id,
        "created_at": definition.created_at.isoformat(),
        "updated_at": definition.updated_at.isoformat(),
        "revision": json_object(revision),
    }
