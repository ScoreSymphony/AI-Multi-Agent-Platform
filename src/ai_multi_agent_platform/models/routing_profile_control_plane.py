"""Control Plane lifecycle surface for canonical durable model-routing profiles."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.domain import OwnerRef, Provenance

from .routing_profile_service import ModelRoutingProfileService
from .routing_profiles import (
    ModelRoutingProfileDefinition,
    ModelRoutingProfilePolicy,
    ModelRoutingProfileRef,
    ModelRoutingProfileRevision,
    RoutingProfileFallbackPolicy,
)
from .types import RoutingRequirements

MODEL_ROUTING_PROFILE_COLLECTION = "model-routing-profiles"
MODEL_ROUTING_PROFILE_COMMANDS = (
    "model-routing-profile.create",
    "model-routing-profile.version",
    "model-routing-profile.enable",
    "model-routing-profile.disable",
)


class ModelRoutingProfileResourceService:
    """Authorized read projection for stable profiles and exact immutable revisions."""

    search_indexable = False

    def __init__(self, service: ModelRoutingProfileService) -> None:
        self.service = service

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        project_filter = None
        if query.filters is not None:
            project_filter = query.filters.get("project_id")
        resources: list[dict[str, JsonValue]] = []
        for definition in self.service.repository.list_definitions():
            if project_filter is not None and definition.project_id != project_filter:
                continue
            try:
                revision = await self.service.get_revision(
                    ModelRoutingProfileRef(
                        definition.profile_id,
                        definition.current_revision,
                    ),
                    principal_ref=context.actor.principal_ref,
                    context=_operation_context(
                        context,
                        owner_ref=definition.owner_ref,
                        project_id=definition.project_id,
                    ),
                    actor_type=context.actor.actor_type,
                )
            except ContractError as exc:
                if exc.code is ErrorCode.FORBIDDEN:
                    continue
                raise
            resources.append(_profile_resource(definition, revision))
        return tuple(resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        ref, definition = _resolve_ref(self.service, resource_id)
        revision = await self.service.get_revision(
            ref,
            principal_ref=context.actor.principal_ref,
            context=_operation_context(
                context,
                owner_ref=definition.owner_ref,
                project_id=definition.project_id,
            ),
            actor_type=context.actor.actor_type,
        )
        if resource_id != definition.profile_id:
            return _exact_revision_resource(revision)
        return _profile_resource(definition, revision)


class ModelRoutingProfileCommandHandlers:
    """Lifecycle commands that always delegate writes to ModelRoutingProfileService."""

    def __init__(self, service: ModelRoutingProfileService) -> None:
        self.service = service

    async def create_profile(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_collection(resource_ref)
        owner_ref = _owner_ref(payload.get("owner_ref"), context)
        project_id = _optional_string(payload, "project_id")
        revision = await self.service.create_profile(
            name=_required_string(payload, "name"),
            description=_optional_string(payload, "description") or "",
            policy=_policy(payload.get("policy")),
            owner_ref=owner_ref,
            project_id=project_id,
            profile_id=_optional_string(payload, "profile_id"),
            provenance=_provenance(context, "model-routing-profile.create"),
            principal_ref=context.actor.principal_ref,
            context=_operation_context(
                context,
                owner_ref=owner_ref,
                project_id=project_id,
            ),
            actor_type=context.actor.actor_type,
        )
        definition = self.service.repository.get_definition(revision.profile_id)
        return _profile_resource(definition, revision)

    async def version_profile(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        definition = _stable_definition(self.service, resource_ref)
        revision = await self.service.version_profile(
            definition.profile_id,
            name=_required_string(payload, "name"),
            description=_optional_string(payload, "description") or "",
            policy=_policy(payload.get("policy")),
            expected_revision=_required_positive_int(payload, "expected_revision"),
            provenance=_provenance(context, "model-routing-profile.version"),
            principal_ref=context.actor.principal_ref,
            context=_operation_context(
                context,
                owner_ref=definition.owner_ref,
                project_id=definition.project_id,
            ),
            actor_type=context.actor.actor_type,
        )
        updated = self.service.repository.get_definition(definition.profile_id)
        return _profile_resource(updated, revision)

    async def enable_profile(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del payload
        return await self._set_enabled(context, resource_ref, True)

    async def disable_profile(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del payload
        return await self._set_enabled(context, resource_ref, False)

    async def _set_enabled(
        self,
        context: RequestContext,
        resource_ref: str,
        enabled: bool,
    ) -> dict[str, JsonValue]:
        definition = _stable_definition(self.service, resource_ref)
        updated = await self.service.set_enabled(
            definition.profile_id,
            enabled,
            principal_ref=context.actor.principal_ref,
            context=_operation_context(
                context,
                owner_ref=definition.owner_ref,
                project_id=definition.project_id,
            ),
            actor_type=context.actor.actor_type,
        )
        revision = self.service.repository.get_revision(
            ModelRoutingProfileRef(updated.profile_id, updated.current_revision)
        )
        return _profile_resource(updated, revision)


def register_model_routing_profile_control_plane(
    control_plane: ControlPlane,
    service: ModelRoutingProfileService,
) -> None:
    """Register the canonical #309 management API on the generic Control Plane seam."""

    resources = ModelRoutingProfileResourceService(service)
    handlers = ModelRoutingProfileCommandHandlers(service)
    control_plane.register_resource_service(MODEL_ROUTING_PROFILE_COLLECTION, resources)
    control_plane.register_command(MODEL_ROUTING_PROFILE_COMMANDS[0], handlers.create_profile)
    control_plane.register_command(MODEL_ROUTING_PROFILE_COMMANDS[1], handlers.version_profile)
    control_plane.register_command(MODEL_ROUTING_PROFILE_COMMANDS[2], handlers.enable_profile)
    control_plane.register_command(MODEL_ROUTING_PROFILE_COMMANDS[3], handlers.disable_profile)


