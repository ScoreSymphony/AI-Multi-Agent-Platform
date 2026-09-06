"""Authorized Control Plane lifecycle surface for canonical model-routing profiles."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane, ResourceService
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
    "model-routing-profile:create",
    "model-routing-profile:version",
    "model-routing-profile:enable",
    "model-routing-profile:disable",
)


class ModelRoutingProfileResourceService(ResourceService):
    """Expose current profile state plus exact immutable revision reads."""

    def __init__(self, service: ModelRoutingProfileService) -> None:
        self.service = service

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        project_id = None if query.filters is None else query.filters.get("project_id")
        definitions = await self.service.list_profiles(
            principal_ref=context.actor.principal_ref,
            context=_operation_context(context, project_id=project_id),
            actor_type=_actor_type(context),
        )
        resources: list[dict[str, JsonValue]] = []
        for definition in definitions:
            current = await self.service.get_revision(
                ModelRoutingProfileRef(definition.profile_id, definition.current_revision),
                principal_ref=context.actor.principal_ref,
                context=_operation_context(context, project_id=definition.project_id),
                actor_type=_actor_type(context),
            )
            resources.append(_profile_resource(definition, current))
        return tuple(resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        if "@r" in resource_id:
            try:
                ref = ModelRoutingProfileRef.parse(resource_id)
            except ValueError as exc:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "routing-profile revision resource must use '<profile_id>@r<revision>'",
                ) from exc
            definition = self.service.repository.get_definition(ref.profile_id)
            revision = await self.service.get_revision(
                ref,
                principal_ref=context.actor.principal_ref,
                context=_operation_context(context, project_id=definition.project_id),
                actor_type=_actor_type(context),
            )
            return _revision_resource(revision)

        definition = self.service.repository.get_definition(resource_id)
        current = await self.service.get_revision(
            ModelRoutingProfileRef(definition.profile_id, definition.current_revision),
            principal_ref=context.actor.principal_ref,
            context=_operation_context(context, project_id=definition.project_id),
            actor_type=_actor_type(context),
        )
        return _profile_resource(definition, current)


class ModelRoutingProfileCommandHandlers:
    """Mutating northbound handlers that delegate every write to the domain service."""

    def __init__(self, service: ModelRoutingProfileService) -> None:
        self.service = service

    async def create_profile(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_collection(resource_ref)
        _reject_unknown(
            payload,
            {"name", "description", "owner_ref", "project_id", "policy", "profile_id"},
        )
        owner_ref = _owner_ref(payload.get("owner_ref"), context)
        project_id = _optional_string(payload, "project_id")
        revision = await self.service.create_profile(
            name=_required_string(payload, "name"),
            description=_optional_string(payload, "description") or "",
            policy=_policy(_required(payload, "policy")),
            owner_ref=owner_ref,
            principal_ref=context.actor.principal_ref,
            context=_operation_context(context, project_id=project_id),
            actor_type=_actor_type(context),
            project_id=project_id,
            provenance=_provenance(context, "model-routing-profile:create"),
            profile_id=_optional_string(payload, "profile_id"),
        )
        definition = self.service.repository.get_definition(revision.profile_id)
        return _profile_resource(definition, revision)

    async def version_profile(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _reject_unknown(payload, {"name", "description", "policy", "expected_revision"})
        definition = self.service.repository.get_definition(resource_ref)
        revision = await self.service.version_profile(
            resource_ref,
            name=_required_string(payload, "name"),
            description=_optional_string(payload, "description") or "",
            policy=_policy(_required(payload, "policy")),
            principal_ref=context.actor.principal_ref,
            context=_operation_context(context, project_id=definition.project_id),
            actor_type=_actor_type(context),
            expected_revision=_optional_positive_int(payload, "expected_revision"),
            provenance=_provenance(context, "model-routing-profile:version"),
        )
        updated = self.service.repository.get_definition(resource_ref)
        return _profile_resource(updated, revision)

    async def enable_profile(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await self._set_enabled(context, resource_ref, payload, enabled=True)

    async def disable_profile(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await self._set_enabled(context, resource_ref, payload, enabled=False)

    async def _set_enabled(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
        *,
        enabled: bool,
    ) -> dict[str, JsonValue]:
        _reject_unknown(payload, set())
        definition = self.service.repository.get_definition(resource_ref)
        updated = await self.service.set_enabled(
            resource_ref,
            enabled,
            principal_ref=context.actor.principal_ref,
            context=_operation_context(context, project_id=definition.project_id),
            actor_type=_actor_type(context),
        )
        current = self.service.repository.get_revision(
            ModelRoutingProfileRef(updated.profile_id, updated.current_revision)
        )
        return _profile_resource(updated, current)


def register_model_routing_profile_control_plane(
    control_plane: ControlPlane,
    service: ModelRoutingProfileService,
) -> None:
    """Register the canonical #309 management surface on the generic #32 seam."""

    control_plane.register_resource_service(
        MODEL_ROUTING_PROFILE_COLLECTION,
        ModelRoutingProfileResourceService(service),
    )
    handlers = ModelRoutingProfileCommandHandlers(service)
    control_plane.register_command("model-routing-profile:create", handlers.create_profile)
    control_plane.register_command("model-routing-profile:version", handlers.version_profile)
    control_plane.register_command("model-routing-profile:enable", handlers.enable_profile)
    control_plane.register_command("model-routing-profile:disable", handlers.disable_profile)


