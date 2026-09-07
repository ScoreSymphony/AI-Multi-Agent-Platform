"""Portable codecs for canonical Skill histories and immutable Skill Bundle evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.skills.codec import (
    skill_bundle_from_json,
    skill_bundle_to_json,
    skill_definition_from_json,
    skill_definition_to_json,
    skill_revision_from_json,
    skill_revision_to_json,
)
from ai_multi_agent_platform.skills.models import SkillBundle, SkillDefinition, SkillRevision
from ai_multi_agent_platform.skills.repository import SkillRepository

from .dependencies import resource_dependency
from .models import DependencyKind, DependencyRequirement, IdPolicy, PortableResource
from .registry import ImportContext, ResourceExport, ResourceSerializerRegistry

SKILL_PORTABLE_SCHEMA_VERSION = "1"
SKILL_BUNDLE_PORTABLE_SCHEMA_VERSION = "1"
SKILL_RESOURCE_TYPE = "skill"
SKILL_BUNDLE_RESOURCE_TYPE = "skill_bundle"


@dataclass(frozen=True, slots=True)
class SkillPortableSnapshot:
    definition: SkillDefinition
    revisions: tuple[SkillRevision, ...]

    def __post_init__(self) -> None:
        if not self.revisions:
            raise ValueError("portable Skill snapshot requires revision history")
        if any(item.skill_id != self.definition.skill_id for item in self.revisions):
            raise ValueError("portable Skill revisions must match the Skill definition")
        numbers = tuple(item.revision for item in self.revisions)
        if numbers != tuple(range(1, self.definition.current_revision + 1)):
            raise ValueError("portable Skill revision history must be contiguous from revision 1")
        if self.revisions[-1].revision != self.definition.current_revision:
            raise ValueError("portable Skill definition must point at its latest revision")
        for revision in self.revisions:
            if (
                revision.owner_ref != self.definition.owner_ref
                or revision.project_id != self.definition.project_id
                or revision.workspace_id != self.definition.workspace_id
            ):
                raise ValueError("portable Skill revision ownership scope is inconsistent")


def snapshot_skill(repository: SkillRepository, skill_id: str) -> SkillPortableSnapshot:
    return SkillPortableSnapshot(
        definition=repository.get_skill(skill_id),
        revisions=repository.list_skill_revisions(skill_id),
    )


class SkillPortableCodec:
    resource_type = SKILL_RESOURCE_TYPE

    def serialize(self, value: object) -> ResourceExport:
        if not isinstance(value, SkillPortableSnapshot):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Skill portable codec requires a SkillPortableSnapshot",
            )
        try:
            snapshot = SkillPortableSnapshot(value.definition, value.revisions)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "canonical Skill history is not portable",
                details={"skill_id": value.definition.skill_id},
            ) from exc
        return ResourceExport(
            resource_id=snapshot.definition.skill_id,
            resource_version=str(snapshot.definition.current_revision),
            payload={
                "schema_version": SKILL_PORTABLE_SCHEMA_VERSION,
                "definition": skill_definition_to_json(snapshot.definition),
                "revisions": [skill_revision_to_json(item) for item in snapshot.revisions],
            },
            id_policy=IdPolicy.PRESERVE,
            dependencies=_skill_dependencies(snapshot),
        )

    def deserialize(self, resource: PortableResource, context: ImportContext) -> object:
        if resource.resource_type != self.resource_type:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"Skill codec cannot deserialize resource type {resource.resource_type!r}",
            )
        _require_schema(resource.payload, SKILL_PORTABLE_SCHEMA_VERSION, "Skill")
        if context.remap(SKILL_RESOURCE_TYPE, resource.resource_id) != resource.resource_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "portable Skill histories preserve canonical identity; remapping requires cloning after import",
                details={"skill_id": resource.resource_id},
            )
        try:
            definition = skill_definition_from_json(resource.payload.get("definition"))
            revisions = tuple(
                skill_revision_from_json(item)
                for item in _array(resource.payload.get("revisions"), "Skill revisions")
            )
            snapshot = SkillPortableSnapshot(definition, revisions)
        except ContractError:
            raise
        except (TypeError, ValueError) as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "invalid portable Skill payload",
                details={"resource_id": resource.resource_id},
            ) from exc
        if snapshot.definition.skill_id != resource.resource_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "portable Skill resource identity does not match payload",
            )
        return snapshot


class SkillBundlePortableCodec:
    resource_type = SKILL_BUNDLE_RESOURCE_TYPE

    def serialize(self, value: object) -> ResourceExport:
        if not isinstance(value, SkillBundle):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Skill Bundle portable codec requires a SkillBundle",
            )
        return ResourceExport(
            resource_id=value.skill_bundle_id,
            resource_version=value.digest,
            payload={
                "schema_version": SKILL_BUNDLE_PORTABLE_SCHEMA_VERSION,
                "bundle": skill_bundle_to_json(value),
            },
            id_policy=IdPolicy.HISTORICAL_PRESERVE,
            dependencies=_bundle_dependencies(value),
        )

    def deserialize(self, resource: PortableResource, context: ImportContext) -> object:
        if resource.resource_type != self.resource_type:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"Skill Bundle codec cannot deserialize resource type {resource.resource_type!r}",
            )
        _require_schema(
            resource.payload,
            SKILL_BUNDLE_PORTABLE_SCHEMA_VERSION,
            "Skill Bundle",
        )
        if context.remap(SKILL_BUNDLE_RESOURCE_TYPE, resource.resource_id) != resource.resource_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "historical Skill Bundle identity cannot be remapped",
                details={"skill_bundle_id": resource.resource_id},
            )
        bundle = skill_bundle_from_json(resource.payload.get("bundle"))
        if bundle.skill_bundle_id != resource.resource_id or bundle.digest != resource.resource_version:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "portable Skill Bundle identity/hash does not match payload",
            )
        for resource_type, resource_id in (
            ("run", bundle.run_id),
            ("task", bundle.task_id),
            ("agent", bundle.agent_id),
            ("step", bundle.step_id),
            ("project", bundle.project_id),
            ("workspace", bundle.workspace_id),
        ):
            if resource_id is not None and context.remap(resource_type, resource_id) != resource_id:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "historical Skill Bundle dependencies cannot be remapped without changing its digest",
                    details={"resource_type": resource_type, "resource_id": resource_id},
                )
        for entry in bundle.entries:
            if context.remap(SKILL_RESOURCE_TYPE, entry.ref.skill_id) != entry.ref.skill_id:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "historical Skill Bundle Skill references cannot be remapped",
                    details={"skill_id": entry.ref.skill_id},
                )
        return bundle


def register_skill_portability_codecs(registry: ResourceSerializerRegistry) -> None:
    registry.register(SkillPortableCodec())
    registry.register(SkillBundlePortableCodec())


def _skill_dependencies(snapshot: SkillPortableSnapshot) -> tuple[DependencyRequirement, ...]:
    dependencies: set[DependencyRequirement] = set()
    definition = snapshot.definition
    if definition.project_id is not None:
        dependencies.add(
            resource_dependency("project", definition.project_id, purpose="Skill project scope")
        )
    if definition.workspace_id is not None:
        dependencies.add(
            resource_dependency("workspace", definition.workspace_id, purpose="Skill workspace scope")
        )
    for revision in snapshot.revisions:
        profile = revision.profile
        for requirement in profile.capability_requirements:
            dependencies.add(
                DependencyRequirement(
                    kind=DependencyKind.CAPABILITY,
                    identifier=requirement.capability_id,
                    version_constraint=_capability_constraint(requirement),
                    purpose="Skill capability requirement",
                )
            )
        for conflict_id in profile.conflicts_with_skill_ids:
            dependencies.add(
                resource_dependency(
                    SKILL_RESOURCE_TYPE,
                    conflict_id,
                    required=False,
                    purpose="Skill composition conflict metadata",
                )
            )
        if profile.replacement is not None:
            dependencies.add(
                resource_dependency(
                    SKILL_RESOURCE_TYPE,
                    profile.replacement.skill_id,
                    required=False,
                    version_constraint=f"=={profile.replacement.revision}",
                    purpose="Deprecated Skill replacement",
                )
            )
    return _sorted_dependencies(dependencies)


def _bundle_dependencies(bundle: SkillBundle) -> tuple[DependencyRequirement, ...]:
    dependencies: set[DependencyRequirement] = {
        resource_dependency("run", bundle.run_id, purpose="Skill Bundle Run evidence"),
        resource_dependency("task", bundle.task_id, purpose="Skill Bundle Task evidence"),
        resource_dependency(
            "agent",
            bundle.agent_id,
            version_constraint=f"=={bundle.agent_revision}",
            purpose="Skill Bundle Agent revision evidence",
        ),
    }
    if bundle.step_id is not None:
        dependencies.add(resource_dependency("step", bundle.step_id, purpose="Skill Bundle Step"))
    if bundle.project_id is not None:
        dependencies.add(
            resource_dependency("project", bundle.project_id, purpose="Skill Bundle project scope")
        )
    if bundle.workspace_id is not None:
        dependencies.add(
            resource_dependency("workspace", bundle.workspace_id, purpose="Skill Bundle workspace scope")
        )
    for entry in bundle.entries:
        dependencies.add(
            resource_dependency(
                SKILL_RESOURCE_TYPE,
                entry.ref.skill_id,
                version_constraint=f"=={entry.ref.revision}",
                purpose="Exact Skill Bundle member revision",
            )
        )
    return _sorted_dependencies(dependencies)


def _capability_constraint(requirement: object) -> str | None:
    from ai_multi_agent_platform.skills.models import SkillCapabilityRequirement

    item = cast(SkillCapabilityRequirement, requirement)
    if item.exact_version is not None:
        return f"=={item.exact_version}"
    parts: list[str] = []
    if item.minimum_version is not None:
        parts.append(f">={item.minimum_version}")
    if item.maximum_version is not None:
        parts.append(f"<={item.maximum_version}")
    return ",".join(parts) or None


def _sorted_dependencies(
    dependencies: set[DependencyRequirement],
) -> tuple[DependencyRequirement, ...]:
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


def _require_schema(payload: dict[str, JsonValue], expected: str, name: str) -> None:
    if payload.get("schema_version") != expected:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"unsupported portable {name} schema version",
            details={"schema_version": payload.get("schema_version")},
        )


def _array(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, f"{name} must be an array")
    return cast(list[object], value)
