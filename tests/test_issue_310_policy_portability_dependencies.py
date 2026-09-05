from __future__ import annotations

import asyncio

from ai_multi_agent_platform.agents.repository import InMemoryAgentRepository
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.control_plane.service import ScopeStore
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.models import ModelRegistry
from ai_multi_agent_platform.portability import (
    AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE,
    ExportSelection,
    package_to_dict,
)
from ai_multi_agent_platform.portability.composition import build_agent_portability_workflow
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    AuthorizationPolicyConditions,
    AuthorizationPolicyProfileCallContext,
    AuthorizationPolicyProfileContent,
    AuthorizationPolicyProfileDefinition,
    AuthorizationPolicyProfileRevision,
    AuthorizationPolicyProfileService,
    AuthorizationPolicyProvenance,
    InMemoryAuthorizationPolicyProfileRepository,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)


def _gate() -> AuthorizationGate:
    return AuthorizationGate(
        LocalAuthorizationProvider(
            (
                LocalPrincipalPolicy(
                    principal_ref="user:policy-admin",
                    actor_types=frozenset({ActorType.HUMAN}),
                    resource_types=frozenset({ResourceType.GENERIC}),
                    administrator=True,
                ),
            )
        )
    )


def _context() -> AuthorizationPolicyProfileCallContext:
    return AuthorizationPolicyProfileCallContext(
        operation=OperationContext(
            correlation_id="issue-310-portability-dependencies",
            owner_type="user",
            owner_id="policy-admin",
        ),
        actor_ref="user:policy-admin",
    )


def _workflow(
    repository: InMemoryAuthorizationPolicyProfileRepository,
    *,
    target_owner: OwnerRef,
    additional_resource_exists=None,
):
    return build_agent_portability_workflow(
        agents=InMemoryAgentRepository(),
        models=ModelRegistry(),
        scopes=ScopeStore(),
        platform_version="0.0.1",
        policy_profiles=repository,
        policy_profile_service=AuthorizationPolicyProfileService(repository, _gate()),
        policy_profile_import_context=_context(),
        policy_profile_target_owner=target_owner,
        additional_resource_exists=additional_resource_exists,
    )


def test_external_policy_scope_dependencies_fail_closed_then_resolve_canonically() -> None:
    source = InMemoryAuthorizationPolicyProfileRepository()
    organization_id = new_id("organization")
    team_id = new_id("team")
    node_id = new_id("node")
    profile_id = new_id("authorization_policy_profile")
    source_owner = OwnerRef(type="user", id="source-owner")
    definition = AuthorizationPolicyProfileDefinition(
        policy_profile_id=profile_id,
        owner_ref=source_owner,
        current_revision=1,
        organization_id=organization_id,
        team_id=team_id,
    )
    source.create_profile(
        definition,
        AuthorizationPolicyProfileRevision(
            policy_profile_id=profile_id,
            revision=1,
            owner_ref=source_owner,
            organization_id=organization_id,
            team_id=team_id,
            content=AuthorizationPolicyProfileContent(
                name="External dependency policy",
                allowed_actions=(AuthorizationAction.READ,),
                resource_types=(ResourceType.FILE,),
                conditions=AuthorizationPolicyConditions(allowed_node_ids=(node_id,)),
                provenance=AuthorizationPolicyProvenance(
                    created_by="user:source-owner",
                    source="local",
                ),
            ),
            created_at=definition.created_at,
        ),
    )
    source_workflow = _workflow(source, target_owner=source_owner)
    exported = asyncio.run(
        source_workflow.export_package(
            (ExportSelection(AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE, profile_id),)
        )
    )

    destination = InMemoryAuthorizationPolicyProfileRepository()
    destination_owner = OwnerRef(type="user", id="destination-owner")
    without_external_view = _workflow(destination, target_owner=destination_owner)
    inspection = without_external_view.validate_package_document(
        package_to_dict(exported.package)
    )
    blocked = without_external_view.preview_import(inspection.package_id)
    assert blocked.ready is False
    missing_types = {
        item.requirement.identifier.split(":", 1)[0]
        for item in blocked.preview.missing_dependencies
    }
    assert missing_types == {"organization", "team", "node"}

    known = {
        ("organization", organization_id),
        ("team", team_id),
        ("node", node_id),
    }
    with_external_view = _workflow(
        destination,
        target_owner=destination_owner,
        additional_resource_exists=lambda resource_type, resource_id: (
            resource_type,
            resource_id,
        )
        in known,
    )
    inspection = with_external_view.validate_package_document(package_to_dict(exported.package))
    preview = with_external_view.preview_import(inspection.package_id)
    assert preview.ready is True
    assert preview.preview.missing_dependencies == ()

    report = asyncio.run(with_external_view.execute_import(preview.preview_id))
    imported_id = report.result.resources[0].target_id
    imported = destination.get_profile(imported_id)
    revision = destination.get_revision(imported_id, 1)
    assert imported.enabled is False
    assert imported.organization_id == organization_id
    assert imported.team_id == team_id
    assert revision.content.conditions.allowed_node_ids == (node_id,)
    assert destination.list_assignments(policy_profile_id=imported_id) == ()
