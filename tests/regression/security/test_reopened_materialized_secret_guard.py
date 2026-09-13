from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.templates.application import (
    ContextualTemplateHandlerRegistry,
    TemplateApplicationService,
    TemplateInstantiationContext,
)
from ai_multi_agent_platform.templates.materialization import MaterializingTemplateEnvironment
from ai_multi_agent_platform.templates.models import (
    TemplateConfiguration,
    TemplateContent,
    TemplateInstantiationProvenance,
    TemplateProvenance,
    TemplateRequirements,
    TemplateResourceChange,
    TemplateResourceRef,
    TemplateRevision,
    TemplateType,
)
from ai_multi_agent_platform.templates.repository import InMemoryTemplateRepository

OWNER = OwnerRef(type="user", id="issue-78-materialized-secret-guard")


@dataclass
class _RecordingHandler:
    calls: list[TemplateRevision] = field(default_factory=list)
    template_type = TemplateType.AGENT

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
        self.calls.append(revision)
        return ()


def _application() -> tuple[TemplateApplicationService, _RecordingHandler]:
    repository = InMemoryTemplateRepository()
    registry = ContextualTemplateHandlerRegistry()
    handler = _RecordingHandler()
    registry.register(handler)
    return TemplateApplicationService(repository, registry), handler


def _publish(
    application: TemplateApplicationService,
    configuration: TemplateConfiguration,
    *,
    requirements: TemplateRequirements | None = None,
) -> TemplateRevision:
    draft = application.templates.create_draft(
        owner_ref=OWNER,
        content=TemplateContent(
            name="Materialized safety guard",
            description="Reject forbidden fields introduced only during materialization",
            template_type=TemplateType.AGENT,
            configuration=configuration,
            requirements=TemplateRequirements() if requirements is None else requirements,
            provenance=TemplateProvenance(author="test", source="test"),
        ),
    )
    return application.templates.publish(draft.template_id, expected_revision=draft.revision)


def test_configuration_reference_plaintext_secret_is_rejected_before_handler() -> None:
    async def scenario() -> None:
        application, handler = _application()
        reference = "config://issue-78/unsafe-secret"
        published = _publish(application, TemplateConfiguration(reference=reference))
        environment = MaterializingTemplateEnvironment(
            validated_configuration_refs=frozenset({reference}),
            configuration_payloads={reference: {"name": "Agent", "api_key": "plaintext"}},
        )

        with pytest.raises(ContractError) as exc_info:
            await application.apply(
                published.template_id,
                applied_by=OWNER,
                environment=environment,
            )

        assert exc_info.value.code is ErrorCode.INVALID_CONFIGURATION
        assert exc_info.value.details["path"] == "configuration.api_key"
        assert handler.calls == []

    asyncio.run(scenario())


def test_object_placeholder_runtime_private_state_is_rejected_before_handler() -> None:
    async def scenario() -> None:
        application, handler = _application()
        published = _publish(
            application,
            TemplateConfiguration(payload={"settings": "${settings}"}),
            requirements=TemplateRequirements(placeholders=("settings",)),
        )
        environment = MaterializingTemplateEnvironment(
            resolved_placeholders=frozenset({"settings"}),
            placeholder_bindings={
                "settings": {
                    "provider_session_id": "provider-private-session",
                }
            },
        )

        with pytest.raises(ContractError) as exc_info:
            await application.apply(
                published.template_id,
                applied_by=OWNER,
                environment=environment,
            )

        assert exc_info.value.code is ErrorCode.INVALID_CONFIGURATION
        assert exc_info.value.details["path"] == "configuration.settings.provider_session_id"
        assert handler.calls == []

    asyncio.run(scenario())
