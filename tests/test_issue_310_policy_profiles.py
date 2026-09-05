from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.security.authorization import (
    ActorType,
    AuthorizationAction,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.security.enforcement import AuthorizationGate
from ai_multi_agent_platform.security.policy_profile_persistence import (
    JsonAuthorizationPolicyProfileRepository,
    policy_profile_revision_to_json,
)
from ai_multi_agent_platform.security.policy_profiles import (
    AuthorizationPolicyConditions,
    AuthorizationPolicyProfileCallContext,
    AuthorizationPolicyProfileContent,
    AuthorizationPolicyProfileRef,
    AuthorizationPolicyProfileRevision,
    AuthorizationPolicyProfileService,
    AuthorizationPolicyProvenance,
    AuthorizationPolicyScopeConstraints,
    InMemoryAuthorizationPolicyProfileRepository,
    compile_local_principal_policy,
)


def _operation(actor_ref: str, *, project_id: str | None = None) -> OperationContext:
    return OperationContext(
        correlation_id="corr-issue-310",
        owner_type="user",
        owner_id=actor_ref,
        project_id=project_id,
    )


def _context(
    actor_ref: str,
    *,
    project_id: str | None = None,
    organization_id: str | None = None,
    team_id: str | None = None,
) -> AuthorizationPolicyProfileCallContext:
    return AuthorizationPolicyProfileCallContext(
        operation=_operation(actor_ref, project_id=project_id),
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
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref=actor_ref,
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset(
                    {
                        AuthorizationAction.CREATE,
                        AuthorizationAction.READ,
                        AuthorizationAction.MODIFY,
                        AuthorizationAction.ADMINISTER,
                    }
                ),
                resource_types=frozenset({ResourceType.GENERIC}),
                project_ids=project_ids,
                organization_ids=organization_ids,
                team_ids=team_ids,
            ),
        )
    )
    return AuthorizationGate(provider)


def _content(
    name: str = "Developers",
    *,
    provenance: AuthorizationPolicyProvenance | None = None,
) -> AuthorizationPolicyProfileContent:
    return AuthorizationPolicyProfileContent(
        name=name,
        description="Reusable canonical developer permissions",
        allowed_actions=(AuthorizationAction.READ,),
        approval_required_actions=(AuthorizationAction.EXECUTE,),
        resource_types=(ResourceType.FILE, ResourceType.TOOL),
        provenance=provenance
        or AuthorizationPolicyProvenance(created_by="user:admin", source="local"),
    )


def test_create_and_revise_preserves_exact_historical_revision() -> None:
    repository = InMemoryAuthorizationPolicyProfileRepository()
    service = AuthorizationPolicyProfileService(repository, _admin_gate())
    owner = OwnerRef(type="user", id="user:admin")
    context = _context("user:admin")

    created = asyncio.run(service.create(owner_ref=owner, content=_content(), context=context))
    first = repository.get_revision(created.policy_profile_id, 1)

    updated = asyncio.run(
        service.revise(
            created.policy_profile_id,
            AuthorizationPolicyProfileContent(
                name="Developers v2",
                allowed_actions=(AuthorizationAction.READ, AuthorizationAction.CREATE),
                resource_types=(ResourceType.FILE,),
                provenance=AuthorizationPolicyProvenance(
                    created_by="user:admin",
                    source="local",
                ),
            ),
            context,
            expected_revision=1,
        )
    )

    assert updated.current_revision == 2
    assert repository.get_revision(created.policy_profile_id, 1) == first
    assert repository.get_revision(created.policy_profile_id, 2).content.name == "Developers v2"
    assert AuthorizationPolicyProfileRef(created.policy_profile_id, 1).token.endswith("@1")


