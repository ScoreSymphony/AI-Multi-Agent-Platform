"""Portable canonical authorization-policy profiles for issue #310."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.security.policy_profile_persistence import (
    policy_profile_revision_from_json,
    policy_profile_revision_to_json,
)
from ai_multi_agent_platform.security.policy_profiles import (
    AuthorizationPolicyConditions,
    AuthorizationPolicyProfileContent,
    AuthorizationPolicyProfileDefinition,
    AuthorizationPolicyProfileRepository,
    AuthorizationPolicyProfileRevision,
    AuthorizationPolicyProvenance,
    AuthorizationPolicyScopeConstraints,
)

from .dependencies import resource_dependency
from .models import DependencyRequirement, IdPolicy, PortableResource
from .planner import ImportSecurityFinding, ImportSecurityFindingKind
from .registry import ImportContext, ResourceExport, ResourceSerializerRegistry

AUTHORIZATION_POLICY_PROFILE_PORTABLE_SCHEMA_VERSION = "1"
AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE = "authorization_policy_profile"

OwnerType = Literal["user", "organization", "team", "service"]
_OWNER_TYPES = frozenset({"user", "organization", "team", "service"})


@dataclass(frozen=True, slots=True)
class AuthorizationPolicyProfilePortableSnapshot:
    """Stable definition plus complete immutable revision history.

    Assignments are intentionally absent: a portable profile is configuration, never an
    authority grant.
    """

    definition: AuthorizationPolicyProfileDefinition
    revisions: tuple[AuthorizationPolicyProfileRevision, ...]

    def __post_init__(self) -> None:
        if not self.revisions:
            raise ValueError("portable policy profile snapshot requires revision history")
        if any(
            item.policy_profile_id != self.definition.policy_profile_id for item in self.revisions
        ):
            raise ValueError("portable policy profile revisions must match the definition")
        numbers = tuple(item.revision for item in self.revisions)
        if numbers != tuple(range(1, self.definition.current_revision + 1)):
            raise ValueError("portable policy profile revision history must be contiguous")
        if self.revisions[-1].revision != self.definition.current_revision:
            raise ValueError("portable policy profile must include the current revision")
        for revision in self.revisions:
            if (
                revision.owner_ref != self.definition.owner_ref
                or revision.project_id != self.definition.project_id
                or revision.organization_id != self.definition.organization_id
                or revision.team_id != self.definition.team_id
            ):
                raise ValueError("portable policy profile ownership scope is inconsistent")


def snapshot_authorization_policy_profile(
    repository: AuthorizationPolicyProfileRepository,
    policy_profile_id: str,
) -> AuthorizationPolicyProfilePortableSnapshot:
    return AuthorizationPolicyProfilePortableSnapshot(
        definition=repository.get_profile(policy_profile_id),
        revisions=repository.list_revisions(policy_profile_id),
    )


class AuthorizationPolicyProfilePortableCodec:
    """Serialize provider-neutral profile history without assignments/provider state."""

    resource_type = AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE

    def __init__(self, *, id_policy: IdPolicy = IdPolicy.PRESERVE) -> None:
        self.id_policy = id_policy

    def serialize(self, value: object) -> ResourceExport:
        if not isinstance(value, AuthorizationPolicyProfilePortableSnapshot):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "authorization policy profile codec requires a portable snapshot",
            )
        try:
            snapshot = AuthorizationPolicyProfilePortableSnapshot(
                value.definition,
                value.revisions,
            )
        except ValueError as exc:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "canonical authorization policy profile history is not portable",
                details={"policy_profile_id": value.definition.policy_profile_id},
            ) from exc
        return ResourceExport(
            resource_id=snapshot.definition.policy_profile_id,
            resource_version=str(snapshot.definition.current_revision),
            payload={
                "schema_version": AUTHORIZATION_POLICY_PROFILE_PORTABLE_SCHEMA_VERSION,
                "definition": _definition_to_json(snapshot.definition),
                "revisions": [
                    policy_profile_revision_to_json(revision) for revision in snapshot.revisions
                ],
            },
            id_policy=self.id_policy,
            dependencies=_profile_dependencies(snapshot),
        )

    def deserialize(self, resource: PortableResource, context: ImportContext) -> object:
        if resource.resource_type != self.resource_type:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"policy profile codec cannot deserialize {resource.resource_type!r}",
            )
        try:
            _require_schema(resource.payload)
            definition = _definition(resource.payload.get("definition"))
            revisions = tuple(
                policy_profile_revision_from_json(cast(JsonValue, item))
                for item in _array(resource.payload.get("revisions"), "policy profile revisions")
            )
            snapshot = AuthorizationPolicyProfilePortableSnapshot(definition, revisions)
            if definition.policy_profile_id != resource.resource_id:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "portable policy profile identity disagrees with resource ID",
                )
            if str(definition.current_revision) != resource.resource_version:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "portable policy profile revision disagrees with resource version",
                )
            return _remap_snapshot(snapshot, context)
        except ContractError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "invalid portable authorization policy profile payload",
                details={"resource_id": resource.resource_id},
            ) from exc


def register_authorization_policy_profile_portability_codec(
    registry: ResourceSerializerRegistry,
    *,
    id_policy: IdPolicy = IdPolicy.PRESERVE,
) -> None:
    registry.register(AuthorizationPolicyProfilePortableCodec(id_policy=id_policy))


def inspect_authorization_policy_profile_import(
    resource: PortableResource,
    target_id: str,
) -> tuple[ImportSecurityFinding, ...]:
    """Expose authority impact in #79 preview without granting or applying anything."""

    if resource.resource_type != AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE:
        return ()
    try:
        _require_schema(resource.payload)
        revisions = _array(resource.payload.get("revisions"), "policy profile revisions")
        if not revisions:
            raise ValueError("policy profile revision history must not be empty")
        if "assignments" in resource.payload:
            return (
                ImportSecurityFinding(
                    kind=ImportSecurityFindingKind.INVALID_SECURITY_PAYLOAD,
                    resource_type=resource.resource_type,
                    resource_id=target_id,
                    detail="policy-profile portability must never contain assignments",
                    blocking=True,
                ),
            )

        allowed: set[str] = set()
        approval_required: set[str] = set()
        resource_types: set[str] = set()
        for raw_revision in revisions:
            revision = _object(raw_revision, "policy profile revision")
            content = _object(revision.get("content"), "policy profile content")
            allowed.update(_string_array(content.get("allowed_actions"), "allowed_actions"))
            approval_required.update(
                _string_array(
                    content.get("approval_required_actions"),
                    "approval_required_actions",
                )
            )
            resource_types.update(_string_array(content.get("resource_types"), "resource_types"))

        grant_summary = ", ".join(sorted(allowed)) or "none"
        approval_summary = ", ".join(sorted(approval_required)) or "none"
        resource_summary = ", ".join(sorted(resource_types)) or "unrestricted"
        return (
            ImportSecurityFinding(
                kind=ImportSecurityFindingKind.UNTRUSTED_CONFIGURATION,
                resource_type=resource.resource_type,
                resource_id=target_id,
                detail=(
                    "destination imports this policy profile as untrusted configuration; "
                    "assignments and effective provider authority are not imported"
                ),
            ),
            ImportSecurityFinding(
                kind=ImportSecurityFindingKind.PERMISSION_ESCALATION,
                resource_type=resource.resource_type,
                resource_id=target_id,
                detail=(
                    f"potential direct actions: {grant_summary}; approval-gated actions: "
                    f"{approval_summary}; resource types: {resource_summary}"
                ),
            ),
        )
    except (ContractError, KeyError, TypeError, ValueError) as exc:
        return (
            ImportSecurityFinding(
                kind=ImportSecurityFindingKind.INVALID_SECURITY_PAYLOAD,
                resource_type=resource.resource_type,
                resource_id=target_id,
                detail=(
                    "authorization policy profile cannot be safely inspected: "
                    f"{type(exc).__name__}"
                ),
                blocking=True,
            ),
        )


