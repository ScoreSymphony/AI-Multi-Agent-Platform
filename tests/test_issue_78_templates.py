from __future__ import annotations

from dataclasses import replace

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.templates import (
    CapabilityRequirement,
    InMemoryTemplateRepository,
    TemplateConfiguration,
    TemplateContent,
    TemplateDependency,
    TemplateEnvironment,
    TemplateHandlerRegistry,
    TemplateInstantiationProvenance,
    TemplateProvenance,
    TemplateRequirements,
    TemplateResourceChange,
    TemplateResourceRef,
    TemplateRevision,
    TemplateRevisionState,
    TemplateService,
    TemplateTrust,
    TemplateType,
)


class _RecordingHandler:
    def __init__(self, template_type: TemplateType, resource_prefix: str) -> None:
        self.template_type = template_type
        self.resource_prefix = resource_prefix
        self.instantiations: list[TemplateInstantiationProvenance] = []

    def preview(self, revision: TemplateRevision) -> tuple[TemplateResourceChange, ...]:
        return (
            TemplateResourceChange(
                resource_type=revision.content.template_type.value,
                action="create",
                description=revision.content.name,
                privileged=bool(revision.content.requirements.permission_actions),
            ),
        )

    def instantiate(
        self,
        revision: TemplateRevision,
        provenance: TemplateInstantiationProvenance,
    ) -> tuple[TemplateResourceRef, ...]:
        self.instantiations.append(provenance)
        return (
            TemplateResourceRef(
                resource_type=revision.content.template_type.value,
                resource_id=new_id(self.resource_prefix),
            ),
        )


def _owner(name: str = "user-78") -> OwnerRef:
    return OwnerRef(type="user", id=name)


def _content(
    name: str,
    *,
    template_type: TemplateType = TemplateType.AGENT,
    dependencies: tuple[TemplateDependency, ...] = (),
    requirements: TemplateRequirements | None = None,
    source: str = "local",
) -> TemplateContent:
    return TemplateContent(
        name=name,
        description=f"Reusable {name}",
        template_type=template_type,
        configuration=TemplateConfiguration(payload={"name": name, "enabled": True}),
        dependencies=dependencies,
        requirements=requirements or TemplateRequirements(),
        provenance=TemplateProvenance(
            author="issue-78-test",
            source=source,
            trust=TemplateTrust.LOCAL,
        ),
        tags=("test",),
        categories=("example",),
    )


def _service(*handlers: _RecordingHandler) -> TemplateService:
    registry = TemplateHandlerRegistry()
    for handler in handlers:
        registry.register(handler)
    return TemplateService(InMemoryTemplateRepository(), registry)


def test_create_revise_and_publish_preserves_immutable_history() -> None:
    service = _service()
    draft = service.create_draft(owner_ref=_owner(), content=_content("Agent Starter"))
    revised = service.revise_draft(
        draft.template_id,
        replace(draft.content, description="Edited draft"),
        expected_revision=1,
    )
    published = service.publish(draft.template_id, expected_revision=2)

    assert draft.revision == 1
    assert draft.state is TemplateRevisionState.DRAFT
    assert revised.revision == 2
    assert revised.content.description == "Edited draft"
    assert published.revision == 3
    assert published.state is TemplateRevisionState.PUBLISHED
    assert service.repository.get_revision(draft.template_id, 1).content.description == (
        "Reusable Agent Starter"
    )
    assert service.repository.get_revision(draft.template_id, 2).state is TemplateRevisionState.DRAFT
    definition = service.repository.get_template(draft.template_id)
    assert definition.current_revision == 3
    assert definition.latest_published_revision == 3


def test_composite_preview_resolves_published_dependencies_before_root() -> None:
    agent_handler = _RecordingHandler(TemplateType.AGENT, "agent")
    composite_handler = _RecordingHandler(TemplateType.COMPOSITE, "project")
    service = _service(agent_handler, composite_handler)

    ingredient = service.create_draft(owner_ref=_owner(), content=_content("Developer"))
    ingredient = service.publish(ingredient.template_id, expected_revision=1)
    composite = service.create_draft(
        owner_ref=_owner(),
        content=_content(
            "Software Team",
            template_type=TemplateType.COMPOSITE,
            dependencies=(
                TemplateDependency(
                    template_id=ingredient.template_id,
                    revision=ingredient.revision,
                ),
            ),
        ),
    )
    composite = service.publish(composite.template_id, expected_revision=1)

    preview = service.preview(
        composite.template_id,
        applied_by=_owner(),
        environment=TemplateEnvironment(),
    )

    assert preview.applicable is True
    assert preview.dependency_order == (ingredient.ref, composite.ref)
    assert tuple(item.resource_type for item in preview.resource_changes) == ("agent", "composite")


