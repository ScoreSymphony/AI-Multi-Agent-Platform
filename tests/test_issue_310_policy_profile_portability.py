from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from ai_multi_agent_platform.agents.repository import InMemoryAgentRepository
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.control_plane.service import ScopeStore
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.models import ModelRegistry
from ai_multi_agent_platform.portability import (
    AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE,
    AuthorizationPolicyProfilePortableCodec,
    AuthorizationPolicyProfilePortableSnapshot,
    ExportSelection,
    IdPolicy,
    ImportContext,
    ImportSecurityFindingKind,
    PortableResource,
    package_to_dict,
    seal_resource,
    snapshot_authorization_policy_profile,
)
from ai_multi_agent_platform.portability.composition import build_agent_portability_workflow
from ai_multi_agent_platform.portability.policy_profile_codecs import (
    inspect_authorization_policy_profile_import,
)
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
    AuthorizationPolicyScopeConstraints,
    InMemoryAuthorizationPolicyProfileRepository,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)


def _management_gate(actor_ref: str = "user:policy-admin") -> AuthorizationGate:
    return AuthorizationGate(
        LocalAuthorizationProvider(
            (
                LocalPrincipalPolicy(
                    principal_ref=actor_ref,
                    actor_types=frozenset({ActorType.HUMAN}),
                    resource_types=frozenset({ResourceType.GENERIC}),
                    administrator=True,
                ),
            )
        )
    )


def _context(actor_ref: str = "user:policy-admin") -> AuthorizationPolicyProfileCallContext:
    return AuthorizationPolicyProfileCallContext(
        operation=OperationContext(
            correlation_id="issue-310-portability",
            owner_type="user",
            owner_id="policy-admin",
        ),
        actor_ref=actor_ref,
    )


def _source_profile(
    repository: InMemoryAuthorizationPolicyProfileRepository,
    *,
    project_id: str | None = None,
) -> AuthorizationPolicyProfileDefinition:
    profile_id = new_id("authorization_policy_profile")
    owner = OwnerRef(type="user", id="source-owner")
    content = AuthorizationPolicyProfileContent(
        name="Portable operator",
        description="Provider-neutral reusable policy",
        allowed_actions=(AuthorizationAction.READ, AuthorizationAction.EXECUTE),
        approval_required_actions=(AuthorizationAction.MODIFY,),
        resource_types=(ResourceType.FILE, ResourceType.TOOL),
        scope_constraints=AuthorizationPolicyScopeConstraints(
            project_ids=(project_id,) if project_id is not None else (),
        ),
        provenance=AuthorizationPolicyProvenance(
            created_by="user:source-owner",
            source="local",
        ),
    )
    definition = AuthorizationPolicyProfileDefinition(
        policy_profile_id=profile_id,
        owner_ref=owner,
        current_revision=1,
        project_id=project_id,
    )
    repository.create_profile(
        definition,
        AuthorizationPolicyProfileRevision(
            policy_profile_id=profile_id,
            revision=1,
            owner_ref=owner,
            content=content,
            project_id=project_id,
            created_at=definition.created_at,
        ),
    )
    return definition


def _portable_resource(
    codec: AuthorizationPolicyProfilePortableCodec,
    snapshot: AuthorizationPolicyProfilePortableSnapshot,
) -> PortableResource:
    exported = codec.serialize(snapshot)
    return seal_resource(
        PortableResource(
            resource_type=AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE,
            resource_id=exported.resource_id,
            resource_version=exported.resource_version,
            payload=exported.payload,
            id_policy=exported.id_policy,
            dependencies=exported.dependencies,
        )
    )


def _workflow(
    repository: InMemoryAuthorizationPolicyProfileRepository,
    *,
    target_owner: OwnerRef,
    id_policy: IdPolicy = IdPolicy.PRESERVE,
):
    return build_agent_portability_workflow(
        agents=InMemoryAgentRepository(),
        models=ModelRegistry(),
        scopes=ScopeStore(),
        platform_version="0.0.1",
        policy_profiles=repository,
        policy_profile_service=AuthorizationPolicyProfileService(repository, _management_gate()),
        policy_profile_import_context=_context(),
        policy_profile_target_owner=target_owner,
        id_policy=id_policy,
    )


