from __future__ import annotations

from dataclasses import replace

from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.portability import (
    AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE,
    AuthorizationPolicyProfilePortableCodec,
    IdPolicy,
    ImportContext,
    ImportPreviewService,
    ImportSecurityFindingKind,
    PackageProvenance,
    ResourceSerializerRegistry,
    build_package,
    inspect_authorization_policy_profile_import,
    snapshot_authorization_policy_profile,
)
from ai_multi_agent_platform.security import (
    AuthorizationAction,
    AuthorizationPolicyConditions,
    AuthorizationPolicyProfileContent,
    AuthorizationPolicyProfileDefinition,
    AuthorizationPolicyProfileRevision,
    AuthorizationPolicyProvenance,
    AuthorizationPolicyScopeConstraints,
    InMemoryAuthorizationPolicyProfileRepository,
    ResourceType,
)


def _snapshot() -> tuple[
    InMemoryAuthorizationPolicyProfileRepository,
    AuthorizationPolicyProfileDefinition,
]:
    repository = InMemoryAuthorizationPolicyProfileRepository()
    profile_id = new_id("authorization_policy_profile")
    project_id = new_id("project")
    organization_id = new_id("organization")
    team_id = new_id("team")
    workspace_id = new_id("workspace")
    node_id = new_id("node")
    owner = OwnerRef(type="user", id="portable-owner")
    definition = AuthorizationPolicyProfileDefinition(
        policy_profile_id=profile_id,
        owner_ref=owner,
        current_revision=1,
        project_id=project_id,
        organization_id=organization_id,
        team_id=team_id,
    )
    content = AuthorizationPolicyProfileContent(
        name="Portable operator",
        allowed_actions=(AuthorizationAction.READ, AuthorizationAction.EXECUTE),
        approval_required_actions=(AuthorizationAction.ADMINISTER,),
        resource_types=(ResourceType.FILE, ResourceType.TOOL),
        scope_constraints=AuthorizationPolicyScopeConstraints(
            project_ids=(project_id,),
            organization_ids=(organization_id,),
            team_ids=(team_id,),
            workspace_ids=(workspace_id,),
            resource_ids=("file:opaque-stable-reference",),
        ),
        conditions=AuthorizationPolicyConditions(allowed_node_ids=(node_id,)),
        provenance=AuthorizationPolicyProvenance(
            created_by="user:portable-owner",
            source="local",
        ),
    )
    revision = AuthorizationPolicyProfileRevision(
        policy_profile_id=profile_id,
        revision=1,
        owner_ref=owner,
        content=content,
        project_id=project_id,
        organization_id=organization_id,
        team_id=team_id,
        created_at=definition.created_at,
    )
    repository.create_profile(definition, revision)

    second = replace(definition, current_revision=2)
    second_revision = replace(
        revision,
        revision=2,
        content=replace(content, name="Portable operator v2"),
        created_at=second.updated_at,
    )
    repository.append_revision(second, second_revision)
    return repository, second


def test_policy_profile_codec_exports_complete_history_without_assignments() -> None:
    repository, definition = _snapshot()
    registry = ResourceSerializerRegistry()
    registry.register(AuthorizationPolicyProfilePortableCodec())

    resource = registry.serialize(
        AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE,
        snapshot_authorization_policy_profile(repository, definition.policy_profile_id),
    )

    assert resource.resource_id == definition.policy_profile_id
    assert resource.resource_version == "2"
    assert resource.payload["schema_version"] == "1"
    assert len(resource.payload["revisions"]) == 2
    assert "assignments" not in resource.payload
    encoded = str(resource.payload).lower()
    assert "localprincipalpolicy" not in encoded
    assert "provider_policy" not in encoded
    assert "credential" not in encoded
    assert "secret" not in encoded