def _resolve_ref(
    service: ModelRoutingProfileService,
    value: str,
) -> tuple[ModelRoutingProfileRef, ModelRoutingProfileDefinition]:
    try:
        ref = ModelRoutingProfileRef.parse(value)
    except ValueError:
        definition = service.repository.get_definition(value)
        return (
            ModelRoutingProfileRef(definition.profile_id, definition.current_revision),
            definition,
        )
    definition = service.repository.get_definition(ref.profile_id)
    return ref, definition


def _stable_definition(
    service: ModelRoutingProfileService,
    resource_ref: str,
) -> ModelRoutingProfileDefinition:
    try:
        ModelRoutingProfileRef.parse(resource_ref)
    except ValueError:
        return service.repository.get_definition(resource_ref)
    raise ContractError(
        ErrorCode.INVALID_REQUEST,
        "routing profile lifecycle commands require a stable profile ID, not an exact revision",
        details={"resource_ref": resource_ref},
    )


def _require_collection(resource_ref: str) -> None:
    if resource_ref != MODEL_ROUTING_PROFILE_COLLECTION:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"resource_ref must be {MODEL_ROUTING_PROFILE_COLLECTION!r} for profile creation",
        )


def _operation_context(
    context: RequestContext,
    *,
    owner_ref: OwnerRef,
    project_id: str | None,
) -> OperationContext:
    return OperationContext(
        correlation_id=context.correlation_id,
        causation_id=context.request_id,
        owner_type=owner_ref.type,
        owner_id=owner_ref.id,
        project_id=project_id,
    )


def _owner_ref(value: JsonValue | None, context: RequestContext) -> OwnerRef:
    if value is not None:
        data = _object(value, "owner_ref")
        owner_type = _required_string(data, "type")
        owner_id = _required_string(data, "id")
        if owner_type not in {"user", "organization", "team", "service"}:
            raise ContractError(ErrorCode.INVALID_REQUEST, "owner_ref.type is not supported")
        typed_owner_type = cast(
            Literal["user", "organization", "team", "service"],
            owner_type,
        )
        return OwnerRef(type=typed_owner_type, id=owner_id)
    if context.actor.owner_type is None or context.actor.owner_id is None:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "routing profile creation requires an authenticated owner context or owner_ref",
        )
    return OwnerRef(type=context.actor.owner_type, id=context.actor.owner_id)


def _policy(value: JsonValue | None) -> ModelRoutingProfilePolicy:
    if value is None:
        return ModelRoutingProfilePolicy()
    data = _object(value, "policy")
    requirements_data = _object(data.get("requirements", {}), "policy.requirements")
    try:
        return ModelRoutingProfilePolicy(
            requirements=RoutingRequirements(
                explicit_model_id=_optional_string(requirements_data, "explicit_model_id"),
                min_context_window=_optional_positive_int(requirements_data, "min_context_window"),
                tool_calling=_boolean(requirements_data, "tool_calling", default=False),
                structured_output=_boolean(requirements_data, "structured_output", default=False),
                streaming=_boolean(requirements_data, "streaming", default=False),
                modalities=_string_tuple(requirements_data, "modalities"),
                reasoning=_string_tuple(requirements_data, "reasoning"),
                local_only=_boolean(requirements_data, "local_only", default=False),
                self_hosted_only=_boolean(requirements_data, "self_hosted_only", default=False),
            ),
            preferred_model_ids=_string_tuple(data, "preferred_model_ids"),
            fallback=RoutingProfileFallbackPolicy(
                _optional_string(data, "fallback") or RoutingProfileFallbackPolicy.ROUTE.value
            ),
        )
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"invalid routing profile policy: {exc}",
        ) from exc