def test_missing_capability_plugin_and_connector_are_reported_before_apply() -> None:
    handler = _RecordingHandler(TemplateType.AGENT, "agent")
    service = _service(handler)
    requirements = TemplateRequirements(
        capabilities=(
            CapabilityRequirement("tool.file.read"),
            CapabilityRequirement("tool.web.read", optional=True),
        ),
        plugin_ids=("plugin.source-control",),
        connector_ids=("connector.github",),
        model_policy_refs=("model-policy.local",),
    )
    draft = service.create_draft(
        owner_ref=_owner(),
        content=_content("Developer", requirements=requirements),
    )
    service.publish(draft.template_id, expected_revision=1)

    preview = service.preview(
        draft.template_id,
        applied_by=_owner(),
        environment=TemplateEnvironment(),
    )

    assert preview.applicable is False
    assert preview.missing_required_capability_ids == ("tool.file.read",)
    assert preview.missing_optional_capability_ids == ("tool.web.read",)
    assert preview.missing_plugin_ids == ("plugin.source-control",)
    assert preview.missing_connector_ids == ("connector.github",)
    assert preview.missing_model_policy_refs == ("model-policy.local",)

    with pytest.raises(ContractError) as exc_info:
        service.apply(
            draft.template_id,
            applied_by=_owner(),
            environment=TemplateEnvironment(),
        )
    assert exc_info.value.code is ErrorCode.INVALID_CONFIGURATION
    assert handler.instantiations == []


def test_permission_escalation_is_rejected_and_privileged_capability_is_visible() -> None:
    handler = _RecordingHandler(TemplateType.AGENT, "agent")
    service = _service(handler)
    requirements = TemplateRequirements(
        capabilities=(CapabilityRequirement("tool.shell.execute", privileged=True),),
        permission_actions=("execute:privileged",),
    )
    draft = service.create_draft(
        owner_ref=_owner(),
        content=_content("Administrator", requirements=requirements),
    )
    service.publish(draft.template_id, expected_revision=1)

    preview = service.preview(
        draft.template_id,
        applied_by=_owner(),
        environment=TemplateEnvironment(capability_ids=frozenset({"tool.shell.execute"})),
    )

    assert preview.privileged_capability_ids == ("tool.shell.execute",)
    assert preview.ungrantable_permissions == ("execute:privileged",)
    assert preview.applicable is False

    with pytest.raises(ContractError) as exc_info:
        service.apply(
            draft.template_id,
            applied_by=_owner(),
            environment=TemplateEnvironment(capability_ids=frozenset({"tool.shell.execute"})),
        )
    assert exc_info.value.code is ErrorCode.FORBIDDEN
    assert exc_info.value.details["permissions"] == ["execute:privileged"]


def test_plaintext_secrets_and_runtime_private_state_are_rejected_before_storage() -> None:
    service = _service()

    secret_content = replace(
        _content("Unsafe"),
        configuration=TemplateConfiguration(payload={"api_key": "plaintext-secret"}),
    )
    with pytest.raises(ContractError) as secret_error:
        service.create_draft(owner_ref=_owner(), content=secret_content)
    assert secret_error.value.code is ErrorCode.INVALID_CONFIGURATION
    assert secret_error.value.details["path"] == "configuration.api_key"

    runtime_content = replace(
        _content("Runtime Snapshot"),
        configuration=TemplateConfiguration(payload={"provider_session_id": "session-123"}),
    )
    with pytest.raises(ContractError) as runtime_error:
        service.create_draft(owner_ref=_owner(), content=runtime_content)
    assert runtime_error.value.code is ErrorCode.INVALID_CONFIGURATION
    assert runtime_error.value.details["path"] == "configuration.provider_session_id"

    assert service.repository.list_templates() == ()


def test_secret_reference_placeholder_is_allowed_but_must_be_resolved_for_apply() -> None:
    handler = _RecordingHandler(TemplateType.AGENT, "agent")
    service = _service(handler)
    content = replace(
        _content("Connector Agent"),
        configuration=TemplateConfiguration(payload={"credential_ref": "${github_credential}"}),
        requirements=TemplateRequirements(
            secret_reference_placeholders=("github_credential",),
        ),
    )
    draft = service.create_draft(owner_ref=_owner(), content=content)
    service.publish(draft.template_id, expected_revision=1)

    missing = service.preview(
        draft.template_id,
        applied_by=_owner(),
        environment=TemplateEnvironment(),
    )
    assert missing.unresolved_secret_reference_placeholders == ("github_credential",)
    assert missing.applicable is False

    ready = service.preview(
        draft.template_id,
        applied_by=_owner(),
        environment=TemplateEnvironment(
            resolved_secret_reference_placeholders=frozenset({"github_credential"})
        ),
    )
    assert ready.applicable is True


