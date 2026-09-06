"""Rollback-capable canonical model-routing profile import handler."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.models.routing_profile_repository import (
    ModelRoutingProfileRepository,
)
from ai_multi_agent_platform.models.routing_profiles import ModelRoutingProfileDefinition

from .model_routing_profile_codecs import (
    MODEL_ROUTING_PROFILE_RESOURCE_TYPE,
    ModelRoutingProfilePortableSnapshot,
)
from .models import PortableResource
from .registry import ImportContext

RoutingProfileDependencyAudit = Callable[[str], tuple[str, ...] | None]


class ModelRoutingProfileImportMutationHandler:
    """Restore one complete routing-profile history through its canonical repository.

    Profile removal is transaction compensation only. Compensation is fail-closed and
    requires a cross-domain dependency audit that can prove no canonical resource still
    references the imported profile.
    """

    resource_type = MODEL_ROUTING_PROFILE_RESOURCE_TYPE

    def __init__(
        self,
        repository: ModelRoutingProfileRepository,
        *,
        dependency_audit: RoutingProfileDependencyAudit | None = None,
    ) -> None:
        self._repository = repository
        self._dependency_audit = dependency_audit

    async def preflight(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> None:
        del resource, context
        snapshot = _require_snapshot(value)
        _require_missing_profile(self._repository, snapshot.definition.profile_id)

    async def apply(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> object:
        del resource, context
        snapshot = _require_snapshot(value)
        applied_revision = 0
        try:
            for index, revision in enumerate(snapshot.revisions):
                definition = _definition_at(
                    snapshot.definition,
                    revision.revision,
                    revision.created_at,
                    is_final=index == len(snapshot.revisions) - 1,
                )
                if index == 0:
                    self._repository.create_profile(definition, revision)
                else:
                    self._repository.update_profile(definition, revision)
                applied_revision = revision.revision
            return snapshot.definition.profile_id
        except Exception:
            if applied_revision:
                try:
                    self._compensate(
                        snapshot.definition.profile_id,
                        expected_current_revision=applied_revision,
                    )
                except Exception as rollback_error:
                    raise ContractError(
                        ErrorCode.BACKEND_ERROR,
                        "routing profile import failed and internal compensation also failed",
                        details={
                            "profile_id": snapshot.definition.profile_id,
                            "expected_current_revision": applied_revision,
                        },
                    ) from rollback_error
            raise

    async def rollback(
        self,
        resource: PortableResource,
        value: object,
        token: object,
        context: ImportContext,
    ) -> None:
        del resource, context
        snapshot = _require_snapshot(value)
        if not isinstance(token, str) or token != snapshot.definition.profile_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "routing profile rollback token must match the imported profile ID",
            )
        self._compensate(
            token,
            expected_current_revision=snapshot.definition.current_revision,
        )

    def _compensate(self, profile_id: str, *, expected_current_revision: int) -> None:
        dependencies = (
            None if self._dependency_audit is None else self._dependency_audit(profile_id)
        )
        if dependencies is None:
            raise ContractError(
                ErrorCode.CONFLICT,
                "routing profile compensation requires a complete cross-domain reference audit",
                details={"profile_id": profile_id},
            )
        if dependencies:
            raise ContractError(
                ErrorCode.CONFLICT,
                "routing profile cannot be compensated while canonical resources reference it",
                details={
                    "profile_id": profile_id,
                    "dependencies": list(dependencies),
                },
            )
        self._repository.compensate_profile_creation(
            profile_id,
            expected_current_revision=expected_current_revision,
        )


def _definition_at(
    final_definition: ModelRoutingProfileDefinition,
    revision: int,
    revision_created_at: datetime,
    *,
    is_final: bool,
) -> ModelRoutingProfileDefinition:
    if is_final:
        return final_definition
    return replace(
        final_definition,
        current_revision=revision,
        updated_at=revision_created_at,
    )


def _require_snapshot(value: object) -> ModelRoutingProfilePortableSnapshot:
    if not isinstance(value, ModelRoutingProfilePortableSnapshot):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "routing profile mutation handler received the wrong decoded resource type",
        )
    return value


def _require_missing_profile(
    repository: ModelRoutingProfileRepository,
    profile_id: str,
) -> None:
    try:
        repository.get_definition(profile_id)
    except ContractError as exc:
        if exc.code is ErrorCode.NOT_FOUND:
            return
        raise
    raise ContractError(
        ErrorCode.CONFLICT,
        f"routing profile appeared after import preview: {profile_id}",
        details={"profile_id": profile_id},
    )


__all__ = [
    "ModelRoutingProfileImportMutationHandler",
    "RoutingProfileDependencyAudit",
]
