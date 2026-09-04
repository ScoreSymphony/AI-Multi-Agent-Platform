"""Canonical repository capability definitions and risk metadata."""

from __future__ import annotations

from ai_multi_agent_platform.capabilities import CapabilitySpec
from ai_multi_agent_platform.capabilities import CredentialRequirement
from ai_multi_agent_platform.capabilities import SafetyClassification
from ai_multi_agent_platform.capabilities import SideEffectClassification

from .models import RepositoryCapability, RepositoryOperation


READ_OPERATIONS = frozenset(
    {
        RepositoryOperation.DISCOVER,
        RepositoryOperation.READ,
        RepositoryOperation.MATERIALIZE,
        RepositoryOperation.INSPECT_REFS,
        RepositoryOperation.STATUS,
        RepositoryOperation.DIFF,
        RepositoryOperation.ISSUE_READ,
        RepositoryOperation.CHANGE_REQUEST_READ,
        RepositoryOperation.EVENT_RECEIVE,
    }
)

LOCAL_WRITE_OPERATIONS = frozenset(
    {
        RepositoryOperation.FETCH,
        RepositoryOperation.CREATE_BRANCH,
        RepositoryOperation.CHECKOUT,
        RepositoryOperation.COMMIT,
    }
)

EXTERNAL_SIDE_EFFECT_OPERATIONS = frozenset(
    {
        RepositoryOperation.PUSH,
        RepositoryOperation.ISSUE_WRITE,
        RepositoryOperation.CHANGE_REQUEST_WRITE,
    }
)

CREDENTIAL_OPERATIONS = frozenset(
    {
        RepositoryOperation.FETCH,
        RepositoryOperation.PUSH,
        RepositoryOperation.ISSUE_READ,
        RepositoryOperation.ISSUE_WRITE,
        RepositoryOperation.CHANGE_REQUEST_READ,
        RepositoryOperation.CHANGE_REQUEST_WRITE,
    }
)


def repository_capability(
    operation: RepositoryOperation,
    *,
    requires_credentials: bool = False,
    supported: bool = True,
) -> RepositoryCapability:
    if operation in LOCAL_WRITE_OPERATIONS:
        side_effects = SideEffectClassification.LOCAL_WRITE
    elif operation in EXTERNAL_SIDE_EFFECT_OPERATIONS:
        side_effects = SideEffectClassification.EXTERNAL
    else:
        side_effects = SideEffectClassification.NONE
    return RepositoryCapability(
        operation=operation,
        side_effects=side_effects,
        requires_credentials=requires_credentials,
        supported=supported,
    )


def repository_capability_specs() -> tuple[CapabilitySpec, ...]:
    """Return #12-compatible capability definitions for canonical repository operations."""

    specs: list[CapabilitySpec] = []
    for operation in RepositoryOperation:
        capability = repository_capability(
            operation,
            requires_credentials=operation in CREDENTIAL_OPERATIONS,
        )
        sensitive = capability.side_effects in {
            SideEffectClassification.EXTERNAL,
            SideEffectClassification.DESTRUCTIVE,
        }
        specs.append(
            CapabilitySpec(
                capability_id=operation.value,
                name=operation.value,
                description=f"Canonical provider-neutral {operation.value} operation",
                tags=("repository", "git"),
                safety=(
                    SafetyClassification.RESTRICTED if sensitive else SafetyClassification.STANDARD
                ),
                side_effects=capability.side_effects,
                required_permissions=(operation.value,),
                credential_requirement=(
                    CredentialRequirement.REQUIRED
                    if capability.requires_credentials
                    else CredentialRequirement.NONE
                ),
                features=("provider-neutral",),
            )
        )
    return tuple(specs)


LOCAL_GIT_CAPABILITIES = tuple(
    repository_capability(operation)
    for operation in (
        RepositoryOperation.DISCOVER,
        RepositoryOperation.READ,
        RepositoryOperation.MATERIALIZE,
        RepositoryOperation.FETCH,
        RepositoryOperation.INSPECT_REFS,
        RepositoryOperation.CREATE_BRANCH,
        RepositoryOperation.CHECKOUT,
        RepositoryOperation.STATUS,
        RepositoryOperation.DIFF,
        RepositoryOperation.COMMIT,
        RepositoryOperation.PUSH,
    )
)
