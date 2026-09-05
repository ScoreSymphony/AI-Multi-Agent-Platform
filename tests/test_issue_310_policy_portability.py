from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.portability.executor import ImportExecutor, ImportMutationRegistry
from ai_multi_agent_platform.portability.models import IdPolicy, PackageProvenance
from ai_multi_agent_platform.portability.package import build_package
from ai_multi_agent_platform.portability.planner import (
    ImportPreviewService,
    ImportSecurityFindingKind,
)
from ai_multi_agent_platform.portability.policy_profile_codecs import (
    AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE,
    AuthorizationPolicyProfilePortableCodec,
    inspect_authorization_policy_profile_import,
    register_authorization_policy_profile_portability_codec,
    snapshot_authorization_policy_profile,
)
from ai_multi_agent_platform.portability.policy_profile_import import (
    AuthorizationPolicyProfileImportMutationHandler,
)
from ai_multi_agent_platform.portability.registry import ImportContext, ResourceSerializerRegistry
from ai_multi_agent_platform.portability.workflow import (
    ExportSelection,
    ExportSourceRegistry,
    PortabilityWorkflowService,
)
from ai_multi_agent_platform.security.authorization import (
    ActorType,
    AuthorizationAction,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.security.enforcement import AuthorizationGate
from ai_multi_agent_platform.security.policy_profile_import_service import (
    AuthorizationPolicyProfileImportService,
    PortableInMemoryAuthorizationPolicyProfileRepository,
)
from ai_multi_agent_platform.security.policy_profiles import (
    AuthorizationPolicyProfileCallContext,
    AuthorizationPolicyProfileContent,
    AuthorizationPolicyProfileDefinition,
    AuthorizationPolicyProfileRevision,
    AuthorizationPolicyProfileService,
    AuthorizationPolicyProvenance,
    AuthorizationPolicyScopeConstraints,
    InMemoryAuthorizationPolicyProfileRepository,
)


def _source_profile(
    repository: InMemoryAuthorizationPolicyProfileRepository,
    *,
    project_id: str | None = None,
) -> AuthorizationPolicyProfileDefinition:
    profile_id = new_id("authorization_policy_profile")
    owner = OwnerRef(type="service", id="portable-source")
    scope = AuthorizationPolicyScopeConstraints(
        project_ids=(project_id,) if project_id is not None else (),
    )
    content = AuthorizationPolicyProfileContent(
        name="Portable developer policy",
        description="Canonical fixture for issue #310",
        allowed_actions=(AuthorizationAction.READ,),
        approval_required_actions=(AuthorizationAction.MODIFY,),
        resource_types=(ResourceType.GENERIC,),
        scope_constraints=scope,
        provenance=AuthorizationPolicyProvenance(
            created_by="service:source",
            source="local",
            trusted=True,
        ),
    )
    definition = AuthorizationPolicyProfileDefinition(
        policy_profile_id=profile_id,
        owner_ref=owner,
        current_revision=1,
        project_id=project_id,
    )
    revision = AuthorizationPolicyProfileRevision(
        policy_profile_id=profile_id,
        revision=1,
        owner_ref=owner,
        content=content,
        project_id=project_id,
        created_at=definition.created_at,
    )
    repository.create_profile(definition, revision)
    return definition


def _import_context() -> AuthorizationPolicyProfileCallContext:
    return AuthorizationPolicyProfileCallContext(
        operation=OperationContext(
            correlation_id="issue-310-portability",
            owner_type="service",
            owner_id="portability",
        ),
        actor_ref="service:portability",
    )


def _import_gate() -> AuthorizationGate:
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref="service:portability",
                actor_types=frozenset({ActorType.SERVICE}),
                allowed_actions=frozenset({AuthorizationAction.CREATE}),
                resource_types=frozenset({ResourceType.GENERIC}),
            ),
        )
    )
    return AuthorizationGate(provider)


def _exists(repository: PortableInMemoryAuthorizationPolicyProfileRepository, resource_id: str) -> bool:
    try:
        repository.get_profile(resource_id)
    except ContractError as exc:
        if exc.code is ErrorCode.NOT_FOUND:
            return False
        raise
    return True


def test_policy_profile_full_79_roundtrip_imports_disabled_untrusted_configuration() -> None:
    source = InMemoryAuthorizationPolicyProfileRepository()
    source_definition = _source_profile(source)
    destination = PortableInMemoryAuthorizationPolicyProfileRepository()

    serializers = ResourceSerializerRegistry()
    register_authorization_policy_profile_portability_codec(serializers)
    export_sources = ExportSourceRegistry()

    async def load(resource_id: str) -> object:
        return snapshot_authorization_policy_profile(source, resource_id)

    export_sources.register(AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE, load)

    import_service = AuthorizationPolicyProfileImportService(destination, _import_gate())
    mutations = ImportMutationRegistry()
    mutations.register(
        AuthorizationPolicyProfileImportMutationHandler(
            import_service,
            destination,
            import_context=_import_context(),
        )
    )
    preview_service = ImportPreviewService(
        resource_exists=lambda resource_type, resource_id: (
            resource_type == AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE
            and _exists(destination, resource_id)
        ),
        dependency_available=lambda requirement: True,
        security_inspector=inspect_authorization_policy_profile_import,
    )
    workflow = PortabilityWorkflowService(
        serializers=serializers,
        export_sources=export_sources,
        preview_service=preview_service,
        executor=ImportExecutor(serializers, mutations),
        platform_version="0.0.1",
    )

    inspection = asyncio.run(
        workflow.export_package(
            (
                ExportSelection(
                    AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE,
                    source_definition.policy_profile_id,
                ),
            )
        )
    )
    preview = workflow.preview_import(inspection.package_id)

    assert preview.ready is True
    assert {item.kind for item in preview.preview.security_findings} == {
        ImportSecurityFindingKind.PERMISSION_ESCALATION,
        ImportSecurityFindingKind.UNTRUSTED_CONFIGURATION,
    }
    assert all(not item.blocking for item in preview.preview.security_findings)

    report = asyncio.run(workflow.execute_import(preview.preview_id))
    assert report.result.resources[0].target_id == source_definition.policy_profile_id

    imported = destination.get_profile(source_definition.policy_profile_id)
    imported_revision = destination.get_revision(source_definition.policy_profile_id, 1)
    assert imported.enabled is False
    assert imported_revision.content.provenance.imported is True
    assert imported_revision.content.provenance.trusted is False
    assert imported_revision.content.provenance.source.startswith("portable-import:")
    assert destination.list_assignments(policy_profile_id=source_definition.policy_profile_id) == ()


