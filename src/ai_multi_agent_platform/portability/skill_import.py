"""Rollback-capable canonical Skill and historical Skill Bundle import handlers."""

from __future__ import annotations

from dataclasses import replace

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.domain import Provenance
from ai_multi_agent_platform.skills import (
    SkillBundle,
    SkillDefinition,
    SkillEvaluationStatus,
    SkillProfile,
    SkillRepository,
    SkillTrustStatus,
)

from .models import PortableResource
from .registry import ImportContext
from .skill_codecs import (
    SKILL_BUNDLE_RESOURCE_TYPE,
    SKILL_RESOURCE_TYPE,
    SkillPortableSnapshot,
)


class SkillImportMutationHandler:
    """Restore one Skill history without inheriting third-party runtime trust."""

    resource_type = SKILL_RESOURCE_TYPE

    def __init__(self, repository: SkillRepository) -> None:
        self._repository = repository

    async def preflight(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> None:
        del resource, context
        snapshot = _require_skill_snapshot(value)
        _require_missing_skill(self._repository, snapshot.definition.skill_id)

    async def apply(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> object:
        del resource, context
        snapshot = _destination_safe_snapshot(_require_skill_snapshot(value))
        created = False
        try:
            for index, revision in enumerate(snapshot.revisions):
                definition = _definition_at(
                    snapshot,
                    revision.revision,
                    is_final=index == len(snapshot.revisions) - 1,
                )
                if index == 0:
                    self._repository.create_skill(definition, revision)
                    created = True
                else:
                    self._repository.update_skill(definition, revision)
            return snapshot.definition.skill_id
        except Exception:
            if created:
                try:
                    self._repository.delete_skill(snapshot.definition.skill_id)
                except Exception as rollback_error:
                    raise ContractError(
                        ErrorCode.BACKEND_ERROR,
                        "portable Skill apply failed and internal compensation also failed",
                        details={"skill_id": snapshot.definition.skill_id},
                    ) from rollback_error
            raise

    async def rollback(
        self,
        resource: PortableResource,
        value: object,
        token: object,
        context: ImportContext,
    ) -> None:
        del resource, value, context
        if not isinstance(token, str):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "portable Skill rollback token must be the imported Skill ID",
            )
        self._repository.delete_skill(token)


class SkillBundleImportMutationHandler:
    """Restore immutable historical Skill Bundle evidence after its Skill revisions exist."""

    resource_type = SKILL_BUNDLE_RESOURCE_TYPE

    def __init__(self, repository: SkillRepository) -> None:
        self._repository = repository

    async def preflight(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> None:
        del resource, context
        bundle = _require_bundle(value)
        _require_missing_bundle(self._repository, bundle.skill_bundle_id)
        for entry in bundle.entries:
            self._repository.get_skill_revision(entry.ref.skill_id, entry.ref.revision)

    async def apply(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> object:
        del resource, context
        bundle = _require_bundle(value)
        self._repository.save_bundle(bundle)
        return bundle.skill_bundle_id

    async def rollback(
        self,
        resource: PortableResource,
        value: object,
        token: object,
        context: ImportContext,
    ) -> None:
        del resource, value, context
        if not isinstance(token, str):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "portable Skill Bundle rollback token must be the imported bundle ID",
            )
        self._repository.delete_bundle(token)


def _destination_safe_snapshot(snapshot: SkillPortableSnapshot) -> SkillPortableSnapshot:
    revisions = []
    for revision in snapshot.revisions:
        profile = revision.profile
        if profile.source is None:
            revisions.append(revision)
            continue
        provenance = _import_provenance(revision.provenance, profile)
        revisions.append(
            replace(
                revision,
                profile=replace(
                    profile,
                    enabled=False,
                    trust_status=SkillTrustStatus.DISCOVERED,
                    evaluation_status=SkillEvaluationStatus.NOT_EVALUATED,
                    evaluation_metadata={},
                ),
                provenance=provenance,
            )
        )
    return SkillPortableSnapshot(snapshot.definition, tuple(revisions))


def _import_provenance(value: Provenance | None, profile: SkillProfile) -> Provenance:
    details = {} if value is None else dict(value.details)
    details.update(
        {
            "portable_import_requires_revalidation": True,
            "imported_source_trust_status": profile.trust_status.value,
            "imported_source_evaluation_status": profile.evaluation_status.value,
            "imported_source_enabled": profile.enabled,
        }
    )
    if value is None:
        return Provenance(source="portability-import", details=details)
    return Provenance(source=value.source, actor_ref=value.actor_ref, details=details)


def _definition_at(
    snapshot: SkillPortableSnapshot,
    revision: int,
    *,
    is_final: bool,
) -> SkillDefinition:
    if is_final:
        return snapshot.definition
    return replace(
        snapshot.definition,
        current_revision=revision,
        updated_at=snapshot.revisions[revision - 1].created_at,
    )


def _require_skill_snapshot(value: object) -> SkillPortableSnapshot:
    if not isinstance(value, SkillPortableSnapshot):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "portable Skill mutation handler received the wrong decoded resource type",
        )
    return value


def _require_bundle(value: object) -> SkillBundle:
    if not isinstance(value, SkillBundle):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "portable Skill Bundle mutation handler received the wrong decoded resource type",
        )
    return value


def _require_missing_skill(repository: SkillRepository, skill_id: str) -> None:
    try:
        repository.get_skill(skill_id)
    except ContractError as exc:
        if exc.code is ErrorCode.NOT_FOUND:
            return
        raise
    raise ContractError(
        ErrorCode.CONFLICT,
        f"Skill appeared after import preview: {skill_id}",
        details={"skill_id": skill_id},
    )


def _require_missing_bundle(repository: SkillRepository, skill_bundle_id: str) -> None:
    try:
        repository.get_bundle(skill_bundle_id)
    except ContractError as exc:
        if exc.code is ErrorCode.NOT_FOUND:
            return
        raise
    raise ContractError(
        ErrorCode.CONFLICT,
        f"Skill Bundle appeared after import preview: {skill_bundle_id}",
        details={"skill_bundle_id": skill_bundle_id},
    )


__all__ = ["SkillBundleImportMutationHandler", "SkillImportMutationHandler"]
