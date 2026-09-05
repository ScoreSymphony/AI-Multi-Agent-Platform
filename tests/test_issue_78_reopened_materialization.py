from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.security import SecretReference
from ai_multi_agent_platform.templates.application import (
    ContextualTemplateHandlerRegistry,
    TemplateApplicationService,
    TemplateInstantiationContext,
)
from ai_multi_agent_platform.templates.materialization import MaterializingTemplateEnvironment
from ai_multi_agent_platform.templates.models import (
    TemplateConfiguration,
    TemplateContent,
    TemplateDependency,
    TemplateInstantiationProvenance,
    TemplateProvenance,
    TemplateRequirements,
    TemplateResourceChange,
    TemplateResourceRef,
    TemplateRevision,
    TemplateTrust,
    TemplateType,
)
from ai_multi_agent_platform.templates.repository import InMemoryTemplateRepository

OWNER = OwnerRef(type="user", id="issue-78-materialization")


@dataclass
class _RecordingAgentHandler:
    template_type = TemplateType.AGENT
    revisions: list[TemplateRevision] = field(default_factory=list)

    def preview(self, revision: TemplateRevision) -> tuple[TemplateResourceChange, ...]:
        del revision
        return (TemplateResourceChange(resource_type="agent", action="create"),)

    async def instantiate(
        self,
        revision: TemplateRevision,
        provenance: TemplateInstantiationProvenance,
        context: TemplateInstantiationContext,
    ) -> tuple[TemplateResourceRef, ...]:
        del provenance, context
        self.revisions.append(revision)
        return ()


def _application() -> tuple[TemplateApplicationService, _RecordingAgentHandler]:
    registry = ContextualTemplateHandlerRegistry()
    handler = _RecordingAgentHandler()
    registry.register(handler)
    return TemplateApplicationService(InMemoryTemplateRepository(), registry), handler


def _content(
    name: str,
    *,
    template_type: TemplateType = TemplateType.AGENT,
    configuration: TemplateConfiguration | None = None,
    requirements: TemplateRequirements | None = None,
    dependencies: tuple[TemplateDependency, ...] = (),
) -> TemplateContent:
    return TemplateContent(
        name=name,
        description=name,
        template_type=template_type,
        configuration=configuration or TemplateConfiguration(payload={"name": name}),
        requirements=requirements or TemplateRequirements(),
        dependencies=dependencies,
        provenance=TemplateProvenance(
            author="issue-78-test",
            source="local",
            trust=TemplateTrust.LOCAL,
        ),
    )


def _publish(application: TemplateApplicationService, content: TemplateContent) -> TemplateRevision:
    draft = application.templates.create_draft(owner_ref=OWNER, content=content)
    return application.templates.publish(draft.template_id, expected_revision=draft.revision)


def _payload(revision: TemplateRevision) -> Mapping[str, object]:
    payload = revision.content.configuration.payload
    assert payload is not None
    return payload


def test_apply_materializes_ordinary_placeholder_values_without_mutating_template() -> None:
    async def scenario() -> None:
        application, handler = _application()
        published = _publish(
            application,
            _content(
                "Bound agent",
                configuration=TemplateConfiguration(
                    payload={
                        "name": "Agent ${suffix}",
                        "retry_limit": "${retry_limit}",
                    }
                ),
                requirements=TemplateRequirements(placeholders=("suffix", "retry_limit")),
            ),
        )
        environment = MaterializingTemplateEnvironment(
            resolved_placeholders=frozenset({"suffix", "retry_limit"}),
            placeholder_bindings={"suffix": "Alpha", "retry_limit": 3},
        )

        preview = application.preview(
            published.template_id,
            applied_by=OWNER,
            environment=environment,
            revision=published.revision,
        )
        assert preview.applicable is True

        await application.apply(
            published.template_id,
            applied_by=OWNER,
            environment=environment,
            revision=published.revision,
        )

        assert len(handler.revisions) == 1
        assert _payload(handler.revisions[0]) == {"name": "Agent Alpha", "retry_limit": 3}
        persisted = application.repository.get_revision(published.template_id, published.revision)
        assert _payload(persisted) == {
            "name": "Agent ${suffix}",
            "retry_limit": "${retry_limit}",
        }

    asyncio.run(scenario())


def test_apply_requires_real_binding_even_when_preview_name_is_marked_resolved() -> None:
    async def scenario() -> None:
        application, handler = _application()
        published = _publish(
            application,
            _content(
                "Missing binding",
                configuration=TemplateConfiguration(payload={"name": "${agent_name}"}),
                requirements=TemplateRequirements(placeholders=("agent_name",)),
            ),
        )
        environment = MaterializingTemplateEnvironment(
            resolved_placeholders=frozenset({"agent_name"})
        )
        assert application.preview(
            published.template_id,
            applied_by=OWNER,
            environment=environment,
        ).applicable is True

        with pytest.raises(ContractError) as exc_info:
            await application.apply(
                published.template_id,
                applied_by=OWNER,
                environment=environment,
            )
        assert exc_info.value.code is ErrorCode.INVALID_CONFIGURATION
        assert exc_info.value.details["placeholder"] == "agent_name"
        assert handler.revisions == []

    asyncio.run(scenario())


