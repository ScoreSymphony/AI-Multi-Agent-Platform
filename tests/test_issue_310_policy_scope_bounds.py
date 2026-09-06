from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationGate,
    AuthorizationPolicyProfileContent,
    AuthorizationPolicyProfileRevision,
    AuthorizationPolicyProvenance,
    AuthorizationPolicyScopeConstraints,
    LocalAuthorizationProvider,
    ProposedAction,
    ResourceType,
    compile_local_principal_policy,
)


def _content(
    *,
    project_ids: tuple[str, ...] = (),
    organization_ids: tuple[str, ...] = (),
    team_ids: tuple[str, ...] = (),
) -> AuthorizationPolicyProfileContent:
    return AuthorizationPolicyProfileContent(
        name="Bounded policy",
        allowed_actions=(AuthorizationAction.READ,),
        resource_types=(ResourceType.FILE,),
        scope_constraints=AuthorizationPolicyScopeConstraints(
            project_ids=project_ids,
            organization_ids=organization_ids,
            team_ids=team_ids,
        ),
        provenance=AuthorizationPolicyProvenance(
            created_by="user:owner",
            source="local",
        ),
    )


def test_local_compiler_inherits_outer_project_organization_and_team_scope() -> None:
    project_id = new_id("project")
    organization_id = new_id("organization")
    team_id = new_id("team")
    revision = AuthorizationPolicyProfileRevision(
        policy_profile_id=new_id("authorization_policy_profile"),
        revision=1,
        owner_ref=OwnerRef(type="user", id="owner"),
        content=_content(),
        project_id=project_id,
        organization_id=organization_id,
        team_id=team_id,
    )

    policy = compile_local_principal_policy(
        revision,
        principal_ref="agent:worker",
        actor_types=(ActorType.AGENT,),
    )

    assert policy.project_ids == frozenset({project_id})
    assert policy.organization_ids == frozenset({organization_id})
    assert policy.team_ids == frozenset({team_id})

    action = ProposedAction(
        AuthorizationContext(
            actor=ActorIdentity("agent:worker", ActorType.AGENT),
            action=AuthorizationAction.READ,
            resource_type=ResourceType.FILE,
            resource_id="file:one",
            operation=OperationContext(
                correlation_id="issue-310-scope-bound",
                owner_type="service",
                owner_id="tests",
                project_id=new_id("project"),
            ),
            organization_id=organization_id,
            team_id=team_id,
        )
    )
    gate = AuthorizationGate(LocalAuthorizationProvider((policy,)))
    with pytest.raises(ContractError) as captured:
        asyncio.run(gate.enforce(action))
    assert captured.value.code is ErrorCode.FORBIDDEN


@pytest.mark.parametrize("scope_kind", ["project", "organization", "team"])
def test_revision_rejects_permission_constraint_outside_outer_scope(scope_kind: str) -> None:
    project_id = new_id("project")
    organization_id = new_id("organization")
    team_id = new_id("team")
    other_project = new_id("project")
    other_organization = new_id("organization")
    other_team = new_id("team")

    content = _content(
        project_ids=(other_project,) if scope_kind == "project" else (),
        organization_ids=(other_organization,) if scope_kind == "organization" else (),
        team_ids=(other_team,) if scope_kind == "team" else (),
    )

    with pytest.raises(ValueError, match="outside profile outer"):
        AuthorizationPolicyProfileRevision(
            policy_profile_id=new_id("authorization_policy_profile"),
            revision=1,
            owner_ref=OwnerRef(type="user", id="owner"),
            content=content,
            project_id=project_id,
            organization_id=organization_id,
            team_id=team_id,
        )