def _profile_resource(
    definition: ModelRoutingProfileDefinition,
    revision: ModelRoutingProfileRevision,
) -> dict[str, JsonValue]:
    return {
        "id": definition.profile_id,
        "profile_id": definition.profile_id,
        "exact_ref": revision.ref.canonical_ref,
        "current_revision": definition.current_revision,
        "enabled": definition.enabled,
        "owner_ref": {"type": definition.owner_ref.type, "id": definition.owner_ref.id},
        "project_id": definition.project_id,
        "created_at": definition.created_at.isoformat(),
        "updated_at": definition.updated_at.isoformat(),
        "schema_version": definition.schema_version,
        "revision": _revision_payload(revision),
    }


def _exact_revision_resource(revision: ModelRoutingProfileRevision) -> dict[str, JsonValue]:
    """Serialize an exact ref only from immutable revision-owned state."""

    return {
        "id": revision.ref.canonical_ref,
        "profile_id": revision.profile_id,
        "exact_ref": revision.ref.canonical_ref,
        "owner_ref": {"type": revision.owner_ref.type, "id": revision.owner_ref.id},
        "project_id": revision.project_id,
        "schema_version": revision.schema_version,
        "revision": _revision_payload(revision),
    }


def _revision_payload(revision: ModelRoutingProfileRevision) -> dict[str, JsonValue]:
    return {
        "revision": revision.revision,
        "name": revision.name,
        "description": revision.description,
        "created_at": revision.created_at.isoformat(),
        "schema_version": revision.schema_version,
        "provenance": _provenance_json(revision.provenance),
        "policy": {
            "requirements": _requirements_json(revision.policy.requirements),
            "preferred_model_ids": list(revision.policy.preferred_model_ids),
            "fallback": revision.policy.fallback.value,
        },
    }


def _requirements_json(value: RoutingRequirements) -> dict[str, JsonValue]:
    return {
        "explicit_model_id": value.explicit_model_id,
        "min_context_window": value.min_context_window,
        "tool_calling": value.tool_calling,
        "structured_output": value.structured_output,
        "streaming": value.streaming,
        "modalities": list(value.modalities),
        "reasoning": list(value.reasoning),
        "local_only": value.local_only,
        "self_hosted_only": value.self_hosted_only,
    }


def _provenance(context: RequestContext, action: str) -> Provenance:
    return Provenance(
        source="control-plane",
        actor_ref=context.actor.principal_ref,
        details={"action": action, "request_id": context.request_id},
    )


def _provenance_json(value: Provenance | None) -> JsonValue:
    if value is None:
        return None
    return {
        "source": value.source,
        "actor_ref": value.actor_ref,
        "details": dict(value.details),
    }


def _object(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field} must be an object")
    return dict(value)


def _required_string(value: Mapping[str, object], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field} must be a non-blank string")
    return item


def _optional_string(value: Mapping[str, object], field: str) -> str | None:
    item = value.get(field)
    if item is None:
        return None
    if not isinstance(item, str) or not item.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{field} must be a non-blank string when provided",
        )
    return item


def _required_positive_int(value: Mapping[str, object], field: str) -> int:
    item = value.get(field)
    if isinstance(item, bool) or not isinstance(item, int) or item < 1:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field} must be a positive integer")
    return item


def _optional_positive_int(value: Mapping[str, object], field: str) -> int | None:
    item = value.get(field)
    if item is None:
        return None
    if isinstance(item, bool) or not isinstance(item, int) or item < 1:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{field} must be a positive integer when provided",
        )
    return item


def _boolean(value: Mapping[str, object], field: str, *, default: bool) -> bool:
    item = value.get(field, default)
    if not isinstance(item, bool):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field} must be a boolean")
    return item


def _string_tuple(value: Mapping[str, object], field: str) -> tuple[str, ...]:
    item = value.get(field, [])
    if not isinstance(item, list) or any(not isinstance(entry, str) for entry in item):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field} must be a list of strings")
    return tuple(cast(list[str], item))


__all__ = [
    "MODEL_ROUTING_PROFILE_COLLECTION",
    "MODEL_ROUTING_PROFILE_COMMANDS",
    "ModelRoutingProfileCommandHandlers",
    "ModelRoutingProfileResourceService",
    "register_model_routing_profile_control_plane",
]