def _profile_dependencies(
    snapshot: AuthorizationPolicyProfilePortableSnapshot,
) -> tuple[DependencyRequirement, ...]:
    dependencies: set[DependencyRequirement] = set()
    definition = snapshot.definition
    _add_scope_dependency(dependencies, "project", definition.project_id, "profile project scope")
    _add_scope_dependency(
        dependencies,
        "organization",
        definition.organization_id,
        "profile organization scope",
    )
    _add_scope_dependency(dependencies, "team", definition.team_id, "profile team scope")

    for revision in snapshot.revisions:
        scope = revision.content.scope_constraints
        for project_id in scope.project_ids:
            dependencies.add(
                resource_dependency("project", project_id, purpose="policy project scope")
            )
        for organization_id in scope.organization_ids:
            dependencies.add(
                resource_dependency(
                    "organization",
                    organization_id,
                    purpose="policy organization scope",
                )
            )
        for team_id in scope.team_ids:
            dependencies.add(resource_dependency("team", team_id, purpose="policy team scope"))
        for workspace_id in scope.workspace_ids:
            dependencies.add(
                resource_dependency("workspace", workspace_id, purpose="policy workspace scope")
            )
        for node_id in revision.content.conditions.allowed_node_ids:
            dependencies.add(resource_dependency("node", node_id, purpose="policy allowed node"))

    return tuple(
        sorted(
            dependencies,
            key=lambda item: (
                item.kind.value,
                item.identifier,
                item.required,
                item.version_constraint or "",
                item.purpose or "",
            ),
        )
    )


