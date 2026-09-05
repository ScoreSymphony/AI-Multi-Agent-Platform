from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.portability.executor import ImportExecutor, ImportMutationRegistry
from ai_multi_agent_platform.portability.models import IdPolicy
from ai_multi_agent_platform.portability.planner import (
    ImportPreviewService,
    ImportSecurityFindingKind,
)
from ai_multi_agent_platform.portability.policy_profile_codecs import (
    AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE,
    AuthorizationPolicyProfilePortableCodec,
    AuthorizationPolicyProfilePortableSnapshot,
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
    content = AuthorizationPolicyProfileContent(
        name="Portable developer policy",
        allowed_actions=(AuthorizationAction.READ,),
        approval_required_actions=(AuthorizationAction.MODIFY,),
        resource_types=(ResourceType.GENERIC,),
        scope_constraints=AuthorizationPolicyScopeConstraints(
            project_ids=(project_id,) if project_id is not None else (),
        ),
        provenance=AuthorizationPolicyProvenance(
            created_by="service:source",
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
    current = replace(definition, current_revision=2)
    repository.append_revision(
        current,
        AuthorizationPolicyProfileRevision(
            policy_profile_id=profile_id,
            revision=2,
            owner_ref=owner,
            content=replace(content, name="Portable developer policy v2"),
            project_id=project_id,
            created_at=current.updated_at,
        ),
    )
    return current


def _call_context() -> AuthorizationPolicyProfileCallContext:
    return AuthorizationPolicyProfileCallContext(
        operation=OperationContext(
            correlation_id="issue-310-portability",
            owner_type="service",
            owner_id="portability",
        ),
        actor_ref="service:portability",
    )


def _gate(*actions: AuthorizationAction) -> AuthorizationGate:
    return AuthorizationGate(
        LocalAuthorizationProvider(
            (
                LocalPrincipalPolicy(
                    principal_ref="service:portability",
                    actor_types=frozenset({ActorType.SERVICE}),
                    allowed_actions=frozenset(actions),
                    resource_types=frozenset({ResourceType.GENERIC}),
                ),
            )
        )
    )


def _exists(repository: InMemoryAuthorizationPolicyProfileRepository, resource_id: str) -> bool:
    try:
        repository.get_profile(resource_id)
    except ContractError as exc:
        if exc.code is ErrorCode.NOT_FOUND:
            return False
        raise
    return True


def test_policy_profile_79_roundtrip_imports_dormant_untrusted_history() -> None:
    source = InMemoryAuthorizationPolicyProfileRepository()
    source_definition = _source_profile(source)
    destination = InMemoryAuthorizationPolicyProfileRepository()
    destination_owner = OwnerRef(type="service", id="destination-policy-owner")
    service = AuthorizationPolicyProfileService(destination, _gate(AuthorizationAction.CREATE))

    serializers = ResourceSerializerRegistry()
    register_authorization_policy_profile_portability_codec(serializers)
    exports = ExportSourceRegistry()

    async def load(resource_id: str) -> object:
        return snapshot_authorization_policy_profile(source, resource_id)

    exports.register(AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE, load)
    mutations = ImportMutationRegistry()
    mutations.register(
        AuthorizationPolicyProfileImportMutationHandler(
            service,
            import_context=_call_context(),
            target_owner_ref=destination_owner,
        )
    )
    workflow = PortabilityWorkflowService(
        serializers=serializers,
        export_sources=exports,
        preview_service=ImportPreviewService(
            resource_exists=lambda resource_type, resource_id: (
                resource_type == AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE
                and _exists(destination, resource_id)
            ),
            dependency_available=lambda _requirement: True,
            security_inspector=inspect_authorization_policy_profile_import,
        ),
        executor=ImportExecutor(serializers, mutations),
        platform_version="0.0.1",
    )

    package = asyncio.run(
        workflow.export_package(
            (
                ExportSelection(
                    AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE,
                    source_definition.policy_profile_id,
                ),
            )
        )
    )
    preview = workflow.preview_import(package.package_id)
    assert preview.ready is True
    assert {finding.kind for finding in preview.preview.security_findings} == {
        ImportSecurityFindingKind.PERMISSION_ESCALATION,
        ImportSecurityFindingKind.UNTRUSTED_CONFIGURATION,
    }

    asyncio.run(workflow.execute_import(preview.preview_id))
    imported = destination.get_profile(source_definition.policy_profile_id)
    revisions = destination.list_revisions(source_definition.policy_profile_id)
    assert imported.enabled is False
    assert imported.owner_ref == destination_owner
    assert tuple(item.revision for item in revisions) == (1, 2)
    assert all(item.owner_ref == destination_owner for item in revisions)
    assert all(item.content.provenance.imported for item in revisions)
    assert all(not item.content.provenance.trusted for item in revisions)
    assert destination.list_assignments(policy_profile_id=source_definition.policy_profile_id) == ()


def test_imported_profile_requires_explicit_enable_before_assignment() -> None:
    source = InMemoryAuthorizationPolicyProfileRepository()
    definition = _source_profile(source)
    serializers = ResourceSerializerRegistry()
    register_authorization_policy_profile_portability_codec(serializers)
    portable = serializers.serialize(
        AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE,
        snapshot_authorization_policy_profile(source, definition.policy_profile_id),
    )
    decoded = serializers.deserialize(portable)
    assert isinstance(decoded, AuthorizationPolicyProfilePortableSnapshot)

    destination = InMemoryAuthorizationPolicyProfileRepository()
    service = AuthorizationPolicyProfileService(
        destination,
        _gate(AuthorizationAction.CREATE, AuthorizationAction.ADMINISTER),
    )
    handler = AuthorizationPolicyProfileImportMutationHandler(
        service,
        import_context=_call_context(),
        target_owner_ref=OwnerRef(type="service", id="destination-policy-owner"),
    )
    context = ImportContext()
    asyncio.run(handler.preflight(portable, decoded, context))
    asyncio.run(handler.apply(portable, decoded, context))

    with pytest.raises(ContractError) as captured:
        asyncio.run(
            service.assign(
                profile_ref=destination.get_revision(definition.policy_profile_id, 2).ref,
                principal_ref="service:portability",
                actor_types=(ActorType.SERVICE,),
                context=_call_context(),
            )
        )
    assert captured.value.code is ErrorCode.CONFLICT

    enabled = asyncio.run(service.enable(definition.policy_profile_id, _call_context()))
    assert enabled.enabled is True
    assignment = asyncio.run(
        service.assign(
            profile_ref=destination.get_revision(definition.policy_profile_id, 2).ref,
            principal_ref="service:portability",
            actor_types=(ActorType.SERVICE,),
            context=_call_context(),
        )
    )
    assert assignment.profile_ref.revision == 2


def test_regenerated_profile_id_remaps_typed_scope_references() -> None:
    source = InMemoryAuthorizationPolicyProfileRepository()
    source_project = new_id("project")
    target_project = new_id("project")
    definition = _source_profile(source, project_id=source_project)
    target_profile = new_id("authorization_policy_profile")
    codec = AuthorizationPolicyProfilePortableCodec(id_policy=IdPolicy.REGENERATE)
    registry = ResourceSerializerRegistry()
    registry.register(codec)
    portable = registry.serialize(
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
    assert isinstance(decoded, AuthorizationPolicyProfilePortableSnapshot)
    assert decoded.definition.policy_profile_id == target_profile
    assert decoded.definition.project_id == target_project
    assert decoded.definition.enabled is False
    assert decoded.revisions[-1].content.scope_constraints.project_ids == (target_project,)


def test_policy_profile_preview_blocks_assignment_payloads() -> None:
    source = InMemoryAuthorizationPolicyProfileRepository()
    definition = _source_profile(source)
    registry = ResourceSerializerRegistry()
    register_authorization_policy_profile_portability_codec(registry)
    portable = registry.serialize(
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


def test_policy_profile_import_compensation_removes_only_dormant_unassigned_import() -> None:
    source = InMemoryAuthorizationPolicyProfileRepository()
    definition = _source_profile(source)
    registry = ResourceSerializerRegistry()
    register_authorization_policy_profile_portability_codec(registry)
    portable = registry.serialize(
        AUTHORIZATION_POLICY_PROFILE_RESOURCE_TYPE,
        snapshot_authorization_policy_profile(source, definition.policy_profile_id),
    )
    decoded = registry.deserialize(portable)
    assert isinstance(decoded, AuthorizationPolicyProfilePortableSnapshot)

    destination = InMemoryAuthorizationPolicyProfileRepository()
    service = AuthorizationPolicyProfileService(destination, _gate(AuthorizationAction.CREATE))
    handler = AuthorizationPolicyProfileImportMutationHandler(
        service,
        import_context=_call_context(),
        target_owner_ref=OwnerRef(type="service", id="destination-policy-owner"),
    )
    context = ImportContext()
    token = asyncio.run(handler.apply(portable, decoded, context))
    asyncio.run(handler.rollback(portable, decoded, token, context))

    with pytest.raises(ContractError) as captured:
        destination.get_profile(definition.policy_profile_id)
    assert captured.value.code is ErrorCode.NOT_FOUND