def test_secret_placeholder_materializes_only_canonical_secret_reference_metadata() -> None:
    async def scenario() -> None:
        application, handler = _application()
        published = _publish(
            application,
            _content(
                "Secret-bound agent",
                configuration=TemplateConfiguration(
                    payload={"credential_ref": "${model_credential}"}
                ),
                requirements=TemplateRequirements(
                    secret_reference_placeholders=("model_credential",)
                ),
            ),
        )
        reference = SecretReference(
            provider="local",
            secret_id="model-api-key",
            scope="model-provider",
            version="7",
        )
        environment = MaterializingTemplateEnvironment(
            resolved_secret_reference_placeholders=frozenset({"model_credential"}),
            secret_reference_bindings={"model_credential": reference},
        )

        await application.apply(
            published.template_id,
            applied_by=OWNER,
            environment=environment,
        )

        assert _payload(handler.revisions[0])["credential_ref"] == reference.to_dict()
        persisted = application.repository.get_revision(published.template_id, published.revision)
        assert _payload(persisted)["credential_ref"] == "${model_credential}"

    asyncio.run(scenario())


def test_secret_reference_placeholder_cannot_be_embedded_into_plaintext_string() -> None:
    async def scenario() -> None:
        application, handler = _application()
        published = _publish(
            application,
            _content(
                "Invalid secret interpolation",
                configuration=TemplateConfiguration(
                    payload={"authorization": "Bearer ${credential}"}
                ),
                requirements=TemplateRequirements(
                    secret_reference_placeholders=("credential",)
                ),
            ),
        )
        environment = MaterializingTemplateEnvironment(
            resolved_secret_reference_placeholders=frozenset({"credential"}),
            secret_reference_bindings={
                "credential": SecretReference(
                    provider="local",
                    secret_id="connector-token",
                    scope="connector",
                )
            },
        )

        with pytest.raises(ContractError) as exc_info:
            await application.apply(
                published.template_id,
                applied_by=OWNER,
                environment=environment,
            )
        assert exc_info.value.code is ErrorCode.INVALID_CONFIGURATION
        assert "cannot be interpolated" in exc_info.value.message
        assert handler.revisions == []

    asyncio.run(scenario())


def test_configuration_reference_materializes_payload_and_then_applies_placeholders() -> None:
    async def scenario() -> None:
        application, handler = _application()
        reference = "config://templates/agent-v2"
        published = _publish(
            application,
            _content(
                "Referenced agent",
                configuration=TemplateConfiguration(reference=reference),
                requirements=TemplateRequirements(placeholders=("agent_name",)),
            ),
        )
        environment = MaterializingTemplateEnvironment(
            validated_configuration_refs=frozenset({reference}),
            resolved_placeholders=frozenset({"agent_name"}),
            configuration_payloads={
                reference: {"name": "${agent_name}", "mode": "referenced"}
            },
            placeholder_bindings={"agent_name": "Resolved from reference"},
        )

        await application.apply(
            published.template_id,
            applied_by=OWNER,
            environment=environment,
        )

        assert _payload(handler.revisions[0]) == {
            "name": "Resolved from reference",
            "mode": "referenced",
        }
        persisted = application.repository.get_revision(published.template_id, published.revision)
        assert persisted.content.configuration.reference == reference
        assert persisted.content.configuration.payload is None

    asyncio.run(scenario())


def test_validated_configuration_reference_without_payload_blocks_before_handler() -> None:
    async def scenario() -> None:
        application, handler = _application()
        reference = "config://templates/missing"
        published = _publish(
            application,
            _content(
                "Missing referenced payload",
                configuration=TemplateConfiguration(reference=reference),
            ),
        )
        environment = MaterializingTemplateEnvironment(
            validated_configuration_refs=frozenset({reference})
        )
        assert application.preview(
            published.template_id,
            applied_by=OWNER,
            environment=environment,
        ).applicable is True

        with pytest.raises(ContractError) as exc_info:
            await application.apply(
                published.template_id,
                applied_by=OWNER,
                environment=environment,
            )
        assert exc_info.value.code is ErrorCode.INVALID_CONFIGURATION
        assert exc_info.value.details["configuration_reference"] == reference
        assert handler.revisions == []

    asyncio.run(scenario())


def test_complete_dependency_graph_is_materialized_before_any_resource_handler_runs() -> None:
    async def scenario() -> None:
        application, handler = _application()
        dependency = _publish(application, _content("Dependency agent"))
        root = _publish(
            application,
            _content(
                "Composite root",
                template_type=TemplateType.COMPOSITE,
                configuration=TemplateConfiguration(payload={"name": "${root_name}"}),
                requirements=TemplateRequirements(placeholders=("root_name",)),
                dependencies=(
                    TemplateDependency(
                        template_id=dependency.template_id,
                        revision=dependency.revision,
                    ),
                ),
            ),
        )
        environment = MaterializingTemplateEnvironment(
            resolved_placeholders=frozenset({"root_name"})
        )

        with pytest.raises(ContractError) as exc_info:
            await application.apply(
                root.template_id,
                applied_by=OWNER,
                environment=environment,
                revision=root.revision,
            )
        assert exc_info.value.code is ErrorCode.INVALID_CONFIGURATION
        assert handler.revisions == []

    asyncio.run(scenario())
