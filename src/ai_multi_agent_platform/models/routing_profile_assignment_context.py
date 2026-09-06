"""Request-scoped access context for canonical routing-profile assignment consumers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.domain import OwnerRef

from .routing_profile_assignment import ModelRoutingProfileAssignmentGate
from .routing_profiles import ModelRoutingProfileRef, ModelRoutingProfileRevision


@dataclass(frozen=True, slots=True)
class RoutingProfileAssignmentAccess:
    """Authenticated caller identity reused by nested assignment consumers."""

    gate: ModelRoutingProfileAssignmentGate
    principal_ref: str
    actor_type: str
    correlation_id: str
    causation_id: str

    def __post_init__(self) -> None:
        if not self.principal_ref.strip():
            raise ValueError("routing-profile assignment principal_ref must not be blank")
        if not self.actor_type.strip():
            raise ValueError("routing-profile assignment actor_type must not be blank")
        if not self.correlation_id.strip():
            raise ValueError("routing-profile assignment correlation_id must not be blank")
        if not self.causation_id.strip():
            raise ValueError("routing-profile assignment causation_id must not be blank")

    async def authorize(
        self,
        ref: ModelRoutingProfileRef,
        *,
        owner_ref: OwnerRef,
        project_id: str | None,
    ) -> ModelRoutingProfileRevision:
        return await self.gate.authorize(
            ref,
            principal_ref=self.principal_ref,
            context=OperationContext(
                correlation_id=self.correlation_id,
                causation_id=self.causation_id,
                owner_type=owner_ref.type,
                owner_id=owner_ref.id,
                project_id=project_id,
            ),
            actor_type=self.actor_type,
        )


_CURRENT_ASSIGNMENT_ACCESS: ContextVar[RoutingProfileAssignmentAccess | None] = ContextVar(
    "routing_profile_assignment_access",
    default=None,
)


@contextmanager
def activate_routing_profile_assignment_access(
    access: RoutingProfileAssignmentAccess,
) -> Iterator[None]:
    """Expose authenticated assignment identity to nested Template/import consumers."""

    token = _CURRENT_ASSIGNMENT_ACCESS.set(access)
    try:
        yield
    finally:
        _CURRENT_ASSIGNMENT_ACCESS.reset(token)


def current_routing_profile_assignment_access() -> RoutingProfileAssignmentAccess | None:
    return _CURRENT_ASSIGNMENT_ACCESS.get()


def require_routing_profile_assignment_access() -> RoutingProfileAssignmentAccess:
    access = current_routing_profile_assignment_access()
    if access is None:
        raise ContractError(
            ErrorCode.UNAUTHORIZED,
            "canonical routing-profile assignment requires authenticated request context",
        )
    return access


__all__ = [
    "RoutingProfileAssignmentAccess",
    "activate_routing_profile_assignment_access",
    "current_routing_profile_assignment_access",
    "require_routing_profile_assignment_access",
]