def _add_scope_dependency(
    dependencies: set[DependencyRequirement],
    resource_type: str,
    resource_id: str | None,
    purpose: str,
) -> None:
    if resource_id is not None:
        dependencies.add(resource_dependency(resource_type, resource_id, purpose=purpose))


def _remap_snapshot(
    snapshot: AuthorizationPolicyProfilePortableSnapshot,
    context: ImportContext,
) -> AuthorizationPolicyProfilePortableSnapshot:
    source_id = snapshot.definition.policy_profile_id
    target_id = context.remap(AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE, source_id)
    target_project = _remap_optional(context, "project", snapshot.definition.project_id)
    target_organization = _remap_optional(
        context,
        "organization",
        snapshot.definition.organization_id,
    )
    target_team = _remap_optional(context, "team", snapshot.definition.team_id)

    definition = replace(
        snapshot.definition,
        policy_profile_id=target_id,
        enabled=False,
        project_id=target_project,
        organization_id=target_organization,
        team_id=target_team,
    )
    revisions = tuple(
        replace(
            revision,
            policy_profile_id=target_id,
            project_id=target_project,
            organization_id=target_organization,
            team_id=target_team,
            content=_remap_content(revision.content, context, source_id, revision.revision),
        )
        for revision in snapshot.revisions
    )
    return AuthorizationPolicyProfilePortableSnapshot(definition, revisions)


def _remap_content(
    content: AuthorizationPolicyProfileContent,
    context: ImportContext,
    source_profile_id: str,
    revision: int,
) -> AuthorizationPolicyProfileContent:
    scope = content.scope_constraints
    remapped_scope = AuthorizationPolicyScopeConstraints(
        project_ids=tuple(context.remap("project", item) for item in scope.project_ids),
        organization_ids=tuple(
            context.remap("organization", item) for item in scope.organization_ids
        ),
        team_ids=tuple(context.remap("team", item) for item in scope.team_ids),
        workspace_ids=tuple(context.remap("workspace", item) for item in scope.workspace_ids),
        # Opaque exact IDs have no safe resource-type discriminator. Preserve them rather
        # than guessing a type and silently broadening/narrowing authority.
        resource_ids=scope.resource_ids,
    )
    conditions = content.conditions
    remapped_conditions = AuthorizationPolicyConditions(
        required_security_labels=conditions.required_security_labels,
        allowed_node_ids=tuple(context.remap("node", item) for item in conditions.allowed_node_ids),
        allowed_side_effects=conditions.allowed_side_effects,
    )
    provenance = content.provenance
    imported_source = provenance.source if provenance.source != "local" else "portable-package"
    imported_reference = provenance.source_reference or f"{source_profile_id}@{revision}"
    remapped_provenance = AuthorizationPolicyProvenance(
        created_by=provenance.created_by,
        source=imported_source,
        source_reference=imported_reference,
        imported=True,
        trusted=False,
    )
    return replace(
        content,
        scope_constraints=remapped_scope,
        conditions=remapped_conditions,
        provenance=remapped_provenance,
    )


