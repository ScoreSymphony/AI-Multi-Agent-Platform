"""Rollback-capable import handler for canonical EgressProfile histories."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.security.egress_profiles import (
    EgressProfileDefinition,
    EgressProfileRepository,
)

from .egress_profile_codecs import (
    EGRESS_PROFILE_RESOURCE_TYPE,
    EgressProfilePortableSnapshot,
)
from .models import PortableResource
from .registry import ImportContext


class EgressProfileImportMutationHandler:
    """Restore sanitized history without importing source-system authority."""

    resource_type = EGRESS_PROFILE_RESOURCE_TYPE

    def __init__(
        self,
        repository: EgressProfileRepository,
        *,
        target_owner_ref: OwnerRef,
    ) -> None:
        self._repository = repository
        self._target_owner_ref = target_owner_ref

    async def preflight(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> None:
        del resource, context
        snapshot = _require_snapshot(value)
        _require_missing_profile(self._repository, snapshot.definition.profile_id)
        if any(revision.trust.value != "unverified" for revision in snapshot.revisions):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "decoded imported egress profiles must be unverified",
            )

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
                    replace(snapshot.definition, owner_ref=self._target_owner_ref),
                    revision.revision,
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
                    self._repository.compensate_profile_creation(
                        snapshot.definition.profile_id,
                        expected_current_revision=applied_revision,
                    )
                except Exception as rollback_error:
                    raise ContractError(
                        ErrorCode.BACKEND_ERROR,
                        "egress profile import failed and compensation also failed",
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
                "egress profile rollback token must match the imported profile ID",
            )
        self._repository.compensate_profile_creation(
            token,
            expected_current_revision=snapshot.definition.current_revision,
        )


def _definition_at(
    final_definition: EgressProfileDefinition,
    revision: int,
    *,
    is_final: bool,
) -> EgressProfileDefinition:
    if is_final:
        return final_definition
    created_at: datetime = final_definition.created_at
    return replace(
        final_definition,
        current_revision=revision,
        enabled=True,
        updated_at=created_at,
    )


def _require_snapshot(value: object) -> EgressProfilePortableSnapshot:
    if not isinstance(value, EgressProfilePortableSnapshot):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "egress profile mutation handler received the wrong decoded resource type",
        )
    return value


def _require_missing_profile(
    repository: EgressProfileRepository,
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
        f"egress profile appeared after import preview: {profile_id}",
    )


__all__ = ["EgressProfileImportMutationHandler"]
