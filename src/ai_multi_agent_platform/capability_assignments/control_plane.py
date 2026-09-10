"""Control Plane projection and lifecycle commands for Capability Assignments."""

from __future__ import annotations

from typing import Literal, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.security import ActorIdentity, ActorType, infer_actor_identity

from .codec import _content as _content_from_json
from .codec import policy_to_json, revision_to_json
from .contracts import CapabilityAssignmentAccessContext
from .models import (
    CAPABILITY_ASSIGNMENT_SCHEMA_VERSION,
    CapabilityAssignmentContent,
    CapabilityAssignmentPolicy,
    CapabilityAssignmentRevision,
)
from .service import CapabilityAssignmentService

CAPABILITY_ASSIGNMENT_COLLECTION = "capability-assignments"
CAPABILITY_ASSIGNMENT_COMMANDS = (
    "capability-assignment.create",
    "capability-assignment.revise",
)


class CapabilityAssignmentResourceService:
    """Authorized northbound projection over the #366 owning service."""

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


class CapabilityAssignmentCommandHandlers:
    """Safe northbound mutation handlers for canonical assignment revisions."""

    def __init__(self, service: CapabilityAssignmentService) -> None:
        self.service = service

    async def create_assignment(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_collection(resource_ref)
        access = _assignment_access(
            context,
            approval_id=_optional_string(payload, "approval_id"),
        )
        revision = await self.service.create(
            owner_ref=_owner_ref(payload.get("owner_ref"), context),
            content=_content_from_payload(payload.get("content"), context),
            access=access,
            project_id=_optional_string(payload, "project_id"),
            organization_id=_optional_string(payload, "organization_id"),
            assignment_id=_optional_string(payload, "assignment_id"),
        )
        policy = self.service.repository.get(revision.assignment_id)
        return _assignment_resource(policy, revision)

    async def revise_assignment(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        revision = await self.service.revise(
            resource_ref,
            _content_from_payload(payload.get("content"), context),
            access=_assignment_access(
                context,
                approval_id=_optional_string(payload, "approval_id"),
            ),
            expected_revision=_required_positive_int(payload, "expected_revision"),
        )
        policy = self.service.repository.get(resource_ref)
        return _assignment_resource(policy, revision)


def register_capability_assignment_resource_control_plane(
    control_plane: ControlPlane,
    service: CapabilityAssignmentService,
) -> None:
    """Expose #366 reads and safe revision mutations through canonical APIs."""

    if CAPABILITY_ASSIGNMENT_COLLECTION not in control_plane.registered_collections:
        control_plane.register_resource_service(
            CAPABILITY_ASSIGNMENT_COLLECTION,
            CapabilityAssignmentResourceService(service),
        )

    handlers = CapabilityAssignmentCommandHandlers(service)
    registered = set(control_plane.registered_commands)
    if CAPABILITY_ASSIGNMENT_COMMANDS[0] not in registered:
        control_plane.register_command(
            CAPABILITY_ASSIGNMENT_COMMANDS[0],
            handlers.create_assignment,
        )
    if CAPABILITY_ASSIGNMENT_COMMANDS[1] not in registered:
        control_plane.register_command(
            CAPABILITY_ASSIGNMENT_COMMANDS[1],
            handlers.revise_assignment,
        )


def _assignment_access(
    context: RequestContext,
    *,
    approval_id: str | None = None,
) -> CapabilityAssignmentAccessContext:
    return CapabilityAssignmentAccessContext(
        actor=_actor_identity(context),
        operation=OperationContext(
            correlation_id=context.correlation_id,
            causation_id=context.request_id,
            owner_type=context.actor.owner_type,
            owner_id=context.actor.owner_id,
        ),
        approval_id=approval_id,
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


def _owner_ref(value: JsonValue | None, context: RequestContext) -> OwnerRef:
    if value is None:
        if context.actor.owner_type is None or context.actor.owner_id is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "capability assignment creation requires an authenticated owner context or owner_ref",
            )
        return OwnerRef(type=context.actor.owner_type, id=context.actor.owner_id)

    if not isinstance(value, dict):
        raise ContractError(ErrorCode.INVALID_REQUEST, "owner_ref must be an object")
    owner_type = value.get("type")
    owner_id = value.get("id")
    if owner_type not in {"user", "organization", "team", "service"}:
        raise ContractError(ErrorCode.INVALID_REQUEST, "owner_ref.type is not supported")
    if not isinstance(owner_id, str) or not owner_id.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, "owner_ref.id must be a non-blank string")
    return OwnerRef(
        type=cast(Literal["user", "organization", "team", "service"], owner_type),
        id=owner_id,
    )


def _content_from_payload(
    value: JsonValue | None,
    context: RequestContext,
) -> CapabilityAssignmentContent:
    if not isinstance(value, dict):
        raise ContractError(ErrorCode.INVALID_REQUEST, "content must be an object")
    normalized = dict(value)
    normalized["schema_version"] = normalized.get(
        "schema_version",
        CAPABILITY_ASSIGNMENT_SCHEMA_VERSION,
    )
    # Provenance is server-authored. A browser must not be able to impersonate the
    # creator or make a configuration revision look as if it came from another source.
    normalized["provenance"] = {
        "source": "control-plane",
        "creator_ref": context.actor.principal_ref,
    }
    try:
        return _content_from_json(normalized)
    except (TypeError, ValueError) as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"invalid capability assignment content: {exc}",
        ) from exc


def _optional_string(payload: dict[str, JsonValue], field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{field} must be a non-blank string when provided",
        )
    return value


def _required_positive_int(payload: dict[str, JsonValue], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field} must be a positive integer")
    return value


def _require_collection(resource_ref: str) -> None:
    if resource_ref != CAPABILITY_ASSIGNMENT_COLLECTION:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"resource_ref must be {CAPABILITY_ASSIGNMENT_COLLECTION!r} for assignment creation",
        )


def _assignment_resource(
    policy: CapabilityAssignmentPolicy,
    revision: CapabilityAssignmentRevision,
) -> dict[str, JsonValue]:
    resource = policy_to_json(policy)
    resource["id"] = policy.assignment_id
    resource["type"] = "capability_assignment"
    resource["revision"] = revision_to_json(revision)
    return resource


__all__ = [
    "CAPABILITY_ASSIGNMENT_COLLECTION",
    "CAPABILITY_ASSIGNMENT_COMMANDS",
    "CapabilityAssignmentCommandHandlers",
    "CapabilityAssignmentResourceService",
    "register_capability_assignment_resource_control_plane",
]