def test_policy_profile_79_roundtrip_imports_dormant_untrusted_configuration_only() -> None:
    source = InMemoryAuthorizationPolicyProfileRepository()
    source_definition = _source_profile(source)
    source_workflow = _workflow(
        source,
        target_owner=OwnerRef(type="user", id="source-owner"),
    )
    exported = asyncio.run(
        source_workflow.export_package(
            (
                ExportSelection(
                    AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE,
                    source_definition.policy_profile_id,
                ),
            )
        )
    )

    destination = InMemoryAuthorizationPolicyProfileRepository()
    target_owner = OwnerRef(type="user", id="destination-owner")
    destination_workflow = _workflow(destination, target_owner=target_owner)
    inspection = destination_workflow.validate_package_document(package_to_dict(exported.package))
    preview = destination_workflow.preview_import(inspection.package_id)

    assert preview.ready is True
    assert {finding.kind for finding in preview.preview.security_findings} == {
        ImportSecurityFindingKind.PERMISSION_ESCALATION,
        ImportSecurityFindingKind.UNTRUSTED_CONFIGURATION,
    }
    assert all(not finding.blocking for finding in preview.preview.security_findings)

    report = asyncio.run(destination_workflow.execute_import(preview.preview_id))
    imported_id = report.result.resources[0].target_id
    imported = destination.get_profile(imported_id)
    imported_revision = destination.get_revision(imported_id, 1)

    assert imported_id == source_definition.policy_profile_id
    assert imported.enabled is False
    assert imported.owner_ref == target_owner
    assert imported_revision.owner_ref == target_owner
    assert imported_revision.content.provenance.imported is True
    assert imported_revision.content.provenance.trusted is False
    assert imported_revision.content.provenance.source == "portable-package"
    assert destination.list_assignments(policy_profile_id=imported_id) == ()


def test_imported_profile_needs_separate_authorized_enable_and_assignment() -> None:
    source = InMemoryAuthorizationPolicyProfileRepository()
    source_definition = _source_profile(source)
    codec = AuthorizationPolicyProfilePortableCodec()
    decoded = codec.deserialize(
        _portable_resource(
            codec,
            snapshot_authorization_policy_profile(source, source_definition.policy_profile_id),
        ),
        ImportContext(),
    )
    assert isinstance(decoded, AuthorizationPolicyProfilePortableSnapshot)

    destination = InMemoryAuthorizationPolicyProfileRepository()
    service = AuthorizationPolicyProfileService(destination, _management_gate())
    target_owner = OwnerRef(type="user", id="destination-owner")
    imported = asyncio.run(
        service.import_profile(
            definition=replace(decoded.definition, owner_ref=target_owner),
            revisions=tuple(
                replace(revision, owner_ref=target_owner) for revision in decoded.revisions
            ),
            context=_context(),
        )
    )

    with pytest.raises(ContractError) as captured:
        asyncio.run(
            service.assign(
                profile_ref=AuthorizationPolicyProfileRef(imported.policy_profile_id, 1),
                principal_ref="agent:consumer",
                actor_types=(ActorType.AGENT,),
                context=_context(),
            )
        )
    assert captured.value.code is ErrorCode.CONFLICT
    assert destination.list_assignments() == ()

    enabled = asyncio.run(service.enable(imported.policy_profile_id, _context()))
    assignment = asyncio.run(
        service.assign(
            profile_ref=AuthorizationPolicyProfileRef(imported.policy_profile_id, 1),
            principal_ref="agent:consumer",
            actor_types=(ActorType.AGENT,),
            context=_context(),
        )
    )
    assert enabled.enabled is True
    assert assignment.profile_ref.revision == 1