def test_imported_profile_cannot_self_grant_or_be_assigned_while_disabled() -> None:
    source = InMemoryAuthorizationPolicyProfileRepository()
    source_definition = _source_profile(source)
    destination = PortableInMemoryAuthorizationPolicyProfileRepository()
    import_service = AuthorizationPolicyProfileImportService(destination, _import_gate())
    snapshot = snapshot_authorization_policy_profile(source, source_definition.policy_profile_id)

    imported = asyncio.run(
        import_service.import_history(
            snapshot.definition,
            snapshot.revisions,
            context=_import_context(),
            source_reference="portable-resource:test",
        )
    )
    assert imported.enabled is False

    management = AuthorizationPolicyProfileService(destination, _import_gate())
    with pytest.raises(ContractError) as captured:
        asyncio.run(
            management.assign(
                profile_ref=destination.get_revision(imported.policy_profile_id, 1).ref,
                principal_ref="service:portability",
                actor_types=(ActorType.SERVICE,),
                context=_import_context(),
            )
        )
    assert captured.value.code is ErrorCode.CONFLICT
    assert destination.list_assignments(policy_profile_id=imported.policy_profile_id) == ()


def test_regenerate_id_remaps_profile_and_canonical_scope_references() -> None:
    source = InMemoryAuthorizationPolicyProfileRepository()
    source_project = new_id("project")
    target_project = new_id("project")
    definition = _source_profile(source, project_id=source_project)
    target_profile = new_id("authorization_policy_profile")

    codec = AuthorizationPolicyProfilePortableCodec(id_policy=IdPolicy.REGENERATE)
    exported = codec.serialize(snapshot_authorization_policy_profile(source, definition.policy_profile_id))
    resource = build_package(
        source_platform_version="0.0.1",
        resources=(
            ResourceSerializerRegistry(),
        ),
        provenance=PackageProvenance(source="test"),
    )
    del resource

    serializers = ResourceSerializerRegistry()
    register_authorization_policy_profile_portability_codec(
        serializers,
        id_policy=IdPolicy.REGENERATE,
    )
    portable = serializers.serialize(
        AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE,
        snapshot_authorization_policy_profile(source, definition.policy_profile_id),
    )
    decoded = codec.deserialize(
        portable,
        ImportContext(
            id_mapping={
                (AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE, definition.policy_profile_id): target_profile,
                ("project", source_project): target_project,
            }
        ),
    )
    assert isinstance(decoded, type(snapshot_authorization_policy_profile(source, definition.policy_profile_id)))
    assert decoded.definition.policy_profile_id == target_profile
    assert decoded.definition.project_id == target_project
    assert decoded.definition.enabled is False
    assert decoded.revisions[0].project_id == target_project
    assert decoded.revisions[0].content.scope_constraints.project_ids == (target_project,)
    assert exported.resource_id == definition.policy_profile_id


def test_policy_profile_security_inspection_blocks_assignment_payloads() -> None:
    source = InMemoryAuthorizationPolicyProfileRepository()
    definition = _source_profile(source)
    serializers = ResourceSerializerRegistry()
    register_authorization_policy_profile_portability_codec(serializers)
    portable = serializers.serialize(
        AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE,
        snapshot_authorization_policy_profile(source, definition.policy_profile_id),
    )
    tampered = replace(
        portable,
        payload={**portable.payload, "assignments": [{"principal_ref": "service:any"}]},
    )

    findings = inspect_authorization_policy_profile_import(tampered, definition.policy_profile_id)
    assert len(findings) == 1
    assert findings[0].kind is ImportSecurityFindingKind.INVALID_SECURITY_PAYLOAD
    assert findings[0].blocking is True


def test_import_compensation_only_removes_disabled_untrusted_unassigned_profiles() -> None:
    source = InMemoryAuthorizationPolicyProfileRepository()
    definition = _source_profile(source)
    destination = PortableInMemoryAuthorizationPolicyProfileRepository()
    service = AuthorizationPolicyProfileImportService(destination, _import_gate())
    snapshot = snapshot_authorization_policy_profile(source, definition.policy_profile_id)

    asyncio.run(
        service.import_history(
            snapshot.definition,
            snapshot.revisions,
            context=_import_context(),
            source_reference="portable-resource:test",
        )
    )
    service.compensate_import(definition.policy_profile_id)

    with pytest.raises(ContractError) as captured:
        destination.get_profile(definition.policy_profile_id)
    assert captured.value.code is ErrorCode.NOT_FOUND
