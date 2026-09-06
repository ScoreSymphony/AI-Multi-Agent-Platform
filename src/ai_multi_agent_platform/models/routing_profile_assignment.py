"""#15 authorization boundary for assigning durable model-routing profiles."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import (
    AuthorizationProvider,
    ContractError,
    ErrorCode,
    OperationContext,
)
from ai_multi_agent_platform.contracts.authorization import (
    AuthorizationRequest,
    normalize_authorization_decision,
)

from .routing_profile_repository import ModelRoutingProfileRepository
from .routing_profiles import ModelRoutingProfileRef, ModelRoutingProfileRevision


class ModelRoutingProfileAssignmentGate:
    """Authorize use of one exact enabled routing-profile revision by another resource."""

    def __init__(
        self,
        repository: ModelRoutingProfileRepository,
        *,
        authorization: AuthorizationProvider | None = None,
    ) -> None:
        self.repository = repository
        self.authorization = authorization

    async def authorize(
        self,
        ref: ModelRoutingProfileRef,
        *,
        principal_ref: str,
        context: OperationContext,
        actor_type: str = "service",
    ) -> ModelRoutingProfileRevision:
        if not principal_ref.strip():
            raise ContractError(ErrorCode.UNAUTHORIZED, "principal_ref must not be blank")
        if not actor_type.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "actor_type must not be blank")

        definition = self.repository.get_definition(ref.profile_id)
        if definition.project_id is not None and context.project_id != definition.project_id:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "routing profile project scope does not match assignment context",
                details={
                    "routing_profile_ref": ref.canonical_ref,
                    "profile_project_id": definition.project_id,
                    "assignment_project_id": context.project_id,
                },
            )
        if not definition.enabled:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                f"routing profile is disabled: {ref.profile_id}",
                details={"routing_profile_ref": ref.canonical_ref},
            )

        if self.authorization is None:
            if (
                context.owner_type != definition.owner_ref.type
                or context.owner_id != definition.owner_ref.id
            ):
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "routing profile owner scope does not match assignment context",
                    details={"routing_profile_ref": ref.canonical_ref},
                )
        else:
            decision = normalize_authorization_decision(
                await self.authorization.authorize(
                    AuthorizationRequest(
                        principal_ref=principal_ref,
                        action="model-routing-profile:assign",
                        resource_ref=ref.canonical_ref,
                        context=context,
                        actor_type=actor_type,
                        resource_type="model_routing_profile",
                        organization_id=(
                            definition.owner_ref.id
                            if definition.owner_ref.type == "organization"
                            else None
                        ),
                        team_id=(
                            definition.owner_ref.id if definition.owner_ref.type == "team" else None
                        ),
                    )
                )
            )
            if not decision.allowed:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    decision.reason or "routing profile assignment authorization denied",
                    details={
                        "action": "model-routing-profile:assign",
                        "routing_profile_ref": ref.canonical_ref,
                    },
                )

        revision = self.repository.get_revision(ref)
        if (
            revision.owner_ref != definition.owner_ref
            or revision.project_id != definition.project_id
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "routing profile revision scope disagrees with its stable definition",
                details={"routing_profile_ref": ref.canonical_ref},
            )
        return revision


__all__ = ["ModelRoutingProfileAssignmentGate"]