def test_policy_profile_codec_remaps_typed_scope_and_marks_import_untrusted() -> None:
    repository, definition = _snapshot()
    snapshot = snapshot_authorization_policy_profile(repository, definition.policy_profile_id)
    registry = ResourceSerializerRegistry()
    registry.register(AuthorizationPolicyProfilePortableCodec(id_policy=IdPolicy.REGENERATE))
    resource = registry.serialize(AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE, snapshot)

    source = snapshot.revisions[-1]
    source_scope = source.content.scope_constraints
    source_node = source.content.conditions.allowed_node_ids[0]
    target_profile = new_id("authorization_policy_profile")
    target_project = new_id("project")
    target_organization = new_id("organization")
    target_team = new_id("team")
    target_workspace = new_id("workspace")
    target_node = new_id("node")
    context = ImportContext(
        id_mapping={
            (AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE, definition.policy_profile_id): target_profile,
            ("project", definition.project_id): target_project,
            ("organization", definition.organization_id): target_organization,
            ("team", definition.team_id): target_team,
            ("project", source_scope.project_ids[0]): target_project,
            ("organization", source_scope.organization_ids[0]): target_organization,
            ("team", source_scope.team_ids[0]): target_team,
            ("workspace", source_scope.workspace_ids[0]): target_workspace,
            ("node", source_node): target_node,
        }
    )

    decoded = registry.deserialize(resource, context)
    assert decoded.definition.policy_profile_id == target_profile
    assert decoded.definition.enabled is False
    assert decoded.definition.project_id == target_project
    assert decoded.definition.organization_id == target_organization
    assert decoded.definition.team_id == target_team
    assert decoded.definition.owner_ref == definition.owner_ref

    imported = decoded.revisions[-1]
    assert imported.content.scope_constraints.project_ids == (target_project,)
    assert imported.content.scope_constraints.organization_ids == (target_organization,)
    assert imported.content.scope_constraints.team_ids == (target_team,)
    assert imported.content.scope_constraints.workspace_ids == (target_workspace,)
    assert imported.content.conditions.allowed_node_ids == (target_node,)
    assert imported.content.scope_constraints.resource_ids == source_scope.resource_ids
    assert imported.content.provenance.imported is True
    assert imported.content.provenance.trusted is False
    assert imported.content.provenance.source == "portable-package"


def test_policy_profile_import_preview_surfaces_permission_escalation_without_grant() -> None:
    repository, definition = _snapshot()
    registry = ResourceSerializerRegistry()
    registry.register(AuthorizationPolicyProfilePortableCodec())
    resource = registry.serialize(
        AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE,
        snapshot_authorization_policy_profile(repository, definition.policy_profile_id),
    )
    package = build_package(
        source_platform_version="1.0",
        resources=(resource,),
        provenance=PackageProvenance(source="tests"),
    )
    preview = ImportPreviewService(
        resource_exists=lambda _kind, _resource_id: False,
        dependency_available=lambda _requirement: True,
        security_inspector=inspect_authorization_policy_profile_import,
    ).preview(package)

    assert preview.ready is True
    kinds = {item.kind for item in preview.security_findings}
    assert ImportSecurityFindingKind.UNTRUSTED_CONFIGURATION in kinds
    assert ImportSecurityFindingKind.PERMISSION_ESCALATION in kinds
    escalation = next(
        item
        for item in preview.security_findings
        if item.kind is ImportSecurityFindingKind.PERMISSION_ESCALATION
    )
    assert escalation.blocking is False
    assert "administer" in escalation.detail
    assert "file" in escalation.detail


def test_policy_profile_preview_blocks_payload_that_attempts_to_transport_assignments() -> None:
    repository, definition = _snapshot()
    registry = ResourceSerializerRegistry()
    registry.register(AuthorizationPolicyProfilePortableCodec())
    resource = registry.serialize(
        AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE,
        snapshot_authorization_policy_profile(repository, definition.policy_profile_id),
    )
    tampered_payload = dict(resource.payload)
    tampered_payload["assignments"] = []
    tampered = replace(resource, payload=tampered_payload, checksum="")

    findings = inspect_authorization_policy_profile_import(tampered, resource.resource_id)
    assert len(findings) == 1
    assert findings[0].kind is ImportSecurityFindingKind.INVALID_SECURITY_PAYLOAD
    assert findings[0].blocking is True
