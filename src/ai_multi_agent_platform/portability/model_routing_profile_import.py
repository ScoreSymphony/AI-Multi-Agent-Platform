"""Rollback-capable canonical model-routing profile import handler."""

from __future__ import annotations

from dataclasses import replace

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


class ModelRoutingProfileImportMutationHandler:
    """Restore one complete routing-profile history through its canonical repository."""

    resource_type = MODEL_ROUTING_PROFILE_RESOURCE_TYPE

    def __init__(self, repository: ModelRoutingProfileRepository) -> None:
        self._repository = repository

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
        created = False
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
                    created = True
                else:
                    self._repository.update_profile(definition, revision)
            return snapshot.definition.profile_id
        except Exception:
            if created:
                try:
                    self._repository.delete_profile(snapshot.definition.profile_id)
                except Exception as rollback_error:
                    raise ContractError(
                        ErrorCode.BACKEND_ERROR,
                        "routing profile import failed and internal compensation also failed",
                        details={"profile_id": snapshot.definition.profile_id},
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
                "routing profile rollback token must be the imported profile ID",
            )
        self._repository.delete_profile(token)


def _definition_at(
    final_definition: ModelRoutingProfileDefinition,
    revision: int,
    revision_created_at: object,
    *,
    is_final: bool,
) -> ModelRoutingProfileDefinition:
    if is_final:
        return final_definition
    if not hasattr(revision_created_at, "tzinfo"):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "routing profile revision timestamp is invalid during import",
        )
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


__all__ = ["ModelRoutingProfileImportMutationHandler"]