def _remap_optional(context: ImportContext, resource_type: str, value: str | None) -> str | None:
    if value is None:
        return None
    return context.remap(resource_type, value)


def _definition_to_json(item: AuthorizationPolicyProfileDefinition) -> dict[str, JsonValue]:
    return {
        "policy_profile_id": item.policy_profile_id,
        "owner_ref": {"type": item.owner_ref.type, "id": item.owner_ref.id},
        "current_revision": item.current_revision,
        "enabled": item.enabled,
        "project_id": item.project_id,
        "organization_id": item.organization_id,
        "team_id": item.team_id,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


def _definition(value: object) -> AuthorizationPolicyProfileDefinition:
    item = _object(value, "policy profile definition")
    return AuthorizationPolicyProfileDefinition(
        policy_profile_id=_string(item, "policy_profile_id"),
        owner_ref=_owner(item.get("owner_ref")),
        current_revision=_integer(item, "current_revision"),
        enabled=_boolean(item, "enabled"),
        project_id=_optional_string(item, "project_id"),
        organization_id=_optional_string(item, "organization_id"),
        team_id=_optional_string(item, "team_id"),
        created_at=_datetime(item, "created_at"),
        updated_at=_datetime(item, "updated_at"),
    )


def _owner(value: object) -> OwnerRef:
    item = _object(value, "policy profile owner")
    owner_type = _string(item, "type")
    if owner_type not in _OWNER_TYPES:
        raise ValueError(f"unsupported policy profile owner type: {owner_type!r}")
    return OwnerRef(type=cast(OwnerType, owner_type), id=_string(item, "id"))


def _require_schema(payload: Mapping[str, JsonValue]) -> None:
    if payload.get("schema_version") != AUTHORIZATION_POLICY_PROFILE_PORTABLE_SCHEMA_VERSION:
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "unsupported portable authorization policy profile schema version",
            details={
                "supported_schema_version": AUTHORIZATION_POLICY_PROFILE_PORTABLE_SCHEMA_VERSION
            },
        )


def _object(value: object, field: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be an object")
    return cast(dict[str, JsonValue], value)


def _array(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return value


def _string(data: Mapping[str, JsonValue], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")
    return value


def _optional_string(data: Mapping[str, JsonValue], field: str) -> str | None:
    value = data.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string or null")
    return value


def _integer(data: Mapping[str, JsonValue], field: str) -> int:
    value = data.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    return value


def _boolean(data: Mapping[str, JsonValue], field: str) -> bool:
    value = data.get(field)
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _datetime(data: Mapping[str, JsonValue], field: str) -> datetime:
    value = _string(data, field)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed


def _string_array(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field} must contain non-blank strings")
    return tuple(cast(str, item) for item in value)


__all__ = [
    "AUTHORIZATION_POLICY_PROFILE_PORTABLE_SCHEMA_VERSION",
    "AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE",
    "AuthorizationPolicyProfilePortableCodec",
    "AuthorizationPolicyProfilePortableSnapshot",
    "inspect_authorization_policy_profile_import",
    "register_authorization_policy_profile_portability_codec",
    "snapshot_authorization_policy_profile",
]
