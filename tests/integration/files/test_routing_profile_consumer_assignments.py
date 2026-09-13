from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentModelPolicy,
    AgentProfile,
    AgentService,
    InMemoryAgentRepository,
    InstructionSource,
    agent_profile_from_json,
)
from ai_multi_agent_platform.agents.routing_profile_control_plane import (
    _install_nested_assignment_context,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.control_plane import ActorContext, RequestContext
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.models import (
    JsonModelRoutingProfileRepository,
    ModelRoutingProfileAssignmentGate,
    ModelRoutingProfilePolicy,
    ModelRoutingProfileRef,
    ModelRoutingProfileService,
    new_model_routing_profile_id,
)
from ai_multi_agent_platform.models.routing_profile_assignment_context import (
    RoutingProfileAssignmentAccess,
    activate_routing_profile_assignment_access,
    current_routing_profile_assignment_access,
)
from ai_multi_agent_platform.portability.agent_codecs import snapshot_agent
from ai_multi_agent_platform.portability.agent_import import AgentImportMutationHandler
from ai_multi_agent_platform.portability.registry import ImportContext, ResourceSerializerRegistry
from ai_multi_agent_platform.portability.routing_profile_reference_codecs import (
    RoutingProfileAwareAgentPortableCodec,
    RoutingProfileAwareTemplatePortableCodec,
)
from ai_multi_agent_platform.portability.template_codecs import snapshot_template
from ai_multi_agent_platform.templates import (
    AgentTemplateHandler,
    InMemoryTemplateRepository,
    TemplateConfiguration,
    TemplateContent,
    TemplateEnvironment,
    TemplateInstantiationContext,
    TemplateInstantiationProvenance,
    TemplateProvenance,
    TemplateRequirements,
    TemplateRevision,
    TemplateRevisionState,
    TemplateService,
    TemplateTrust,
    TemplateType,
)
from ai_multi_agent_platform.templates.agent_handlers import portable_agent_profile_payload
from ai_multi_agent_platform.testing import FakeAuthorizationProvider

OWNER = OwnerRef(type="user", id="user-issue-445")


def _operation(project_id: str | None = None) -> OperationContext:
    return OperationContext(
        correlation_id="corr-issue-445",
        owner_type=OWNER.type,
        owner_id=OWNER.id,
        project_id=project_id,
    )


def _agent_profile(routing_profile_ref: str) -> AgentProfile:
    return AgentProfile(
        name="Issue 445 routed Agent",
        role="researcher",
        instructions=AgentInstructions(
            role=InstructionSource(content="Use the exact durable routing profile.")
        ),
        model=AgentModelPolicy(routing_profile_ref=routing_profile_ref),
    )


def _template_revision(
    routing_profile_ref: str,
    *,
    declare: bool = True,
    project_id: str | None = None,
) -> TemplateRevision:
    template_id = new_id("template")
    profile = _agent_profile(routing_profile_ref)
    content = TemplateContent(
        name="Issue 445 Agent template",
        description="Exercises routing-profile assignment boundaries.",
        template_type=TemplateType.AGENT,
        configuration=TemplateConfiguration(
            payload={
                "profile": portable_agent_profile_payload(profile),
                "project_id": project_id,
                "workspace_id": None,
            }
        ),
        requirements=TemplateRequirements(
            model_policy_refs=(routing_profile_ref,) if declare else (),
        ),
        provenance=TemplateProvenance(
            author=OWNER.id,
            source="issue-445-test",
            trust=TemplateTrust.LOCAL,
        ),
    )
    return TemplateRevision(
        template_id=template_id,
        revision=1,
        state=TemplateRevisionState.DRAFT,
        owner_ref=OWNER,
        content=content,
        project_id=project_id,
    )


def _template_context(routing_profile_ref: str) -> TemplateInstantiationContext:
    return TemplateInstantiationContext(
        instance_id=new_id("template_instance"),
        environment=TemplateEnvironment(model_policy_refs=frozenset({routing_profile_ref})),
        created_resources={},
    )


def _create_routing_profile(
    tmp_path,
    *,
    profile_id: str | None = None,
    project_id: str | None = None,
):
    repository = JsonModelRoutingProfileRepository(tmp_path / f"{new_id('store')}.json")
    revision = asyncio.run(
        ModelRoutingProfileService(repository).create_profile(
            name="Issue 445 profile",
            policy=ModelRoutingProfilePolicy(),
            owner_ref=OWNER,
            principal_ref=OWNER.id,
            context=_operation(project_id),
            project_id=project_id,
            profile_id=profile_id,
        )
    )
    return repository, revision


def _access(
    gate: ModelRoutingProfileAssignmentGate,
    *,
    actor_type: str = "human",
) -> RoutingProfileAssignmentAccess:
    return RoutingProfileAssignmentAccess(
        gate=gate,
        principal_ref=OWNER.id,
        actor_type=actor_type,
        correlation_id="corr-issue-445",
        causation_id="request-issue-445",
    )


def test_agent_template_rejects_hidden_canonical_routing_profile_dependency() -> None:
    routing_ref = ModelRoutingProfileRef(new_model_routing_profile_id(), 1).canonical_ref
    revision = _template_revision(routing_ref, declare=False)
    handler = AgentTemplateHandler(AgentService(InMemoryAgentRepository()))

    with pytest.raises(ContractError) as caught:
        handler.preview(revision)

    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
    assert "declared" in caught.value.message


def test_agent_template_assignment_denial_prevents_agent_creation(tmp_path) -> None:
    repository, routing_profile = _create_routing_profile(tmp_path)
    authorization = FakeAuthorizationProvider(allowed=False)
    agents_repository = InMemoryAgentRepository()
    handler = AgentTemplateHandler(AgentService(agents_repository))
    revision = _template_revision(routing_profile.ref.canonical_ref)

    with activate_routing_profile_assignment_access(
        _access(ModelRoutingProfileAssignmentGate(repository, authorization=authorization))
    ):
        with pytest.raises(ContractError) as caught:
            asyncio.run(
                handler.instantiate(
                    revision,
                    TemplateInstantiationProvenance(
                        source=revision.ref,
                        applied_by=OWNER,
                    ),
                    _template_context(routing_profile.ref.canonical_ref),
                )
            )

    assert caught.value.code is ErrorCode.FORBIDDEN
    assert agents_repository.list_agents() == ()
    assert authorization.calls[-1].action == "model-routing-profile:assign"


def test_agent_template_assignment_authorization_materializes_agent(tmp_path) -> None:
    repository, routing_profile = _create_routing_profile(tmp_path)
    authorization = FakeAuthorizationProvider(allowed=True)
    agents_repository = InMemoryAgentRepository()
    handler = AgentTemplateHandler(AgentService(agents_repository))
    revision = _template_revision(routing_profile.ref.canonical_ref)

    with activate_routing_profile_assignment_access(
        _access(ModelRoutingProfileAssignmentGate(repository, authorization=authorization))
    ):
        created = asyncio.run(
            handler.instantiate(
                revision,
                TemplateInstantiationProvenance(source=revision.ref, applied_by=OWNER),
                _template_context(routing_profile.ref.canonical_ref),
            )
        )

    assert len(created) == 1
    assert created[0].resource_type == "agent"
    assert len(agents_repository.list_agents()) == 1
    assert authorization.calls[-1].action == "model-routing-profile:assign"


def test_portable_agent_import_requires_authenticated_assignment_context(tmp_path) -> None:
    repository, routing_profile = _create_routing_profile(tmp_path)
    source_agents = AgentService(InMemoryAgentRepository())
    source = source_agents.create_agent(
        _agent_profile(routing_profile.ref.canonical_ref),
        owner_ref=OWNER,
    )
    serializers = ResourceSerializerRegistry()
    serializers.register(RoutingProfileAwareAgentPortableCodec())
    resource = serializers.serialize(
        "agent", snapshot_agent(source_agents.repository, source.agent_id)
    )
    snapshot = serializers.deserialize(resource, ImportContext())
    target_repository = InMemoryAgentRepository()
    handler = AgentImportMutationHandler(target_repository)

    with pytest.raises(ContractError) as caught:
        asyncio.run(handler.preflight(resource, snapshot, ImportContext()))

    assert caught.value.code is ErrorCode.UNAUTHORIZED
    assert target_repository.list_agents() == ()
    assert repository.get_definition(routing_profile.profile_id).enabled is True


def test_portable_agent_import_authorizes_before_mutation(tmp_path) -> None:
    repository, routing_profile = _create_routing_profile(tmp_path)
    authorization = FakeAuthorizationProvider(allowed=True)
    source_agents = AgentService(InMemoryAgentRepository())
    source = source_agents.create_agent(
        _agent_profile(routing_profile.ref.canonical_ref),
        owner_ref=OWNER,
    )
    serializers = ResourceSerializerRegistry()
    serializers.register(RoutingProfileAwareAgentPortableCodec())
    resource = serializers.serialize(
        "agent", snapshot_agent(source_agents.repository, source.agent_id)
    )
    context = ImportContext()
    snapshot = serializers.deserialize(resource, context)
    target_repository = InMemoryAgentRepository()
    handler = AgentImportMutationHandler(target_repository)

    with activate_routing_profile_assignment_access(
        _access(ModelRoutingProfileAssignmentGate(repository, authorization=authorization))
    ):
        asyncio.run(handler.preflight(resource, snapshot, context))
        token = asyncio.run(handler.apply(resource, snapshot, context))

    assert token == source.agent_id
    assert target_repository.get_agent(source.agent_id).agent_id == source.agent_id
    assert authorization.calls[-1].action == "model-routing-profile:assign"


def test_template_portability_remaps_declared_and_embedded_agent_profile_refs() -> None:
    source_profile_id = new_model_routing_profile_id()
    target_profile_id = new_model_routing_profile_id()
    source_ref = ModelRoutingProfileRef(source_profile_id, 3).canonical_ref
    target_ref = ModelRoutingProfileRef(target_profile_id, 3).canonical_ref
    repository = InMemoryTemplateRepository()
    service = TemplateService(repository)
    draft = service.create_draft(
        owner_ref=OWNER,
        content=_template_revision(source_ref).content,
    )
    serializers = ResourceSerializerRegistry()
    serializers.register(RoutingProfileAwareTemplatePortableCodec())
    resource = serializers.serialize("template", snapshot_template(repository, draft.template_id))

    restored = serializers.deserialize(
        resource,
        ImportContext(
            id_mapping={
                ("model_routing_profile", source_profile_id): target_profile_id,
            }
        ),
    )
    restored_revision = restored.revisions[0]
    assert restored_revision.content.requirements.model_policy_refs == (target_ref,)
    payload = restored_revision.content.configuration.payload
    assert payload is not None
    profile = agent_profile_from_json(payload["profile"])
    assert profile.model.routing_profile_ref == target_ref


def test_control_plane_wrapper_projects_real_request_identity_to_nested_consumers(tmp_path) -> None:
    repository, _ = _create_routing_profile(tmp_path)
    gate = ModelRoutingProfileAssignmentGate(repository, authorization=FakeAuthorizationProvider())

    class _DummyControlPlane:
        async def execute_command(
            self,
            context: RequestContext,
            command: str,
            resource_ref: str,
            payload: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            del command, resource_ref, payload
            access = current_routing_profile_assignment_access()
            assert access is not None
            return {
                "principal_ref": access.principal_ref,
                "actor_type": access.actor_type,
                "correlation_id": access.correlation_id,
                "causation_id": access.causation_id,
                "request_principal": context.actor.principal_ref,
            }

    control_plane = _DummyControlPlane()
    _install_nested_assignment_context(cast(Any, control_plane), gate)
    request = RequestContext(
        request_id="request-issue-445-projection",
        correlation_id="corr-issue-445-projection",
        actor=ActorContext(
            principal_ref="user:issue-445",
            owner_type="user",
            owner_id=OWNER.id,
        ),
    )

    result = asyncio.run(control_plane.execute_command(request, "test.command", "resource", {}))

    assert result == {
        "principal_ref": "user:issue-445",
        "actor_type": "human",
        "correlation_id": "corr-issue-445-projection",
        "causation_id": "request-issue-445-projection",
        "request_principal": "user:issue-445",
    }
