from __future__ import annotations

import asyncio

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
    LocalPrincipalPolicy,
    ResourceType,
)


def _content() -> AuthorizationPolicyProfileContent:
    return AuthorizationPolicyProfileContent(
        name="Management scope fixture",
        allowed_actions=(AuthorizationAction.READ,),
        resource_types=(ResourceType.GENERIC,),
        provenance=AuthorizationPolicyProvenance(
            created_by="user:project-admin",
            source="local",
        ),
    )


def _context(project_id: str) -> AuthorizationPolicyProfileCallContext:
    return AuthorizationPolicyProfileCallContext(
        operation=OperationContext(
            correlation_id="issue-310-management-scope",
            owner_type="user",
            owner_id="project-admin",
            project_id=project_id,
        ),
        actor_ref="user:project-admin",
    )


def _project_admin_gate(project_id: str) -> AuthorizationGate:
    return AuthorizationGate(
        LocalAuthorizationProvider(
            (
                LocalPrincipalPolicy(
                    principal_ref="user:project-admin",
                    actor_types=frozenset({ActorType.HUMAN}),
                    resource_types=frozenset({ResourceType.GENERIC}),
                    project_ids=frozenset({project_id}),
                    administrator=True,
                ),
            )
        )
    )


def test_project_scoped_admin_cannot_create_global_policy_via_caller_context() -> None:
    project_id = new_id("project")
    repository = InMemoryAuthorizationPolicyProfileRepository()
    service = AuthorizationPolicyProfileService(repository, _project_admin_gate(project_id))

    with pytest.raises(ContractError) as captured:
        asyncio.run(
            service.create(
                owner_ref=OwnerRef(type="user", id="project-admin"),
                content=_content(),
                context=_context(project_id),
            )
        )
    assert captured.value.code is ErrorCode.FORBIDDEN
    assert repository.list_profiles() == ()


def test_project_scoped_admin_can_create_policy_explicitly_bound_to_its_project() -> None:
    project_id = new_id("project")
    repository = InMemoryAuthorizationPolicyProfileRepository()
    service = AuthorizationPolicyProfileService(repository, _project_admin_gate(project_id))

    created = asyncio.run(
        service.create(
            owner_ref=OwnerRef(type="user", id="project-admin"),
            content=_content(),
            context=_context(project_id),
            project_id=project_id,
        )
    )
    assert created.project_id == project_id


def test_project_scoped_admin_cannot_assign_preexisting_global_policy() -> None:
    project_id = new_id("project")
    repository = InMemoryAuthorizationPolicyProfileRepository()
    policy_profile_id = new_id("authorization_policy_profile")
    owner = OwnerRef(type="service", id="bootstrap")
    definition = AuthorizationPolicyProfileDefinition(
        policy_profile_id=policy_profile_id,
        owner_ref=owner,
        current_revision=1,
    )
    repository.create_profile(
        definition,
        AuthorizationPolicyProfileRevision(
            policy_profile_id=policy_profile_id,
            revision=1,
            owner_ref=owner,
            content=_content(),
            created_at=definition.created_at,
        ),
    )
    service = AuthorizationPolicyProfileService(repository, _project_admin_gate(project_id))

    with pytest.raises(ContractError) as captured:
        asyncio.run(
            service.assign(
                profile_ref=AuthorizationPolicyProfileRef(policy_profile_id, 1),
                principal_ref="agent:global",
                actor_types=(ActorType.AGENT,),
                context=_context(project_id),
            )
        )
    assert captured.value.code is ErrorCode.FORBIDDEN
    assert repository.list_assignments() == ()