def test_json_persistence_restart_preserves_history_assignment_and_disabled_state(
    tmp_path: Path,
) -> None:
    path = tmp_path / "authorization-policy-profiles.json"
    repository = JsonAuthorizationPolicyProfileRepository(path)
    service = AuthorizationPolicyProfileService(repository, _admin_gate())
    owner = OwnerRef(type="user", id="user:admin")
    context = _context("user:admin")

    definition = asyncio.run(service.create(owner_ref=owner, content=_content(), context=context))
    asyncio.run(
        service.revise(
            definition.policy_profile_id,
            AuthorizationPolicyProfileContent(
                name="Developers revised",
                allowed_actions=(AuthorizationAction.READ, AuthorizationAction.MODIFY),
                resource_types=(ResourceType.FILE,),
                provenance=AuthorizationPolicyProvenance(
                    created_by="user:admin",
                    source="local",
                ),
            ),
            context,
            expected_revision=1,
        )
    )
    assignment = asyncio.run(
        service.assign(
            profile_ref=AuthorizationPolicyProfileRef(definition.policy_profile_id, 1),
            principal_ref="agent:developer",
            actor_types=(ActorType.AGENT,),
            context=context,
        )
    )
    asyncio.run(service.disable(definition.policy_profile_id, context))

    restored = JsonAuthorizationPolicyProfileRepository(path)
    restored_definition = restored.get_profile(definition.policy_profile_id)
    restored_assignment = restored.get_assignment(assignment.assignment_id)

    assert restored_definition.current_revision == 2
    assert restored_definition.enabled is False
    assert restored.get_revision(definition.policy_profile_id, 1).content.name == "Developers"
    assert restored.get_revision(definition.policy_profile_id, 2).content.name == "Developers revised"
    assert restored_assignment.profile_ref.revision == 1


def test_exact_revision_compiles_for_replaceable_local_provider_without_identity_change() -> None:
    profile_id = new_id("authorization_policy_profile")
    revision = AuthorizationPolicyProfileRevision(
        policy_profile_id=profile_id,
        revision=3,
        owner_ref=OwnerRef(type="user", id="user:admin"),
        content=_content(),
    )
    local_policy = compile_local_principal_policy(
        revision,
        principal_ref="agent:one",
        actor_types=(ActorType.AGENT,),
    )

    provider_a = LocalAuthorizationProvider((local_policy,), provider_id="provider-a")
    provider_b = LocalAuthorizationProvider((local_policy,), provider_id="provider-b")

    request = OperationContext(correlation_id="provider-replacement")
    from ai_multi_agent_platform.contracts import AuthorizationRequest

    authorization_request = AuthorizationRequest(
        principal_ref="agent:one",
        actor_type=ActorType.AGENT.value,
        action=AuthorizationAction.READ.value,
        resource_type=ResourceType.FILE.value,
        resource_ref="file:any",
        context=request,
    )
    first = asyncio.run(provider_a.authorize(authorization_request))
    second = asyncio.run(provider_b.authorize(authorization_request))

    assert first.allowed and second.allowed
    assert revision.ref == AuthorizationPolicyProfileRef(profile_id, 3)
    assert revision.ref.token == f"{profile_id}@3"


def test_untrusted_imported_profile_cannot_self_grant_assignment_authority() -> None:
    repository = InMemoryAuthorizationPolicyProfileRepository()
    attacker_gate = AuthorizationGate(LocalAuthorizationProvider())
    service = AuthorizationPolicyProfileService(repository, attacker_gate)
    profile_id = new_id("authorization_policy_profile")

    definition_repository = InMemoryAuthorizationPolicyProfileRepository()
    trusted_admin_service = AuthorizationPolicyProfileService(definition_repository, _admin_gate())
    imported = _content(
        provenance=AuthorizationPolicyProvenance(
            created_by="external:package",
            source="portable-package",
            source_reference="package:untrusted",
            imported=True,
            trusted=False,
        )
    )
    created = asyncio.run(
        trusted_admin_service.create(
            owner_ref=OwnerRef(type="user", id="user:admin"),
            content=imported,
            context=_context("user:admin"),
            policy_profile_id=profile_id,
        )
    )
    revision = definition_repository.get_revision(created.policy_profile_id, 1)
    repository.create_profile(created, revision)

    with pytest.raises(ContractError) as captured:
        asyncio.run(
            service.assign(
                profile_ref=revision.ref,
                principal_ref="user:attacker",
                actor_types=(ActorType.HUMAN,),
                context=_context("user:attacker"),
            )
        )

    assert captured.value.code is ErrorCode.FORBIDDEN
    assert repository.list_assignments() == ()


