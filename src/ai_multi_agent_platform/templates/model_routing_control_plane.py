"""Control Plane composition for Model Routing Profile -> Template export."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.domain import OwnerRef

from .access import TemplateScopeAccess
from .control_plane import TEMPLATE_COLLECTION, TemplateResourceService
from .model_routing_exporter import ModelRoutingPolicyTemplateExporter

MODEL_ROUTING_POLICY_TEMPLATE_EXPORT_COMMAND = "template.create-from-model-routing-profile"


@dataclass(slots=True)
class ModelRoutingPolicyTemplateExportCommand:
    """Authorize the canonical #309 source revision before creating a Template draft."""

    control_plane: ControlPlane
    exporter: ModelRoutingPolicyTemplateExporter

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
        profile_id = _required_string(payload, "profile_id")
        revision = await self.exporter.create_from_profile(
            profile_id,
            principal_ref=context.actor.principal_ref,
            actor_type=context.actor.actor_type,
            correlation_id=context.correlation_id,
            causation_id=context.request_id,
            owner_ref=_actor_owner(context),
            author=context.actor.principal_ref,
            revision=_optional_positive_int(payload, "revision"),
            name=_optional_string(payload, "name"),
        )
        resources = TemplateResourceService(
            self.exporter.templates.repository,
            scope_access=TemplateScopeAccess(self.control_plane),
        )
        return await resources.get_resource(context, revision.template_id)


def register_model_routing_policy_template_export_control_plane(
    control_plane: ControlPlane,
    exporter: ModelRoutingPolicyTemplateExporter,
) -> None:
    control_plane.register_command(
        MODEL_ROUTING_POLICY_TEMPLATE_EXPORT_COMMAND,
        ModelRoutingPolicyTemplateExportCommand(control_plane, exporter),
    )


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