def test_regenerated_import_remaps_profile_and_typed_scope_references() -> None:
    source = InMemoryAuthorizationPolicyProfileRepository()
    source_project = new_id("project")
    target_project = new_id("project")
    definition = _source_profile(source, project_id=source_project)
    codec = AuthorizationPolicyProfilePortableCodec(id_policy=IdPolicy.REGENERATE)
    target_profile = new_id("authorization_policy_profile")
    decoded = codec.deserialize(
        _portable_resource(
            codec,
            snapshot_authorization_policy_profile(source, definition.policy_profile_id),
        ),
        ImportContext(
            id_mapping={
                (
                    AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE,
                    definition.policy_profile_id,
                ): target_profile,
                ("project", source_project): target_project,
            }
        ),
    )

    assert isinstance(decoded, AuthorizationPolicyProfilePortableSnapshot)
    assert decoded.definition.policy_profile_id == target_profile
    assert decoded.definition.project_id == target_project
    assert decoded.definition.enabled is False
    assert decoded.revisions[0].project_id == target_project
    assert decoded.revisions[0].content.scope_constraints.project_ids == (target_project,)


def test_preview_blocks_payload_that_attempts_to_transport_assignments() -> None:
    source = InMemoryAuthorizationPolicyProfileRepository()
    definition = _source_profile(source)
    codec = AuthorizationPolicyProfilePortableCodec()
    exported = codec.serialize(
        snapshot_authorization_policy_profile(source, definition.policy_profile_id)
    )
    tampered = PortableResource(
        resource_type=AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE,
        resource_id=exported.resource_id,
        resource_version=exported.resource_version,
        payload={**exported.payload, "assignments": [{"principal_ref": "user:attacker"}]},
        id_policy=exported.id_policy,
        dependencies=exported.dependencies,
    )
    findings = inspect_authorization_policy_profile_import(tampered, definition.policy_profile_id)

    assert len(findings) == 1
    assert findings[0].kind is ImportSecurityFindingKind.INVALID_SECURITY_PAYLOAD
    assert findings[0].blocking is True


def test_import_compensation_removes_only_dormant_unassigned_untrusted_profile() -> None:
    repository = InMemoryAuthorizationPolicyProfileRepository()
    source = InMemoryAuthorizationPolicyProfileRepository()
    source_definition = _source_profile(source)
    codec = AuthorizationPolicyProfilePortableCodec()
    decoded = codec.deserialize(
        _portable_resource(
            codec,
            snapshot_authorization_policy_profile(source, source_definition.policy_profile_id),
        ),
        ImportContext(),
    )
    assert isinstance(decoded, AuthorizationPolicyProfilePortableSnapshot)

    owner = OwnerRef(type="user", id="destination-owner")
    service = AuthorizationPolicyProfileService(repository, _management_gate())
    imported = asyncio.run(
        service.import_profile(
            definition=replace(decoded.definition, owner_ref=owner),
            revisions=tuple(replace(item, owner_ref=owner) for item in decoded.revisions),
            context=_context(),
        )
    )
    service.compensate_import(imported.policy_profile_id)

    with pytest.raises(ContractError) as captured:
        repository.get_profile(imported.policy_profile_id)
    assert captured.value.code is ErrorCode.NOT_FOUND


def test_import_compensation_cannot_remove_enabled_profile() -> None:
    repository = InMemoryAuthorizationPolicyProfileRepository()
    source = InMemoryAuthorizationPolicyProfileRepository()
    source_definition = _source_profile(source)
    codec = AuthorizationPolicyProfilePortableCodec()
    decoded = codec.deserialize(
        _portable_resource(
            codec,
            snapshot_authorization_policy_profile(source, source_definition.policy_profile_id),
        ),
        ImportContext(),
    )
    assert isinstance(decoded, AuthorizationPolicyProfilePortableSnapshot)

    owner = OwnerRef(type="user", id="destination-owner")
    service = AuthorizationPolicyProfileService(repository, _management_gate())
    imported = asyncio.run(
        service.import_profile(
            definition=replace(decoded.definition, owner_ref=owner),
            revisions=tuple(replace(item, owner_ref=owner) for item in decoded.revisions),
            context=_context(),
        )
    )
    asyncio.run(service.enable(imported.policy_profile_id, _context()))

    with pytest.raises(ContractError) as captured:
        service.compensate_import(imported.policy_profile_id)
    assert captured.value.code is ErrorCode.CONFLICT
