from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    AuthorizationPolicyProfileCallContext,
    AuthorizationPolicyProfileContent,
    AuthorizationPolicyProfileDefinition,
    AuthorizationPolicyProfileRef,
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
            correlation_id="policy-profile-state-probing",
            owner_type="user",
            owner_id="attacker",
        ),
        actor_ref="user:attacker",
    )


def _content(name: str) -> AuthorizationPolicyProfileContent:
    return AuthorizationPolicyProfileContent(
        name=name,
        allowed_actions=(AuthorizationAction.READ,),
        resource_types=(ResourceType.GENERIC,),
        provenance=AuthorizationPolicyProvenance(
            created_by="user:owner",
            source="local",
        ),
    )


def _profile(
    repository: InMemoryAuthorizationPolicyProfileRepository,
    *,
    enabled: bool = True,
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
            content=_content("State probing fixture"),
            created_at=definition.created_at,
        ),
    )
    return definition


def _service(
    repository: InMemoryAuthorizationPolicyProfileRepository,
) -> AuthorizationPolicyProfileService:
    return AuthorizationPolicyProfileService(
        repository,
        AuthorizationGate(LocalAuthorizationProvider()),
    )


def test_revise_authorizes_before_revealing_current_revision() -> None:
    repository = InMemoryAuthorizationPolicyProfileRepository()
    definition = _profile(repository)
    service = _service(repository)

    with pytest.raises(ContractError) as captured:
        asyncio.run(
            service.revise(
                definition.policy_profile_id,
                _content("Unauthorized revision probe"),
                _context(),
                expected_revision=999,
            )
        )

    assert captured.value.code is ErrorCode.FORBIDDEN
    assert repository.get_profile(definition.policy_profile_id).current_revision == 1


def test_assign_authorizes_before_revealing_missing_revision() -> None:
    repository = InMemoryAuthorizationPolicyProfileRepository()
    definition = _profile(repository)
    service = _service(repository)

    with pytest.raises(ContractError) as captured:
        asyncio.run(
            service.assign(
                profile_ref=AuthorizationPolicyProfileRef(
                    definition.policy_profile_id,
                    999,
                ),
                principal_ref="user:target",
                actor_types=(ActorType.HUMAN,),
                context=_context(),
            )
        )

    assert captured.value.code is ErrorCode.FORBIDDEN
    assert repository.list_assignments(policy_profile_id=definition.policy_profile_id) == ()


def test_assign_authorizes_before_revealing_disabled_state() -> None:
    repository = InMemoryAuthorizationPolicyProfileRepository()
    definition = _profile(repository)
    repository.set_enabled(replace(definition, enabled=False))
    service = _service(repository)

    with pytest.raises(ContractError) as captured:
        asyncio.run(
            service.assign(
                profile_ref=AuthorizationPolicyProfileRef(
                    definition.policy_profile_id,
                    1,
                ),
                principal_ref="user:target",
                actor_types=(ActorType.HUMAN,),
                context=_context(),
            )
        )

    assert captured.value.code is ErrorCode.FORBIDDEN
    assert repository.list_assignments(policy_profile_id=definition.policy_profile_id) == ()
