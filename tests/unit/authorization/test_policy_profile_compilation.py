"""Migrated under #722; original coverage tracked issue #310."""


# ruff: noqa: F401


from __future__ import annotations


import asyncio


import json


from pathlib import Path


import pytest


from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext


from ai_multi_agent_platform.domain import OwnerRef, new_id


from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ProposedAction,
    ResourceType,
)


from ai_multi_agent_platform.security.policy_profile_persistence import (
    JsonAuthorizationPolicyProfileRepository,
    policy_profile_revision_to_json,
)


from ai_multi_agent_platform.security.policy_profiles import (
    AuthorizationPolicyConditions,
    AuthorizationPolicyProfileCallContext,
    AuthorizationPolicyProfileContent,
    AuthorizationPolicyProfileDefinition,
    AuthorizationPolicyProfileRef,
    AuthorizationPolicyProfileRevision,
    AuthorizationPolicyProfileService,
    AuthorizationPolicyProvenance,
    AuthorizationPolicyScopeConstraints,
    InMemoryAuthorizationPolicyProfileRepository,
    compile_local_principal_policy,
)


def _operation(
    *,
    project_id: str | None = None,
    correlation_id: str = "corr-310",
) -> OperationContext:
    return OperationContext(
        correlation_id=correlation_id,
        owner_type="user",
        owner_id="admin",
        project_id=project_id,
    )


def _call_context(
    actor_ref: str,
    *,
    project_id: str | None = None,
    organization_id: str | None = None,
    team_id: str | None = None,
) -> AuthorizationPolicyProfileCallContext:
    return AuthorizationPolicyProfileCallContext(
        operation=_operation(project_id=project_id),
        actor_ref=actor_ref,
        organization_id=organization_id,
        team_id=team_id,
    )


def _admin_gate(
    actor_ref: str = "user:admin",
    *,
    project_ids: frozenset[str] = frozenset(),
    organization_ids: frozenset[str] = frozenset(),
    team_ids: frozenset[str] = frozenset(),
) -> AuthorizationGate:
    return AuthorizationGate(
        LocalAuthorizationProvider(
            (
                LocalPrincipalPolicy(
                    principal_ref=actor_ref,
                    actor_types=frozenset({ActorType.HUMAN}),
                    resource_types=frozenset({ResourceType.GENERIC}),
                    project_ids=project_ids,
                    organization_ids=organization_ids,
                    team_ids=team_ids,
                    administrator=True,
                ),
            )
        )
    )


def _content(
    name: str = "Project operator",
    *,
    project_ids: tuple[str, ...] = (),
    provenance: AuthorizationPolicyProvenance | None = None,
) -> AuthorizationPolicyProfileContent:
    return AuthorizationPolicyProfileContent(
        name=name,
        description="Reusable provider-neutral permissions",
        allowed_actions=(AuthorizationAction.READ, AuthorizationAction.EXECUTE),
        approval_required_actions=(AuthorizationAction.MODIFY,),
        resource_types=(ResourceType.FILE, ResourceType.TOOL),
        scope_constraints=AuthorizationPolicyScopeConstraints(project_ids=project_ids),
        provenance=provenance
        or AuthorizationPolicyProvenance(created_by="user:admin", source="local"),
    )


def _direct_profile(
    repository: InMemoryAuthorizationPolicyProfileRepository,
    *,
    content: AuthorizationPolicyProfileContent,
    project_id: str | None = None,
    organization_id: str | None = None,
    team_id: str | None = None,
) -> AuthorizationPolicyProfileDefinition:
    profile_id = new_id("authorization_policy_profile")
    definition = AuthorizationPolicyProfileDefinition(
        policy_profile_id=profile_id,
        owner_ref=OwnerRef(type="user", id="admin"),
        current_revision=1,
        project_id=project_id,
        organization_id=organization_id,
        team_id=team_id,
    )
    revision = AuthorizationPolicyProfileRevision(
        policy_profile_id=profile_id,
        revision=1,
        owner_ref=definition.owner_ref,
        content=content,
        project_id=project_id,
        organization_id=organization_id,
        team_id=team_id,
        created_at=definition.created_at,
    )
    repository.create_profile(definition, revision)
    return definition


def test_local_reference_provider_compiles_canonical_revision_without_owning_identity() -> None:
    project_id = new_id("project")
    profile_id = new_id("authorization_policy_profile")
    revision = AuthorizationPolicyProfileRevision(
        policy_profile_id=profile_id,
        revision=3,
        owner_ref=OwnerRef(type="user", id="admin"),
        content=_content(project_ids=(project_id,)),
        project_id=project_id,
    )
    policy = compile_local_principal_policy(
        revision,
        principal_ref="agent:worker",
        actor_types=(ActorType.AGENT,),
    )
    actor = ActorIdentity("agent:worker", ActorType.AGENT)
    proposed = ProposedAction(
        AuthorizationContext(
            actor=actor,
            action=AuthorizationAction.READ,
            resource_type=ResourceType.FILE,
            resource_id="file:one",
            operation=_operation(project_id=project_id),
        )
    )

    first = AuthorizationGate(LocalAuthorizationProvider((policy,), provider_id="local-a"))
    second = AuthorizationGate(LocalAuthorizationProvider((policy,), provider_id="local-b"))
    assert asyncio.run(first.enforce(proposed)).allowed
    assert asyncio.run(second.enforce(proposed)).allowed
    assert revision.ref == AuthorizationPolicyProfileRef(profile_id, 3)
    assert revision.ref.token == f"{profile_id}@3"


def test_unsupported_provider_neutral_conditions_fail_closed_in_local_compiler() -> None:
    revision = AuthorizationPolicyProfileRevision(
        policy_profile_id=new_id("authorization_policy_profile"),
        revision=1,
        owner_ref=OwnerRef(type="service", id="tests"),
        content=AuthorizationPolicyProfileContent(
            name="Conditioned",
            allowed_actions=(AuthorizationAction.READ,),
            conditions=AuthorizationPolicyConditions(required_security_labels=("confidential",)),
            provenance=AuthorizationPolicyProvenance(created_by="service:tests", source="local"),
        ),
    )

    with pytest.raises(ContractError) as captured:
        compile_local_principal_policy(
            revision,
            principal_ref="agent:test",
            actor_types=(ActorType.AGENT,),
        )
    assert captured.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
