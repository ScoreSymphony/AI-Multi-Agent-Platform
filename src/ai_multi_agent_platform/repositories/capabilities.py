"""Canonical repository capability definitions and risk metadata."""

from __future__ import annotations

import ai_multi_agent_platform.capabilities as capability_contracts
import ai_multi_agent_platform.repositories.models as repository_models


READ_OPERATIONS = frozenset(
    {
        repository_models.RepositoryOperation.DISCOVER,
        repository_models.RepositoryOperation.READ,
        repository_models.RepositoryOperation.MATERIALIZE,
        repository_models.RepositoryOperation.INSPECT_REFS,
        repository_models.RepositoryOperation.STATUS,
        repository_models.RepositoryOperation.DIFF,
        repository_models.RepositoryOperation.ISSUE_READ,
        repository_models.RepositoryOperation.CHANGE_REQUEST_READ,
        repository_models.RepositoryOperation.EVENT_RECEIVE,
    }
)

LOCAL_WRITE_OPERATIONS = frozenset(
    {
        repository_models.RepositoryOperation.FETCH,
        repository_models.RepositoryOperation.CREATE_BRANCH,
        repository_models.RepositoryOperation.CHECKOUT,
        repository_models.RepositoryOperation.COMMIT,
    }
)

EXTERNAL_SIDE_EFFECT_OPERATIONS = frozenset(
    {
        repository_models.RepositoryOperation.PUSH,
        repository_models.RepositoryOperation.ISSUE_WRITE,
        repository_models.RepositoryOperation.CHANGE_REQUEST_WRITE,
    }
)

CREDENTIAL_OPERATIONS = frozenset(
    {
        repository_models.RepositoryOperation.FETCH,
        repository_models.RepositoryOperation.PUSH,
        repository_models.RepositoryOperation.ISSUE_READ,
        repository_models.RepositoryOperation.ISSUE_WRITE,
        repository_models.RepositoryOperation.CHANGE_REQUEST_READ,
        repository_models.RepositoryOperation.CHANGE_REQUEST_WRITE,
    }
)


def repository_capability(
    operation: repository_models.RepositoryOperation,
    *,
    requires_credentials: bool = False,
    supported: bool = True,
) -> repository_models.RepositoryCapability:
    if operation in LOCAL_WRITE_OPERATIONS:
        side_effects = capability_contracts.SideEffectClassification.LOCAL_WRITE
    elif operation in EXTERNAL_SIDE_EFFECT_OPERATIONS:
        side_effects = capability_contracts.SideEffectClassification.EXTERNAL
    else:
        side_effects = capability_contracts.SideEffectClassification.NONE
    return repository_models.RepositoryCapability(
        operation=operation,
        side_effects=side_effects,
        requires_credentials=requires_credentials,
        supported=supported,
    )


def repository_capability_specs() -> tuple[capability_contracts.CapabilitySpec, ...]:
    """Return #12-compatible capability definitions for canonical repository operations."""

    specs: list[capability_contracts.CapabilitySpec] = []
    for operation in repository_models.RepositoryOperation:
        capability = repository_capability(
            operation,
            requires_credentials=operation in CREDENTIAL_OPERATIONS,
        )
        sensitive = capability.side_effects in {
            capability_contracts.SideEffectClassification.EXTERNAL,
            capability_contracts.SideEffectClassification.DESTRUCTIVE,
        }
        specs.append(
            capability_contracts.CapabilitySpec(
                capability_id=operation.value,
                name=operation.value,
                description=f"Canonical provider-neutral {operation.value} operation",
                tags=("repository", "git"),
                safety=(
                    capability_contracts.SafetyClassification.RESTRICTED
                    if sensitive
                    else capability_contracts.SafetyClassification.STANDARD
                ),
                side_effects=capability.side_effects,
                required_permissions=(operation.value,),
                credential_requirement=(
                    capability_contracts.CredentialRequirement.REQUIRED
                    if capability.requires_credentials
                    else capability_contracts.CredentialRequirement.NONE
                ),
                features=("provider-neutral",),
            )
        )
    return tuple(specs)


LOCAL_GIT_CAPABILITIES = tuple(
    repository_capability(operation)
    for operation in (
        repository_models.RepositoryOperation.DISCOVER,
        repository_models.RepositoryOperation.READ,
        repository_models.RepositoryOperation.MATERIALIZE,
        repository_models.RepositoryOperation.FETCH,
        repository_models.RepositoryOperation.INSPECT_REFS,
        repository_models.RepositoryOperation.CREATE_BRANCH,
        repository_models.RepositoryOperation.CHECKOUT,
        repository_models.RepositoryOperation.STATUS,
        repository_models.RepositoryOperation.DIFF,
        repository_models.RepositoryOperation.COMMIT,
        repository_models.RepositoryOperation.PUSH,
    )
)
