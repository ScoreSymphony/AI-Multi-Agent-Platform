"""Portable codec for canonical reusable Template revision histories."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.templates import (
    TemplateContent,
    TemplateDefinition,
    TemplateRepository,
    TemplateRevision,
    TemplateRevisionState,
    validate_template_configuration,
)
from ai_multi_agent_platform.templates.codec import (
    template_content_from_json,
    template_content_to_json,
)

from .dependencies import resource_dependency
from .models import DependencyKind, DependencyRequirement, IdPolicy, PortableResource
from .registry import ImportContext, ResourceExport, ResourceSerializerRegistry

TEMPLATE_PORTABLE_SCHEMA_VERSION = "1"
TEMPLATE_RESOURCE_TYPE = "template"


@dataclass(frozen=True, slots=True)
class TemplatePortableSnapshot:
    definition: TemplateDefinition
    revisions: tuple[TemplateRevision, ...]

    def __post_init__(self) -> None:
        if not self.revisions:
            raise ValueError("portable Template snapshot requires revision history")
        if any(item.template_id != self.definition.template_id for item in self.revisions):
            raise ValueError("portable Template revisions must match the Template definition")
        numbers = tuple(item.revision for item in self.revisions)
        if numbers != tuple(range(1, self.definition.current_revision + 1)):
            raise ValueError(
                "portable Template revision history must be contiguous from revision 1"
            )
        if self.revisions[-1].revision != self.definition.current_revision:
            raise ValueError("portable Template definition must point at its latest revision")
        published = tuple(
            item.revision
            for item in self.revisions
            if item.state is TemplateRevisionState.PUBLISHED
        )
        latest_published = published[-1] if published else None
        if latest_published != self.definition.latest_published_revision:
            raise ValueError("portable Template published-revision pointer does not match history")
        for revision in self.revisions:
            if (
                revision.owner_ref != self.definition.owner_ref
                or revision.project_id != self.definition.project_id
                or revision.organization_id != self.definition.organization_id
            ):
                raise ValueError("portable Template revision ownership scope is inconsistent")
            validate_template_configuration(revision.content.configuration)


def snapshot_template(
    repository: TemplateRepository,
    template_id: str,
) -> TemplatePortableSnapshot:
    return TemplatePortableSnapshot(
        definition=repository.get_template(template_id),
        revisions=repository.list_revisions(template_id),
    )


class TemplatePortableCodec:
    resource_type = TEMPLATE_RESOURCE_TYPE

    def __init__(self, *, id_policy: IdPolicy = IdPolicy.PRESERVE) -> None:
        self.id_policy = id_policy

    def serialize(self, value: object) -> ResourceExport:
        if not isinstance(value, TemplatePortableSnapshot):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Template portable codec requires a TemplatePortableSnapshot",
            )
        try:
            snapshot = TemplatePortableSnapshot(value.definition, value.revisions)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "canonical Template history is not portable",
                details={"template_id": value.definition.template_id},
            ) from exc
        return ResourceExport(
            resource_id=snapshot.definition.template_id,
            resource_version=str(snapshot.definition.current_revision),
            payload={
                "schema_version": TEMPLATE_PORTABLE_SCHEMA_VERSION,
                "definition": _definition_to_json(snapshot.definition),
                "revisions": [_revision_to_json(item) for item in snapshot.revisions],
            },
            id_policy=self.id_policy,
            dependencies=_template_dependencies(snapshot),
        )

    def deserialize(self, resource: PortableResource, context: ImportContext) -> object:
        if resource.resource_type != self.resource_type:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"Template codec cannot deserialize resource type {resource.resource_type!r}",
            )
        try:
            _require_schema(resource.payload)
            definition = _definition(resource.payload.get("definition"))
            revisions = tuple(
                _revision(item)
                for item in _array(resource.payload.get("revisions"), "Template revisions")
            )
            snapshot = TemplatePortableSnapshot(definition, revisions)
            return _remap_snapshot(snapshot, context)
        except ContractError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "invalid portable Template payload",
                details={"resource_id": resource.resource_id},
            ) from exc


def register_template_portability_codec(
    registry: ResourceSerializerRegistry,
    *,
    id_policy: IdPolicy = IdPolicy.PRESERVE,
) -> None:
    registry.register(TemplatePortableCodec(id_policy=id_policy))


def _template_dependencies(
    snapshot: TemplatePortableSnapshot,
) -> tuple[DependencyRequirement, ...]:
    dependencies: set[DependencyRequirement] = set()
    definition = snapshot.definition
    if definition.project_id is not None:
        dependencies.add(
            resource_dependency(
                "project",
                definition.project_id,
                purpose="Template project scope",
            )
        )
    if definition.organization_id is not None:
        dependencies.add(
            resource_dependency(
                "organization",
                definition.organization_id,
                purpose="Template organization scope",
            )
        )

    for revision in snapshot.revisions:
        for dependency in revision.content.dependencies:
            dependencies.add(
                resource_dependency(
                    TEMPLATE_RESOURCE_TYPE,
                    dependency.template_id,
                    required=not dependency.optional,
                    version_constraint=(
                        f"=={dependency.revision}" if dependency.revision is not None else None
                    ),
                    purpose="Template dependency",
                )
            )
        requirements = revision.content.requirements
        for capability in requirements.capabilities:
            dependencies.add(
                DependencyRequirement(
                    kind=DependencyKind.CAPABILITY,
                    identifier=capability.capability_id,
                    required=not capability.optional,
                    version_constraint=capability.version_constraint,
                    purpose="Template capability requirement",
                )
            )
        for plugin_id in requirements.plugin_ids:
            dependencies.add(
                DependencyRequirement(
                    kind=DependencyKind.PLUGIN,
                    identifier=plugin_id,
                    purpose="Template plugin requirement",
                )
            )
        for connector_id in requirements.connector_ids:
            dependencies.add(
                DependencyRequirement(
                    kind=DependencyKind.CONNECTOR,
                    identifier=connector_id,
                    purpose="Template connector requirement",
                )
            )
        for routing_ref in requirements.model_policy_refs:
            dependencies.add(
                resource_dependency(
                    "model_routing_policy",
                    routing_ref,
                    purpose="Template model-routing policy requirement",
                )
            )
        source_template = revision.content.provenance.source_template
        if source_template is not None:
            dependencies.add(
                resource_dependency(
                    TEMPLATE_RESOURCE_TYPE,
                    source_template.template_id,
                    required=False,
                    version_constraint=f"=={source_template.revision}",
                    purpose="Template provenance source",
                )
            )
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


def _remap_snapshot(
    snapshot: TemplatePortableSnapshot,
    context: ImportContext,
) -> TemplatePortableSnapshot:
    target_id = context.remap(TEMPLATE_RESOURCE_TYPE, snapshot.definition.template_id)
    target_project = _remap_optional(context, "project", snapshot.definition.project_id)
    target_organization = _remap_optional(
        context,
        "organization",
        snapshot.definition.organization_id,
    )
    definition = replace(
        snapshot.definition,
        template_id=target_id,
        project_id=target_project,
        organization_id=target_organization,
    )
    revisions = tuple(
        replace(
            revision,
            template_id=target_id,
            project_id=target_project,
            organization_id=target_organization,
            content=_remap_content(revision.content, context),
        )
        for revision in snapshot.revisions
    )
    return TemplatePortableSnapshot(definition, revisions)


def _remap_content(content: TemplateContent, context: ImportContext) -> TemplateContent:
    dependencies = tuple(
        replace(
            item,
            template_id=context.remap(TEMPLATE_RESOURCE_TYPE, item.template_id),
        )
        for item in content.dependencies
    )
    provenance = content.provenance
    source = provenance.source_template
    if source is not None:
        provenance = replace(
            provenance,
            source_template=replace(
                source,
                template_id=context.remap(TEMPLATE_RESOURCE_TYPE, source.template_id),
            ),
        )
    requirements = content.requirements
    requirements = replace(
        requirements,
        model_policy_refs=tuple(
            context.remap("model_routing_policy", item) for item in requirements.model_policy_refs
        ),
    )
    return replace(
        content,
        dependencies=dependencies,
        requirements=requirements,
        provenance=provenance,
    )


def _definition_to_json(item: TemplateDefinition) -> dict[str, JsonValue]:
    return {
        "template_id": item.template_id,
        "owner_ref": {"type": item.owner_ref.type, "id": item.owner_ref.id},
        "current_revision": item.current_revision,
        "latest_published_revision": item.latest_published_revision,
        "project_id": item.project_id,
        "organization_id": item.organization_id,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


def _revision_to_json(item: TemplateRevision) -> dict[str, JsonValue]:
    return {
        "template_id": item.template_id,
        "revision": item.revision,
        "state": item.state.value,
        "owner_ref": {"type": item.owner_ref.type, "id": item.owner_ref.id},
        "content": template_content_to_json(item.content),
        "project_id": item.project_id,
        "organization_id": item.organization_id,
        "created_at": item.created_at.isoformat(),
    }


def _definition(value: object) -> TemplateDefinition:
    item = _object(value, "Template definition")
    return TemplateDefinition(
        template_id=_string(item, "template_id"),
        owner_ref=_owner(item.get("owner_ref")),
        current_revision=_integer(item, "current_revision"),
        latest_published_revision=_optional_integer(item, "latest_published_revision"),
        project_id=_optional_string(item, "project_id"),
        organization_id=_optional_string(item, "organization_id"),
        created_at=_datetime(item, "created_at"),
        updated_at=_datetime(item, "updated_at"),
    )


def _revision(value: object) -> TemplateRevision:
    item = _object(value, "Template revision")
    return TemplateRevision(
        template_id=_string(item, "template_id"),
        revision=_integer(item, "revision"),
        state=TemplateRevisionState(_string(item, "state")),
        owner_ref=_owner(item.get("owner_ref")),
        content=template_content_from_json(item.get("content")),
        project_id=_optional_string(item, "project_id"),
        organization_id=_optional_string(item, "organization_id"),
        created_at=_datetime(item, "created_at"),
    )


def _owner(value: object) -> OwnerRef:
    item = _object(value, "Template owner")
    owner_type = _string(item, "type")
    allowed = {"user", "organization", "team", "service"}
    if owner_type not in allowed:
        raise ValueError(f"unsupported Template owner type: {owner_type!r}")
    typed = cast(Literal["user", "organization", "team", "service"], owner_type)
    return OwnerRef(type=typed, id=_string(item, "id"))


def _require_schema(payload: dict[str, JsonValue]) -> None:
    if payload.get("schema_version") != TEMPLATE_PORTABLE_SCHEMA_VERSION:
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "unsupported portable Template schema version",
            details={"supported_schema_version": TEMPLATE_PORTABLE_SCHEMA_VERSION},
        )


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return cast(dict[str, object], value)


def _array(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def _string(item: dict[str, object], field: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")
    return value


def _optional_string(item: dict[str, object], field: str) -> str | None:
    value = item.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string when present")
    return value


def _integer(item: dict[str, object], field: str) -> int:
    value = item.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    return value


def _optional_integer(item: dict[str, object], field: str) -> int | None:
    value = item.get(field)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field} must be an integer when present")
    return value


def _datetime(item: dict[str, object], field: str) -> datetime:
    parsed = datetime.fromisoformat(_string(item, field))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed


def _remap_optional(context: ImportContext, resource_type: str, value: str | None) -> str | None:
    if value is None:
        return None
    return context.remap(resource_type, value)


__all__ = [
    "TEMPLATE_PORTABLE_SCHEMA_VERSION",
    "TEMPLATE_RESOURCE_TYPE",
    "TemplatePortableCodec",
    "TemplatePortableSnapshot",
    "register_template_portability_codec",
    "snapshot_template",
]
