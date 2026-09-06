"""Control Plane composition for Capability Assignment -> Template export."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.capability_assignments import CapabilityAssignmentAccessContext
from ai_multi_agent_platform.capability_assignments.control_plane import (
    register_capability_assignment_resource_control_plane,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.security import ActorIdentity, ActorType, infer_actor_identity

from .access import TemplateScopeAccess
from .capability_assignment_exporter import CapabilityAssignmentTemplateExporter
from .control_plane import TEMPLATE_COLLECTION, TemplateResourceService

CAPABILITY_ASSIGNMENT_TEMPLATE_EXPORT_COMMAND = "template.create-from-capability-assignment"


@dataclass(slots=True)
class CapabilityAssignmentTemplateExportCommand:
    """Authorize source reads through #366, then create an ordinary Template draft."""

    control_plane: ControlPlane
    exporter: CapabilityAssignmentTemplateExporter

    async def __call__(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if resource_ref != TEMPLATE_COLLECTION:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"command must target collection {TEMPLATE_COLLECTION!r}",
            )
        assignment_id = _required_string(payload, "assignment_id")
        source_revision = _optional_positive_int(payload, "revision")
        revision = await self.exporter.create_from_assignment(
            assignment_id,
            access=_assignment_access(context),
            owner_ref=_actor_owner(context),
            author=context.actor.principal_ref,
            revision=source_revision,
            name=_optional_string(payload, "name"),
        )
        resources = TemplateResourceService(
            self.exporter.templates.repository,
            scope_access=TemplateScopeAccess(self.control_plane),
        )
        return await resources.get_resource(context, revision.template_id)


def register_capability_assignment_template_export_control_plane(
    control_plane: ControlPlane,
    exporter: CapabilityAssignmentTemplateExporter,
) -> None:
    register_capability_assignment_resource_control_plane(control_plane, exporter.assignments)
    control_plane.register_command(
        CAPABILITY_ASSIGNMENT_TEMPLATE_EXPORT_COMMAND,
        CapabilityAssignmentTemplateExportCommand(control_plane, exporter),
    )


def _assignment_access(context: RequestContext) -> CapabilityAssignmentAccessContext:
    return CapabilityAssignmentAccessContext(
        actor=_actor_identity(context),
        operation=OperationContext(
            correlation_id=context.correlation_id,
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


def _actor_owner(context: RequestContext) -> OwnerRef:
    if context.actor.owner_type is not None and context.actor.owner_id is not None:
        return OwnerRef(type=context.actor.owner_type, id=context.actor.owner_id)
    return OwnerRef(type="service", id=context.actor.principal_ref)


def _required_string(payload: dict[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-blank string")
    return value


def _optional_string(payload: dict[str, JsonValue], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-blank string")
    return value


def _optional_positive_int(payload: dict[str, JsonValue], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a positive integer")
    return value