def test_external_configuration_reference_requires_validation_before_activation() -> None:
    handler = _RecordingHandler(TemplateType.AGENT, "agent")
    service = _service(handler)
    content = replace(
        _content("Referenced"),
        configuration=TemplateConfiguration(reference="config://templates/agent-v1"),
    )
    draft = service.create_draft(owner_ref=_owner(), content=content)
    service.publish(draft.template_id, expected_revision=1)

    preview = service.preview(
        draft.template_id,
        applied_by=_owner(),
        environment=TemplateEnvironment(),
    )
    assert preview.unvalidated_configuration_refs == ("config://templates/agent-v1",)
    assert preview.applicable is False

    validated = service.preview(
        draft.template_id,
        applied_by=_owner(),
        environment=TemplateEnvironment(
            validated_configuration_refs=frozenset({"config://templates/agent-v1"})
        ),
    )
    assert validated.applicable is True


def test_preview_then_apply_creates_resources_with_source_revision_provenance() -> None:
    handler = _RecordingHandler(TemplateType.AGENT, "agent")
    service = _service(handler)
    draft = service.create_draft(owner_ref=_owner(), content=_content("Researcher"))
    published = service.publish(draft.template_id, expected_revision=1)

    preview = service.preview(
        draft.template_id,
        applied_by=_owner(),
        environment=TemplateEnvironment(),
    )
    instance = service.apply(
        draft.template_id,
        applied_by=_owner(),
        environment=TemplateEnvironment(),
    )

    assert preview.applicable is True
    assert len(preview.resource_changes) == 1
    assert instance.source == published.ref
    assert len(instance.resource_refs) == 1
    assert instance.resource_refs[0].resource_type == "agent"
    assert handler.instantiations[0].source == published.ref
    assert handler.instantiations[0].applied_by == _owner()


def test_clone_and_fork_create_independent_drafts_with_explicit_lineage() -> None:
    service = _service()
    draft = service.create_draft(owner_ref=_owner("source-owner"), content=_content("Base"))
    published = service.publish(draft.template_id, expected_revision=1)

    cloned = service.clone_template(
        draft.template_id,
        owner_ref=_owner("clone-owner"),
        author="clone-author",
        name="Cloned Base",
    )
    forked = service.fork_template(
        draft.template_id,
        owner_ref=_owner("fork-owner"),
        author="fork-author",
        name="Forked Base",
    )

    assert cloned.template_id != published.template_id
    assert forked.template_id not in {published.template_id, cloned.template_id}
    assert cloned.content.provenance.source_template == published.ref
    assert forked.content.provenance.source_template == published.ref
    assert cloned.content.provenance.source.startswith("clone:")
    assert forked.content.provenance.source.startswith("fork:")
    assert cloned.state is TemplateRevisionState.DRAFT
    assert forked.state is TemplateRevisionState.DRAFT


def test_new_template_version_never_mutates_prior_instance_or_source_revision() -> None:
    handler = _RecordingHandler(TemplateType.AGENT, "agent")
    service = _service(handler)
    draft = service.create_draft(owner_ref=_owner(), content=_content("Versioned"))
    first_published = service.publish(draft.template_id, expected_revision=1)
    first_instance = service.apply(
        draft.template_id,
        applied_by=_owner(),
        environment=TemplateEnvironment(),
    )

    next_draft = service.revise_draft(
        draft.template_id,
        replace(first_published.content, description="Version two"),
        expected_revision=2,
    )
    second_published = service.publish(draft.template_id, expected_revision=next_draft.revision)

    assert first_instance.source == first_published.ref
    assert second_published.ref != first_instance.source
    assert service.repository.get_revision(draft.template_id, first_published.revision).content.description == (
        "Reusable Versioned"
    )
    assert first_instance.resource_refs[0].resource_id.startswith("agent_")


def test_provider_and_orchestrator_replacement_do_not_change_template_contract() -> None:
    handler = _RecordingHandler(TemplateType.AGENT, "agent")
    service = _service(handler)
    draft = service.create_draft(owner_ref=_owner(), content=_content("Portable"))
    published = service.publish(draft.template_id, expected_revision=1)

    first = service.preview(
        draft.template_id,
        applied_by=_owner(),
        environment=TemplateEnvironment(),
    )
    second = service.preview(
        draft.template_id,
        applied_by=_owner(),
        environment=TemplateEnvironment(),
    )

    assert published.content.compatibility.orchestrator_agnostic is True
    assert published.content.compatibility.provider_agnostic is True
    assert first == second
