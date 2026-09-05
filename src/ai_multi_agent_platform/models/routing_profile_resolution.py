"""Canonical exact-revision resolution for durable model-routing profiles."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.contracts import ContractError, ErrorCode

from .routing_profile_repository import ModelRoutingProfileRepository
from .routing_profiles import ModelRoutingProfileRef, ModelRoutingProfileRevision


@dataclass(slots=True)
class ModelRoutingProfileResolver:
    """Resolve one exact enabled profile revision without owning routing decisions."""

    repository: ModelRoutingProfileRepository

    def resolve(
        self,
        value: str,
        *,
        project_id: str | None = None,
    ) -> ModelRoutingProfileRevision:
        try:
            ref = ModelRoutingProfileRef.parse(value)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "model-routing profile references must pin an exact canonical revision",
                details={"routing_profile_ref": value},
            ) from exc

        definition = self.repository.get_definition(ref.profile_id)
        if not definition.enabled:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                f"model-routing profile is disabled: {ref.profile_id}",
                details={"routing_profile_ref": ref.canonical_ref},
            )
        if definition.project_id is not None and definition.project_id != project_id:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "model-routing profile is outside the active Project scope",
                details={
                    "routing_profile_ref": ref.canonical_ref,
                    "profile_project_id": definition.project_id,
                    "active_project_id": project_id,
                },
            )
        revision = self.repository.get_revision(ref)
        if (
            revision.owner_ref != definition.owner_ref
            or revision.project_id != definition.project_id
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "model-routing profile revision scope disagrees with its stable definition",
                details={"routing_profile_ref": ref.canonical_ref},
            )
        return revision


__all__ = ["ModelRoutingProfileResolver"]