def test_project_organization_and_team_scopes_are_isolated_by_authorization_gate() -> None:
    project_a = new_id("project")
    project_b = new_id("project")
    organization_a = new_id("organization")
    organization_b = new_id("organization")
    team_a = new_id("team")
    team_b = new_id("team")
    gate = _admin_gate(
        project_ids=frozenset({project_a}),
        organization_ids=frozenset({organization_a}),
        team_ids=frozenset({team_a}),
    )
    repository = InMemoryAuthorizationPolicyProfileRepository()
    service = AuthorizationPolicyProfileService(repository, gate)
    owner = OwnerRef(type="user", id="user:admin")
    context_a = _context(
        "user:admin",
        project_id=project_a,
        organization_id=organization_a,
        team_id=team_a,
    )

    visible = asyncio.run(
        service.create(
            owner_ref=owner,
            content=_content("A"),
            context=context_a,
            project_id=project_a,
            organization_id=organization_a,
            team_id=team_a,
        )
    )
    hidden_id = new_id("authorization_policy_profile")
    hidden_definition = type(visible)(
        policy_profile_id=hidden_id,
        owner_ref=owner,
        current_revision=1,
        project_id=project_b,
        organization_id=organization_b,
        team_id=team_b,
    )
    hidden_revision = AuthorizationPolicyProfileRevision(
        policy_profile_id=hidden_id,
        revision=1,
        owner_ref=owner,
        content=_content("B"),
        project_id=project_b,
        organization_id=organization_b,
        team_id=team_b,
        created_at=hidden_definition.created_at,
    )
    repository.create_profile(hidden_definition, hidden_revision)

    listed = asyncio.run(service.list(context_a))
    assert [item.policy_profile_id for item in listed] == [visible.policy_profile_id]

    with pytest.raises(ContractError) as captured:
        asyncio.run(service.get(hidden_id, context_a))
    assert captured.value.code is ErrorCode.FORBIDDEN


def test_profile_grants_cannot_escalate_actor_management_permissions() -> None:
    actor = "user:reader"
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref=actor,
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.READ}),
                resource_types=frozenset({ResourceType.GENERIC}),
            ),
        )
    )
    repository = InMemoryAuthorizationPolicyProfileRepository()
    admin_service = AuthorizationPolicyProfileService(repository, _admin_gate())
    definition = asyncio.run(
        admin_service.create(
            owner_ref=OwnerRef(type="user", id="user:admin"),
            content=AuthorizationPolicyProfileContent(
                name="Administrators",
                allowed_actions=(AuthorizationAction.ADMINISTER,),
                resource_types=(ResourceType.GENERIC,),
                provenance=AuthorizationPolicyProvenance(
                    created_by="user:admin",
                    source="local",
                ),
            ),
            context=_context("user:admin"),
        )
    )
    service = AuthorizationPolicyProfileService(repository, AuthorizationGate(provider))

    with pytest.raises(ContractError) as captured:
        asyncio.run(
            service.assign(
                profile_ref=AuthorizationPolicyProfileRef(definition.policy_profile_id, 1),
                principal_ref=actor,
                actor_types=(ActorType.HUMAN,),
                context=_context(actor),
            )
        )
    assert captured.value.code is ErrorCode.FORBIDDEN
    assert repository.list_assignments() == ()


def test_local_reference_compiler_fails_closed_for_unrepresentable_conditions() -> None:
    revision = AuthorizationPolicyProfileRevision(
        policy_profile_id=new_id("authorization_policy_profile"),
        revision=1,
        owner_ref=OwnerRef(type="user", id="user:admin"),
        content=AuthorizationPolicyProfileContent(
            name="Conditional",
            allowed_actions=(AuthorizationAction.READ,),
            conditions=AuthorizationPolicyConditions(required_security_labels=("trusted",)),
            provenance=AuthorizationPolicyProvenance(created_by="user:admin", source="local"),
        ),
    )

    with pytest.raises(ContractError) as captured:
        compile_local_principal_policy(
            revision,
            principal_ref="agent:one",
            actor_types=(ActorType.AGENT,),
        )
    assert captured.value.code is ErrorCode.UNSUPPORTED_CAPABILITY


def test_canonical_serialization_contains_no_credentials_or_provider_private_policy_objects() -> None:
    revision = AuthorizationPolicyProfileRevision(
        policy_profile_id=new_id("authorization_policy_profile"),
        revision=1,
        owner_ref=OwnerRef(type="user", id="user:admin"),
        content=AuthorizationPolicyProfileContent(
            name="Portable",
            allowed_actions=(AuthorizationAction.READ,),
            scope_constraints=AuthorizationPolicyScopeConstraints(resource_ids=("file:public",)),
            provenance=AuthorizationPolicyProvenance(
                created_by="user:admin",
                source="portable-package",
                source_reference="package:fixture",
                imported=True,
                trusted=False,
            ),
        ),
    )

    serialized = json.dumps(policy_profile_revision_to_json(revision), sort_keys=True)
    lowered = serialized.lower()
    assert "credential" not in lowered
    assert "secret" not in lowered
    assert "provider_policy" not in lowered
    assert "localprincipalpolicy" not in lowered
