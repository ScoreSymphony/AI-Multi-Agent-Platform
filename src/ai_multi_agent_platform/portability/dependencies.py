"""Typed helpers for portable resource-to-resource dependencies."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode

from .models import DependencyKind, DependencyRequirement

_RESOURCE_DEPENDENCY_SEPARATOR = ":"


@dataclass(frozen=True, slots=True)
class ResourceDependencyRef:
    resource_type: str
    resource_id: str

    def __post_init__(self) -> None:
        if not self.resource_type.strip():
            raise ValueError("resource dependency type must not be blank")
        if not self.resource_id.strip():
            raise ValueError("resource dependency ID must not be blank")
        if _RESOURCE_DEPENDENCY_SEPARATOR in self.resource_type:
            raise ValueError("resource dependency type must not contain ':'")

    @property
    def identifier(self) -> str:
        return f"{self.resource_type}{_RESOURCE_DEPENDENCY_SEPARATOR}{self.resource_id}"


def resource_dependency(
    resource_type: str,
    resource_id: str,
    *,
    required: bool = True,
    version_constraint: str | None = None,
    purpose: str | None = None,
) -> DependencyRequirement:
    """Build the canonical package convention for one resource dependency."""

    reference = ResourceDependencyRef(resource_type=resource_type, resource_id=resource_id)
    return DependencyRequirement(
        kind=DependencyKind.RESOURCE,
        identifier=reference.identifier,
        required=required,
        version_constraint=version_constraint,
        purpose=purpose,
    )


def parse_resource_dependency(requirement: DependencyRequirement) -> ResourceDependencyRef:
    """Decode a RESOURCE dependency without guessing from arbitrary identifiers."""

    if requirement.kind is not DependencyKind.RESOURCE:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "dependency is not a canonical resource dependency",
            details={"dependency_kind": requirement.kind.value},
        )
    resource_type, separator, resource_id = requirement.identifier.partition(
        _RESOURCE_DEPENDENCY_SEPARATOR
    )
    if not separator or not resource_type.strip() or not resource_id.strip():
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "portable resource dependency must use '<resource_type>:<resource_id>'",
            details={"identifier": requirement.identifier},
        )
    try:
        return ResourceDependencyRef(resource_type=resource_type, resource_id=resource_id)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "invalid portable resource dependency identifier",
            details={"identifier": requirement.identifier},
        ) from exc
