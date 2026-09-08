"""Safe Control Plane lifecycle surface for canonical EgressProfile configuration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast

from ai_multi_agent_platform.contracts import (
    ContractError,
    DataClassification,
    EgressCostClass,
    EgressProfile,
    EgressProfileTrust,
    EgressTargetKind,
    EgressTargetPosture,
    ErrorCode,
    JsonValue,
    OperationContext,
)
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.domain import OwnerRef

from .egress_profiles import (
    EgressProfileDefinition,
    EgressProfileService,
    new_egress_profile_id,
)

EGRESS_PROFILE_COLLECTION = "egress-profiles"
EGRESS_PROFILE_COMMANDS = (
    "egress-profile.create",
    "egress-profile.version",
    "egress-profile.verify",
    "egress-profile.enable",
    "egress-profile.disable",
)


class EgressProfileResourceService:
    """Authorized safe projection of stable profiles and immutable revisions."""

    search_indexable = False

    def __init__(self, service: EgressProfileService) -> None:
        self.service = service

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        requested_project = None
        if query.filters is not None:
            raw_project = query.filters.get("project_id")
            if raw_project is not None and not isinstance(raw_project, str):
                raise ContractError(ErrorCode.INVALID_REQUEST, "project_id filter must be a string")
            requested_project = cast(str | None, raw_project)
        operation = _operation_context(context, project_id=requested_project)
        definitions = await self.service.list_profiles(
            principal_ref=context.actor.principal_ref,
            context=operation,
            actor_type=context.actor.actor_type,
        )
        resources: list[dict[str, JsonValue]] = []
        for definition in definitions:
            if requested_project is not None and definition.project_id != requested_project:
                continue
            revision = await self.service.get_revision(
                definition.profile_id,
                definition.current_revision,
                principal_ref=context.actor.principal_ref,
                context=_operation_context(context, project_id=definition.project_id),
                actor_type=context.actor.actor_type,
            )
            resources.append(_profile_resource(definition, revision))
        return tuple(resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        profile_id, revision_number = _parse_resource_ref(resource_id)
        definition = self.service.repository.get_definition(profile_id)
        revision = await self.service.get_revision(
            profile_id,
            revision_number or definition.current_revision,
            principal_ref=context.actor.principal_ref,
            context=_operation_context(context, project_id=definition.project_id),
            actor_type=context.actor.actor_type,
        )
        if revision_number is not None:
            return _revision_resource(definition, revision)
        return _profile_resource(definition, revision)


class EgressProfileCommandHandlers:
    """Lifecycle mutations delegated to EgressProfileService authorization and invariants."""

    def __init__(self, service: EgressProfileService) -> None:
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
        profile_id = _optional_string(payload, "profile_id") or new_egress_profile_id()
        revision = _profile_from_payload(payload, profile_id=profile_id, revision=1)
        created = await self.service.create_profile(
            revision,
            owner_ref=owner_ref,
            project_id=project_id,
            principal_ref=context.actor.principal_ref,
            context=_operation_context(context, project_id=project_id, owner_ref=owner_ref),
            actor_type=context.actor.actor_type,
        )
        definition = self.service.repository.get_definition(created.profile_id)
        return _profile_resource(definition, created)

    async def version_profile(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        profile_id, exact_revision = _parse_resource_ref(resource_ref)
        if exact_revision is not None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "egress profile lifecycle commands require a stable profile ID",
            )
        definition = self.service.repository.get_definition(profile_id)
        expected_revision = _required_positive_int(payload, "expected_revision")
        revision = _profile_from_payload(
            payload,
            profile_id=profile_id,
            revision=expected_revision + 1,
            stable_target=definition,
        )
        updated = await self.service.version_profile(
            revision,
            expected_revision=expected_revision,
            principal_ref=context.actor.principal_ref,
            context=_operation_context(context, project_id=definition.project_id),
            actor_type=context.actor.actor_type,
        )
        return _profile_resource(self.service.repository.get_definition(profile_id), updated)

    async def verify_profile(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        profile_id, exact_revision = _parse_resource_ref(resource_ref)
        if exact_revision is not None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "egress profile verification requires a stable profile ID",
            )
        definition = self.service.repository.get_definition(profile_id)
        verified = await self.service.verify_profile(
            profile_id,
            expected_revision=_required_positive_int(payload, "expected_revision"),
            verification_ref=_required_string(payload, "verification_ref"),
            principal_ref=context.actor.principal_ref,
            context=_operation_context(context, project_id=definition.project_id),
            actor_type=context.actor.actor_type,
        )
        return _profile_resource(self.service.repository.get_definition(profile_id), verified)

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
        profile_id, exact_revision = _parse_resource_ref(resource_ref)
        if exact_revision is not None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "egress profile enable/disable requires a stable profile ID",
            )
        definition = self.service.repository.get_definition(profile_id)
        updated = await self.service.set_enabled(
            profile_id,
            enabled,
            principal_ref=context.actor.principal_ref,
            context=_operation_context(context, project_id=definition.project_id),
            actor_type=context.actor.actor_type,
        )
        revision = self.service.repository.get_revision(profile_id, updated.current_revision)
        return _profile_resource(updated, revision)


def register_egress_profile_control_plane(
    control_plane: ControlPlane,
    service: EgressProfileService,
) -> None:
    resources = EgressProfileResourceService(service)
    commands = EgressProfileCommandHandlers(service)
    control_plane.register_resource_service(EGRESS_PROFILE_COLLECTION, resources)
    control_plane.register_command(EGRESS_PROFILE_COMMANDS[0], commands.create_profile)
    control_plane.register_command(EGRESS_PROFILE_COMMANDS[1], commands.version_profile)
    control_plane.register_command(EGRESS_PROFILE_COMMANDS[2], commands.verify_profile)
    control_plane.register_command(EGRESS_PROFILE_COMMANDS[3], commands.enable_profile)
    control_plane.register_command(EGRESS_PROFILE_COMMANDS[4], commands.disable_profile)


def _profile_from_payload(
    payload: Mapping[str, JsonValue],
    *,
    profile_id: str,
    revision: int,
    stable_target: EgressProfileDefinition | None = None,
) -> EgressProfile:
    target_kind = (
        stable_target.target_kind
        if stable_target is not None
        else _enum(payload, "target_kind", EgressTargetKind)
    )
    target_id = (
        stable_target.target_id
        if stable_target is not None
        else _required_string(payload, "target_id")
    )
    trust = _optional_enum(
        payload,
        "trust",
        EgressProfileTrust,
        default=EgressProfileTrust.CONFIGURED,
    )
    if trust is EgressProfileTrust.VERIFIED:
        raise ContractError(
            ErrorCode.FORBIDDEN,
            "verified trust cannot be asserted by create/version; use egress-profile.verify",
        )
    metadata: dict[str, JsonValue] = {}
    allow_sensitive = payload.get("allow_sensitive_external")
    if allow_sensitive is not None:
        if not isinstance(allow_sensitive, bool):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "allow_sensitive_external must be a boolean",
            )
        metadata["allow_sensitive_external"] = allow_sensitive
    try:
        return EgressProfile(
            profile_id=profile_id,
            revision=revision,
            target_kind=target_kind,
            target_id=target_id,
            posture=_enum(payload, "posture", EgressTargetPosture),
            allowed_classifications=_classification_tuple(payload, "allowed_classifications"),
            denied_classifications=_classification_tuple(payload, "denied_classifications"),
            network_egress_required=_optional_bool(payload, "network_egress_required"),
            data_retention_policy=_optional_string(payload, "data_retention_policy"),
            training_policy=_optional_string(payload, "training_policy"),
            logging_policy=_optional_string(payload, "logging_policy"),
            jurisdiction=_optional_string(payload, "jurisdiction"),
            cost_class=_optional_enum(
                payload,
                "cost_class",
                EgressCostClass,
                default=EgressCostClass.UNKNOWN,
            ),
            credential_required=_optional_bool(payload, "credential_required"),
            policy_source=_optional_string(payload, "policy_source") or "control-plane",
            source_revision=_required_string(payload, "source_revision"),
            trust=trust,
            metadata=metadata,
        )
    except ValueError as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"invalid egress profile: {exc}") from exc


def _profile_resource(
    definition: EgressProfileDefinition,
    revision: EgressProfile,
) -> dict[str, JsonValue]:
    return {
        "id": definition.profile_id,
        "profile_id": definition.profile_id,
        "exact_ref": revision.canonical_ref,
        "current_revision": definition.current_revision,
        "enabled": definition.enabled,
        "owner_ref": {"type": definition.owner_ref.type, "id": definition.owner_ref.id},
        "project_id": definition.project_id,
        "created_at": definition.created_at.isoformat(),
        "updated_at": definition.updated_at.isoformat(),
        "schema_version": definition.schema_version,
        "revision": _safe_revision_payload(revision),
    }


def _revision_resource(
    definition: EgressProfileDefinition,
    revision: EgressProfile,
) -> dict[str, JsonValue]:
    return {
        "id": revision.canonical_ref,
        "profile_id": definition.profile_id,
        "exact_ref": revision.canonical_ref,
        "project_id": definition.project_id,
        "schema_version": definition.schema_version,
        "revision": _safe_revision_payload(revision),
    }


def _safe_revision_payload(profile: EgressProfile) -> dict[str, JsonValue]:
    """Never project arbitrary metadata values into operator-facing read APIs."""

    allow_sensitive = profile.metadata.get("allow_sensitive_external")
    return {
        "revision": profile.revision,
        "target_kind": profile.target_kind.value,
        "target_id": profile.target_id,
        "posture": profile.posture.value,
        "allowed_classifications": [item.value for item in profile.allowed_classifications],
        "denied_classifications": [item.value for item in profile.denied_classifications],
        "network_egress_required": profile.network_egress_required,
        "data_retention_policy": profile.data_retention_policy,
        "training_policy": profile.training_policy,
        "logging_policy": profile.logging_policy,
        "jurisdiction": profile.jurisdiction,
        "cost_class": profile.cost_class.value,
        "credential_required": profile.credential_required,
        "policy_source": profile.policy_source,
        "source_revision": profile.source_revision,
        "trust": profile.trust.value,
        "allow_sensitive_external": (
            allow_sensitive if isinstance(allow_sensitive, bool) else False
        ),
        "metadata_keys": sorted(profile.metadata),
        "schema_version": profile.schema_version,
    }


def _parse_resource_ref(value: str) -> tuple[str, int | None]:
    profile_id, separator, raw_revision = value.rpartition("@")
    if not separator:
        if not value.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "egress profile ID must not be blank")
        return value, None
    if not profile_id.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, "egress profile ID must not be blank")
    try:
        revision = int(raw_revision)
    except ValueError as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, "egress profile revision ref is invalid") from exc
    if revision < 1:
        raise ContractError(ErrorCode.INVALID_REQUEST, "egress profile revision must be positive")
    return profile_id, revision


def _require_collection(resource_ref: str) -> None:
    if resource_ref != EGRESS_PROFILE_COLLECTION:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"resource_ref must be {EGRESS_PROFILE_COLLECTION!r} for profile creation",
        )


def _operation_context(
    context: RequestContext,
    *,
    project_id: str | None,
    owner_ref: OwnerRef | None = None,
) -> OperationContext:
    return OperationContext(
        correlation_id=context.correlation_id,
        causation_id=context.request_id,
        owner_type=(owner_ref.type if owner_ref is not None else context.actor.owner_type),
        owner_id=(owner_ref.id if owner_ref is not None else context.actor.owner_id),
        project_id=project_id,
    )


def _owner_ref(value: JsonValue | None, context: RequestContext) -> OwnerRef:
    if value is None:
        if context.actor.owner_type is None or context.actor.owner_id is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "egress profile creation requires an authenticated owner context or owner_ref",
            )
        return OwnerRef(type=context.actor.owner_type, id=context.actor.owner_id)
    item = _object(value, "owner_ref")
    owner_type = _required_string(item, "type")
    if owner_type not in {"user", "organization", "team", "service"}:
        raise ContractError(ErrorCode.INVALID_REQUEST, "owner_ref.type is not supported")
    return OwnerRef(
        type=cast(Literal["user", "organization", "team", "service"], owner_type),
        id=_required_string(item, "id"),
    )


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


def _optional_bool(value: Mapping[str, object], field: str) -> bool | None:
    item = value.get(field)
    if item is None:
        return None
    if not isinstance(item, bool):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field} must be a boolean when provided")
    return item


def _classification_tuple(
    value: Mapping[str, object],
    field: str,
) -> tuple[DataClassification, ...]:
    item = value.get(field, [])
    if not isinstance(item, list) or any(not isinstance(entry, str) for entry in item):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field} must be a list of strings")
    try:
        return tuple(DataClassification(cast(str, entry)) for entry in item)
    except ValueError as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field} contains an unknown classification") from exc


def _enum[T](value: Mapping[str, object], field: str, enum_type: type[T]) -> T:
    item = _required_string(value, field)
    try:
        return enum_type(item)
    except ValueError as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field} contains an unknown value") from exc


def _optional_enum[T](
    value: Mapping[str, object],
    field: str,
    enum_type: type[T],
    *,
    default: T,
) -> T:
    item = value.get(field)
    if item is None:
        return default
    if not isinstance(item, str):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field} must be a string")
    try:
        return enum_type(item)
    except ValueError as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field} contains an unknown value") from exc


__all__ = [
    "EGRESS_PROFILE_COLLECTION",
    "EGRESS_PROFILE_COMMANDS",
    "EgressProfileCommandHandlers",
    "EgressProfileResourceService",
    "register_egress_profile_control_plane",
]
