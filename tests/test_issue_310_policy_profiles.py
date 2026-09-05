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


def test_create_and_version_preserves_exact_historical_revision() -> None:
    project_id = new_id("project")
    repository = InMemoryAuthorizationPolicyProfileRepository()
    service = AuthorizationPolicyProfileService(repository, _admin_gate())
    context = _call_context("user:admin", project_id=project_id)

    created = asyncio.run(
        service.create(
            owner_ref=OwnerRef(type="user", id="admin"),
            content=_content("v1", project_ids=(project_id,)),
            context=context,
            project_id=project_id,
        )
    )
    first_ref = AuthorizationPolicyProfileRef(created.policy_profile_id, 1)

    updated = asyncio.run(
        service.revise(
            created.policy_profile_id,
            _content("v2", project_ids=(project_id,)),
            context,
            expected_revision=1,
        )
    )

    assert updated.current_revision == 2
    assert (
        repository.get_revision(first_ref.policy_profile_id, first_ref.revision).content.name
        == "v1"
    )
    assert repository.get_revision(created.policy_profile_id, 2).content.name == "v2"


def test_json_repository_survives_restart_with_history_assignment_and_disable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "authorization-policy-profiles.json"
    project_id = new_id("project")
    repository = JsonAuthorizationPolicyProfileRepository(path)
    service = AuthorizationPolicyProfileService(repository, _admin_gate())
    context = _call_context("user:admin", project_id=project_id)

    created = asyncio.run(
        service.create(
            owner_ref=OwnerRef(type="user", id="admin"),
            content=_content("durable-v1", project_ids=(project_id,)),
            context=context,
            project_id=project_id,
        )
    )
    asyncio.run(
        service.revise(
            created.policy_profile_id,
            _content("durable-v2", project_ids=(project_id,)),
            context,
            expected_revision=1,
        )
    )
    assignment = asyncio.run(
        service.assign(
            profile_ref=AuthorizationPolicyProfileRef(created.policy_profile_id, 1),
            principal_ref="agent:durable",
            actor_types=(ActorType.AGENT,),
            context=context,
        )
    )
    asyncio.run(service.disable(created.policy_profile_id, context))

    restored = JsonAuthorizationPolicyProfileRepository(path)
    definition = restored.get_profile(created.policy_profile_id)
    assert definition.current_revision == 2
    assert definition.enabled is False
    assert restored.get_revision(created.policy_profile_id, 1).content.name == "durable-v1"
    assert restored.get_revision(created.policy_profile_id, 2).content.name == "durable-v2"
    assert restored.get_assignment(assignment.assignment_id).profile_ref.revision == 1


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


def test_untrusted_imported_profile_cannot_self_grant_assignment_authority() -> None:
    repository = InMemoryAuthorizationPolicyProfileRepository()
    service = AuthorizationPolicyProfileService(repository, _admin_gate())
    imported = AuthorizationPolicyProvenance(
        created_by="import:package",
        source="portable-package",
        source_reference="pkg:test",
        imported=True,
        trusted=False,
    )
    created = asyncio.run(
        service.create(
            owner_ref=OwnerRef(type="user", id="admin"),
            content=AuthorizationPolicyProfileContent(
                name="Imported administrator-like profile",
                allowed_actions=(AuthorizationAction.ADMINISTER,),
                resource_types=(ResourceType.GENERIC,),
                provenance=imported,
            ),
            context=_call_context("user:admin"),
        )
    )

    attacker_service = AuthorizationPolicyProfileService(
        repository,
        AuthorizationGate(LocalAuthorizationProvider()),
    )
    with pytest.raises(ContractError) as captured:
        asyncio.run(
            attacker_service.assign(
                profile_ref=AuthorizationPolicyProfileRef(created.policy_profile_id, 1),
                principal_ref="user:attacker",
                actor_types=(ActorType.HUMAN,),
                context=_call_context("user:attacker"),
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
    repository = InMemoryAuthorizationPolicyProfileRepository()
    visible = _direct_profile(
        repository,
        content=_content(project_ids=(project_a,)),
        project_id=project_a,
        organization_id=organization_a,
        team_id=team_a,
    )
    hidden = _direct_profile(
        repository,
        content=_content(project_ids=(project_b,)),
        project_id=project_b,
        organization_id=organization_b,
        team_id=team_b,
    )
    service = AuthorizationPolicyProfileService(
        repository,
        _admin_gate(
            project_ids=frozenset({project_a}),
            organization_ids=frozenset({organization_a}),
            team_ids=frozenset({team_a}),
        ),
    )
    context = _call_context(
        "user:admin",
        project_id=project_a,
        organization_id=organization_a,
        team_id=team_a,
    )

    assert asyncio.run(service.get(visible.policy_profile_id, context)) == visible
    with pytest.raises(ContractError) as captured:
        asyncio.run(service.get(hidden.policy_profile_id, context))
    assert captured.value.code is ErrorCode.FORBIDDEN
    assert asyncio.run(service.list(context)) == (visible,)


def test_assignment_requires_preexisting_authority_not_profile_contents() -> None:
    repository = InMemoryAuthorizationPolicyProfileRepository()
    definition = _direct_profile(
        repository,
        content=AuthorizationPolicyProfileContent(
            name="Escalation candidate",
            allowed_actions=(AuthorizationAction.ADMINISTER,),
            resource_types=(ResourceType.GENERIC,),
            provenance=AuthorizationPolicyProvenance(created_by="user:owner", source="local"),
        ),
    )
    service = AuthorizationPolicyProfileService(
        repository,
        AuthorizationGate(
            LocalAuthorizationProvider(
                (
                    LocalPrincipalPolicy(
                        principal_ref="user:member",
                        actor_types=frozenset({ActorType.HUMAN}),
                        allowed_actions=frozenset({AuthorizationAction.READ}),
                        resource_types=frozenset({ResourceType.GENERIC}),
                    ),
                )
            )
        ),
    )

    with pytest.raises(ContractError) as captured:
        asyncio.run(
            service.assign(
                profile_ref=AuthorizationPolicyProfileRef(definition.policy_profile_id, 1),
                principal_ref="user:member",
                actor_types=(ActorType.HUMAN,),
                context=_call_context("user:member"),
            )
        )
    assert captured.value.code is ErrorCode.FORBIDDEN
    assert repository.list_assignments() == ()


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


def test_canonical_serialization_contains_no_credentials_or_provider_private_policy_objects() -> (
    None
):
    revision = AuthorizationPolicyProfileRevision(
        policy_profile_id=new_id("authorization_policy_profile"),
        revision=1,
        owner_ref=OwnerRef(type="service", id="tests"),
        content=_content(),
    )
    payload = policy_profile_revision_to_json(revision)
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["schema_version"] == "1"
    assert "credential" not in encoded.lower()
    assert "secret" not in encoded.lower()
    assert "provider_policy" not in encoded.lower()
    assert "localprincipalpolicy" not in encoded.lower()