def _operation_context(
    context: RequestContext,
    *,
    project_id: str | None,
) -> OperationContext:
    return OperationContext(
        correlation_id=context.correlation_id,
        causation_id=context.request_id,
        owner_type=context.actor.owner_type,
        owner_id=context.actor.owner_id,
        project_id=project_id,
    )


def _actor_type(context: RequestContext) -> str:
    return context.actor.actor_type or "service"


def _owner_ref(value: object | None, context: RequestContext) -> OwnerRef:
    if value is None:
        if context.actor.owner_type is None or context.actor.owner_id is None:
            raise ValueError("owner_ref is required when actor owner context is unavailable")
        return OwnerRef(type=context.actor.owner_type, id=context.actor.owner_id)
    data = _mapping(value, "owner_ref")
    raw_type = _required_string(data, "type")
    if raw_type not in {"user", "organization", "team", "service"}:
        raise ValueError("owner_ref.type must be user, organization, team or service")
    owner_type = cast(Literal["user", "organization", "team", "service"], raw_type)
    return OwnerRef(type=owner_type, id=_required_string(data, "id"))


def _policy(value: object) -> ModelRoutingProfilePolicy:
    data = _mapping(value, "policy")
    _reject_unknown_mapping(data, {"requirements", "preferred_model_ids", "fallback"}, "policy")
    fallback_raw = _optional_string(data, "fallback") or RoutingProfileFallbackPolicy.ROUTE.value
    try:
        fallback = RoutingProfileFallbackPolicy(fallback_raw)
    except ValueError as exc:
        raise ValueError("policy.fallback must be 'fail' or 'route'") from exc
    return ModelRoutingProfilePolicy(
        requirements=_requirements(data.get("requirements", {})),
        preferred_model_ids=_string_tuple(data, "preferred_model_ids"),
        fallback=fallback,
    )


def _requirements(value: object) -> RoutingRequirements:
    data = _mapping(value, "policy.requirements")
    _reject_unknown_mapping(
        data,
        {
            "explicit_model_id",
            "min_context_window",
            "tool_calling",
            "structured_output",
            "streaming",
            "modalities",
            "reasoning",
            "local_only",
            "self_hosted_only",
        },
        "policy.requirements",
    )
    return RoutingRequirements(
        explicit_model_id=_optional_string(data, "explicit_model_id"),
        min_context_window=_optional_positive_int(data, "min_context_window"),
        tool_calling=_boolean(data, "tool_calling", False),
        structured_output=_boolean(data, "structured_output", False),
        streaming=_boolean(data, "streaming", False),
        modalities=_string_tuple(data, "modalities"),
        reasoning=_string_tuple(data, "reasoning"),
        local_only=_boolean(data, "local_only", False),
        self_hosted_only=_boolean(data, "self_hosted_only", False),
    )


def _profile_resource(
    definition: ModelRoutingProfileDefinition,
    revision: ModelRoutingProfileRevision,
) -> dict[str, JsonValue]:
    return {
        "id": definition.profile_id,
        "profile_id": definition.profile_id,
        "owner_ref": _owner_json(definition.owner_ref),
        "project_id": definition.project_id,
        "current_revision": definition.current_revision,
        "current_revision_ref": revision.ref.canonical_ref,
        "enabled": definition.enabled,
        "created_at": definition.created_at.isoformat(),
        "updated_at": definition.updated_at.isoformat(),
        "schema_version": definition.schema_version,
        "revision": _revision_payload(revision),
    }


