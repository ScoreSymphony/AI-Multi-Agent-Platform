"""Portable codec for complete canonical EgressProfile revision histories."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, cast

from ai_multi_agent_platform.contracts import (
    ContractError,
    EGRESS_PROFILE_SCHEMA_VERSION,
    EgressProfile,
    EgressProfileTrust,
    EgressTargetKind,
    ErrorCode,
    JsonValue,
)
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.security.egress_profiles import (
    EgressProfileDefinition,
    EgressProfileRepository,
    egress_profile_from_json,
    egress_profile_to_json,
)

from .dependencies import resource_dependency
from .models import DependencyKind, DependencyRequirement, IdPolicy, PortableResource
from .registry import ImportContext, ResourceExport, ResourceSerializerRegistry

EGRESS_PROFILE_PORTABLE_SCHEMA_VERSION = "1"
EGRESS_PROFILE_RESOURCE_TYPE = "egress_profile"


@dataclass(frozen=True, slots=True)
class EgressProfilePortableSnapshot:
    definition: EgressProfileDefinition
    revisions: tuple[EgressProfile, ...]

    def __post_init__(self) -> None:
        if not self.revisions:
            raise ValueError("portable egress profile requires revision history")
        numbers = tuple(item.revision for item in self.revisions)
        if numbers != tuple(range(1, self.definition.current_revision + 1)):
            raise ValueError("portable egress profile history must be contiguous from revision 1")
        for revision in self.revisions:
            if (
                revision.profile_id != self.definition.profile_id
                or revision.target_kind is not self.definition.target_kind
                or revision.target_id != self.definition.target_id
                or revision.schema_version != self.definition.schema_version
            ):
                raise ValueError("portable egress profile history has inconsistent identity")


def snapshot_egress_profile(
    repository: EgressProfileRepository,
    profile_id: str,
) -> EgressProfilePortableSnapshot:
    return EgressProfilePortableSnapshot(
        definition=repository.get_definition(profile_id),
        revisions=repository.list_revisions(profile_id),
    )


class EgressProfilePortableCodec:
    resource_type = EGRESS_PROFILE_RESOURCE_TYPE

    def __init__(self, *, id_policy: IdPolicy = IdPolicy.PRESERVE) -> None:
        self.id_policy = id_policy

    def serialize(self, value: object) -> ResourceExport:
        if not isinstance(value, EgressProfilePortableSnapshot):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "egress profile codec requires EgressProfilePortableSnapshot",
            )
        snapshot = EgressProfilePortableSnapshot(value.definition, value.revisions)
        return ResourceExport(
            resource_id=snapshot.definition.profile_id,
            resource_version=str(snapshot.definition.current_revision),
            payload={
                "schema_version": EGRESS_PROFILE_PORTABLE_SCHEMA_VERSION,
                "definition": _definition_to_json(snapshot.definition),
                "revisions": [
                    egress_profile_to_json(_portable_revision(item))
                    for item in snapshot.revisions
                ],
            },
            id_policy=self.id_policy,
            dependencies=_dependencies(snapshot),
        )

    def deserialize(self, resource: PortableResource, context: ImportContext) -> object:
        if resource.resource_type != self.resource_type:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"egress profile codec cannot deserialize {resource.resource_type!r}",
            )
        try:
            if resource.payload.get("schema_version") != EGRESS_PROFILE_PORTABLE_SCHEMA_VERSION:
                raise ContractError(
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    "unsupported portable egress profile schema version",
                )
            definition = _definition_from_json(resource.payload.get("definition"))
            raw_revisions = resource.payload.get("revisions")
            if not isinstance(raw_revisions, list):
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    "portable egress profile revisions must be a list",
                )
            snapshot = EgressProfilePortableSnapshot(
                definition,
                tuple(egress_profile_from_json(item) for item in raw_revisions),
            )
            if definition.profile_id != resource.resource_id:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "portable egress profile identity disagrees with resource ID",
                )
            if str(definition.current_revision) != resource.resource_version:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "portable egress profile version disagrees with current revision",
                )
            return _sanitize_and_remap(snapshot, context)
        except ContractError:
            raise
        except (TypeError, ValueError) as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "invalid portable egress profile payload",
                details={"resource_id": resource.resource_id},
            ) from exc


def register_egress_profile_portability_codec(
    registry: ResourceSerializerRegistry,
    *,
    id_policy: IdPolicy = IdPolicy.PRESERVE,
) -> None:
    registry.register(EgressProfilePortableCodec(id_policy=id_policy))


def _portable_revision(revision: EgressProfile) -> EgressProfile:
    """Strip arbitrary metadata values before a profile crosses the #79 portability boundary.

    EgressProfile metadata is an extension point and may contain deployment-private notes or
    verification evidence. Portability therefore carries only the single canonical boolean that
    changes disclosure semantics. The destination still removes that opt-in and all source trust
    during deserialization, so imported configuration cannot grant itself external authority.
    """

    allow_sensitive = revision.metadata.get("allow_sensitive_external")
    metadata: dict[str, JsonValue] = {}
    if isinstance(allow_sensitive, bool):
        metadata["allow_sensitive_external"] = allow_sensitive
    return replace(revision, metadata=metadata)


def _sanitize_and_remap(
    snapshot: EgressProfilePortableSnapshot,
    context: ImportContext,
) -> EgressProfilePortableSnapshot:
    target_profile_id = context.remap(EGRESS_PROFILE_RESOURCE_TYPE, snapshot.definition.profile_id)
    project_id = (
        None
        if snapshot.definition.project_id is None
        else context.remap("project", snapshot.definition.project_id)
    )
    target_id = _remap_target(context, snapshot.definition.target_kind, snapshot.definition.target_id)
    definition = replace(
        snapshot.definition,
        profile_id=target_profile_id,
        target_id=target_id,
        project_id=project_id,
    )
    revisions = tuple(
        replace(
            revision,
            profile_id=target_profile_id,
            target_id=target_id,
            trust=EgressProfileTrust.UNVERIFIED,
            metadata=_sanitized_import_metadata(revision.metadata),
        )
        for revision in snapshot.revisions
    )
    return EgressProfilePortableSnapshot(definition, revisions)


def _sanitized_import_metadata(value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    # The portable serializer already restricts metadata to safe policy fields. The target
    # nevertheless strips the sensitive-egress opt-in and any legacy verification keys so a
    # handcrafted/older package cannot retain source-system disclosure authority.
    metadata = {
        key: item
        for key, item in value.items()
        if key not in {"allow_sensitive_external", "verification_ref", "verified_by"}
    }
    metadata["imported_unverified"] = True
    return metadata


def _remap_target(
    context: ImportContext,
    target_kind: EgressTargetKind,
    target_id: str,
) -> str:
    if target_kind is EgressTargetKind.MODEL_PROVIDER:
        return context.remap("model", target_id)
    if target_kind is EgressTargetKind.CAPABILITY:
        return context.remap("capability", target_id)
    if target_kind is EgressTargetKind.CONNECTOR:
        return context.remap("connector", target_id)
    if target_kind in {EgressTargetKind.FILE_EXPORT, EgressTargetKind.ARTIFACT_EXPORT}:
        return context.remap("resource", target_id)
    return target_id


def _dependencies(
    snapshot: EgressProfilePortableSnapshot,
) -> tuple[DependencyRequirement, ...]:
    dependencies: list[DependencyRequirement] = []
    if snapshot.definition.project_id is not None:
        dependencies.append(
            resource_dependency(
                "project",
                snapshot.definition.project_id,
                purpose="Egress profile project scope",
            )
        )
    kind = snapshot.definition.target_kind
    target_id = snapshot.definition.target_id
    if kind is EgressTargetKind.MODEL_PROVIDER:
        dependencies.append(
            DependencyRequirement(
                kind=DependencyKind.MODEL,
                identifier=target_id,
                purpose="Egress profile model configuration target",
            )
        )
    elif kind is EgressTargetKind.CAPABILITY:
        dependencies.append(
            DependencyRequirement(
                kind=DependencyKind.CAPABILITY,
                identifier=target_id,
                purpose="Egress profile capability target",
            )
        )
    elif kind is EgressTargetKind.CONNECTOR:
        dependencies.append(
            resource_dependency(
                "connector",
                target_id,
                purpose="Egress profile connector target",
            )
        )
    return tuple(dependencies)


def _definition_to_json(value: EgressProfileDefinition) -> dict[str, JsonValue]:
    return {
        "profile_id": value.profile_id,
        "target_kind": value.target_kind.value,
        "target_id": value.target_id,
        "owner_ref": {"type": value.owner_ref.type, "id": value.owner_ref.id},
        "current_revision": value.current_revision,
        "project_id": value.project_id,
        "enabled": value.enabled,
        "created_at": value.created_at.isoformat(),
        "updated_at": value.updated_at.isoformat(),
        "schema_version": value.schema_version,
    }


def _definition_from_json(value: object) -> EgressProfileDefinition:
    item = _object(value, "egress profile definition")
    owner = _object(item.get("owner_ref"), "egress profile owner")
    owner_type = _string(owner, "type")
    if owner_type not in {"user", "organization", "team", "service"}:
        raise ValueError("unsupported egress profile owner type")
    return EgressProfileDefinition(
        profile_id=_string(item, "profile_id"),
        target_kind=EgressTargetKind(_string(item, "target_kind")),
        target_id=_string(item, "target_id"),
        owner_ref=OwnerRef(
            type=cast(Literal["user", "organization", "team", "service"], owner_type),
            id=_string(owner, "id"),
        ),
        current_revision=_positive_int(item, "current_revision"),
        project_id=_optional_string(item, "project_id"),
        enabled=_boolean(item, "enabled"),
        created_at=_timestamp(item, "created_at"),
        updated_at=_timestamp(item, "updated_at"),
        schema_version=_string(item, "schema_version"),
    )


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return dict(value)


def _string(value: Mapping[str, object], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{field} must be a non-blank string")
    return item


def _optional_string(value: Mapping[str, object], field: str) -> str | None:
    item = value.get(field)
    if item is None:
        return None
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{field} must be non-blank when provided")
    return item


def _positive_int(value: Mapping[str, object], field: str) -> int:
    item = value.get(field)
    if isinstance(item, bool) or not isinstance(item, int) or item < 1:
        raise ValueError(f"{field} must be a positive integer")
    return item


def _boolean(value: Mapping[str, object], field: str) -> bool:
    item = value.get(field)
    if not isinstance(item, bool):
        raise ValueError(f"{field} must be a boolean")
    return item


def _timestamp(value: Mapping[str, object], field: str) -> datetime:
    raw = _string(value, field)
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed


__all__ = [
    "EGRESS_PROFILE_PORTABLE_SCHEMA_VERSION",
    "EGRESS_PROFILE_RESOURCE_TYPE",
    "EgressProfilePortableCodec",
    "EgressProfilePortableSnapshot",
    "register_egress_profile_portability_codec",
    "snapshot_egress_profile",
]
