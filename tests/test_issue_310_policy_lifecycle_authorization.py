from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.security import (
    AuthorizationAction,
    AuthorizationGate,
    AuthorizationPolicyProfileCallContext,
    AuthorizationPolicyProfileContent,
    AuthorizationPolicyProfileDefinition,
    AuthorizationPolicyProfileRevision,
    AuthorizationPolicyProfileService,
    AuthorizationPolicyProvenance,
    InMemoryAuthorizationPolicyProfileRepository,
    LocalAuthorizationProvider,
    ResourceType,
)


def _context() -> AuthorizationPolicyProfileCallContext:
    return AuthorizationPolicyProfileCallContext(
        operation=OperationContext(
            correlation_id="issue-310-idempotent-lifecycle",
            owner_type="user",
            owner_id="attacker",
        ),
        actor_ref="user:attacker",
    )


def _profile(
    repository: InMemoryAuthorizationPolicyProfileRepository,
    *,
    enabled: bool,
) -> AuthorizationPolicyProfileDefinition:
    profile_id = new_id("authorization_policy_profile")
    owner = OwnerRef(type="user", id="owner")
    definition = AuthorizationPolicyProfileDefinition(
        policy_profile_id=profile_id,
        owner_ref=owner,
        current_revision=1,
        enabled=enabled,
    )
    repository.create_profile(
        definition,
        AuthorizationPolicyProfileRevision(
            policy_profile_id=profile_id,
            revision=1,
            owner_ref=owner,
            content=AuthorizationPolicyProfileContent(
                name="Lifecycle authorization fixture",
                allowed_actions=(AuthorizationAction.READ,),
                resource_types=(ResourceType.GENERIC,),
                provenance=AuthorizationPolicyProvenance(
                    created_by="user:owner",
                    source="local",
                ),
            ),
            created_at=definition.created_at,
        ),
    )
    return definition


def test_enable_already_enabled_profile_still_requires_admin_authority() -> None:
    repository = InMemoryAuthorizationPolicyProfileRepository()
    definition = _profile(repository, enabled=True)
    service = AuthorizationPolicyProfileService(
        repository,
        AuthorizationGate(LocalAuthorizationProvider()),
    )

    with pytest.raises(ContractError) as captured:
        asyncio.run(service.enable(definition.policy_profile_id, _context()))

    assert captured.value.code is ErrorCode.FORBIDDEN
    assert repository.get_profile(definition.policy_profile_id).enabled is True


def test_disable_already_disabled_profile_still_requires_admin_authority() -> None:
    repository = InMemoryAuthorizationPolicyProfileRepository()
    definition = _profile(repository, enabled=True)
    repository.set_enabled(replace(definition, enabled=False))
    service = AuthorizationPolicyProfileService(
        repository,
        AuthorizationGate(LocalAuthorizationProvider()),
    )

    with pytest.raises(ContractError) as captured:
        asyncio.run(service.disable(definition.policy_profile_id, _context()))

    assert captured.value.code is ErrorCode.FORBIDDEN
    assert repository.get_profile(definition.policy_profile_id).enabled is False