def _revision_resource(revision: ModelRoutingProfileRevision) -> dict[str, JsonValue]:
    return {"id": revision.ref.canonical_ref, **_revision_payload(revision)}


def _revision_payload(revision: ModelRoutingProfileRevision) -> dict[str, JsonValue]:
    provenance: JsonValue = None
    if revision.provenance is not None:
        provenance = {
            "source": revision.provenance.source,
            "actor_ref": revision.provenance.actor_ref,
            "details": cast(dict[str, JsonValue], dict(revision.provenance.details)),
        }
    return {
        "profile_id": revision.profile_id,
        "revision": revision.revision,
        "ref": revision.ref.canonical_ref,
        "name": revision.name,
        "description": revision.description,
        "owner_ref": _owner_json(revision.owner_ref),
        "project_id": revision.project_id,
        "policy": _policy_json(revision.policy),
        "provenance": provenance,
        "created_at": revision.created_at.isoformat(),
        "schema_version": revision.schema_version,
    }


def _policy_json(policy: ModelRoutingProfilePolicy) -> dict[str, JsonValue]:
    requirements = policy.requirements
    return {
        "requirements": {
            "explicit_model_id": requirements.explicit_model_id,
            "min_context_window": requirements.min_context_window,
            "tool_calling": requirements.tool_calling,
            "structured_output": requirements.structured_output,
            "streaming": requirements.streaming,
            "modalities": list(requirements.modalities),
            "reasoning": list(requirements.reasoning),
            "local_only": requirements.local_only,
            "self_hosted_only": requirements.self_hosted_only,
        },
        "preferred_model_ids": list(policy.preferred_model_ids),
        "fallback": policy.fallback.value,
    }


def _owner_json(owner: OwnerRef) -> dict[str, JsonValue]:
    return {"type": owner.type, "id": owner.id}


def _provenance(context: RequestContext, operation: str) -> Provenance:
    return Provenance(
        source="control-plane",
        actor_ref=context.actor.principal_ref,
        details={
            "operation": operation,
            "request_id": context.request_id,
            "correlation_id": context.correlation_id,
        },
    )


def _require_collection(resource_ref: str) -> None:
    if resource_ref != MODEL_ROUTING_PROFILE_COLLECTION:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"create command resource_ref must be {MODEL_ROUTING_PROFILE_COLLECTION!r}",
        )


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return cast(Mapping[str, object], value)


def _required(mapping: Mapping[str, object], name: str) -> object:
    if name not in mapping:
        raise ValueError(f"{name} is required")
    return mapping[name]


def _required_string(mapping: Mapping[str, object], name: str) -> str:
    value = mapping.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    return value


def _optional_string(mapping: Mapping[str, object], name: str) -> str | None:
    value = mapping.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string or null")
    return value


def _boolean(mapping: Mapping[str, object], name: str, default: bool) -> bool:
    value = mapping.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be boolean")
    return value


def _optional_positive_int(mapping: Mapping[str, object], name: str) -> int | None:
    value = mapping.get(name)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be an integer >= 1 or null")
    return value


def _string_tuple(mapping: Mapping[str, object], name: str) -> tuple[str, ...]:
    value = mapping.get(name, [])
    if not isinstance(value, list | tuple) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{name} must be an array of strings")
    items = tuple(cast(str, item) for item in value)
    if any(not item.strip() for item in items):
        raise ValueError(f"{name} must not contain blank strings")
    return items


def _reject_unknown(payload: Mapping[str, object], allowed: set[str]) -> None:
    _reject_unknown_mapping(payload, allowed, "command payload")


def _reject_unknown_mapping(
    mapping: Mapping[str, object],
    allowed: set[str],
    name: str,
) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise ValueError(f"{name} contains unsupported fields: {', '.join(unknown)}")


__all__ = [
    "MODEL_ROUTING_PROFILE_COLLECTION",
    "MODEL_ROUTING_PROFILE_COMMANDS",
    "ModelRoutingProfileCommandHandlers",
    "ModelRoutingProfileResourceService",
    "register_model_routing_profile_control_plane",
]
