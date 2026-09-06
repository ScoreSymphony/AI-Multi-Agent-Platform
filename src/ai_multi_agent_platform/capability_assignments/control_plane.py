"""Read-only Control Plane projection for canonical Capability Assignments."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.security import ActorIdentity, ActorType, infer_actor_identity

from .codec import policy_to_json, revision_to_json
from .contracts import CapabilityAssignmentAccessContext
from .models import CapabilityAssignmentPolicy, CapabilityAssignmentRevision
from .service import CapabilityAssignmentService

CAPABILITY_ASSIGNMENT_COLLECTION = "capability-assignments"


class CapabilityAssignmentResourceService:
    """Authorized northbound read projection over the #366 owning service."""

    search_indexable = False

    def __init__(self, service: CapabilityAssignmentService) -> None:
        self.service = service

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        access = _assignment_access(context)
        project_filter = None
        organization_filter = None
        if query.filters is not None:
            project_filter = query.filters.get("project_id")
            organization_filter = query.filters.get("organization_id")

        policies = await self.service.list(
            access=access,
            project_id=project_filter,
            organization_id=organization_filter,
        )
        resources: list[dict[str, JsonValue]] = []
        for policy in policies:
            revision = await self.service.get_revision(
                policy.assignment_id,
                policy.current_revision,
                access=access,
            )
            resources.append(_assignment_resource(policy, revision))
        return tuple(resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        access = _assignment_access(context)
        policy = await self.service.get(resource_id, access=access)
        revision = await self.service.get_revision(
            policy.assignment_id,
            policy.current_revision,
            access=access,
        )
        return _assignment_resource(policy, revision)


def register_capability_assignment_resource_control_plane(
    control_plane: ControlPlane,
    service: CapabilityAssignmentService,
) -> None:
    """Expose #366 reads when the canonical assignment owner service is composed."""

    if CAPABILITY_ASSIGNMENT_COLLECTION in control_plane.registered_collections:
        return
    control_plane.register_resource_service(
        CAPABILITY_ASSIGNMENT_COLLECTION,
        CapabilityAssignmentResourceService(service),
    )


def _assignment_access(context: RequestContext) -> CapabilityAssignmentAccessContext:
    return CapabilityAssignmentAccessContext(
        actor=_actor_identity(context),
        operation=OperationContext(
            correlation_id=context.correlation_id,
            causation_id=context.request_id,
            owner_type=context.actor.owner_type,
            owner_id=context.actor.owner_id,
        ),
    )


def _actor_identity(context: RequestContext) -> ActorIdentity:
    actor_type = context.actor.actor_type
    if actor_type is None:
        return infer_actor_identity(context.actor.principal_ref)
    try:
        return ActorIdentity(context.actor.principal_ref, ActorType(actor_type))
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"unsupported authenticated actor type: {actor_type}",
        ) from exc


def _assignment_resource(
    policy: CapabilityAssignmentPolicy,
    revision: CapabilityAssignmentRevision,
) -> dict[str, JsonValue]:
    resource = policy_to_json(policy)
    resource["id"] = policy.assignment_id
    resource["type"] = "capability_assignment"
    resource["revision"] = revision_to_json(revision)
    return resource
