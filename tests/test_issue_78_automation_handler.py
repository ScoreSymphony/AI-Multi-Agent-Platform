from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.automation import (
    AutomationService,
    IdentityContext,
    InMemoryAutomationRepository,
    TaskTemplate,
    TriggerDefinition,
    TriggerType,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.templates import (
    AutomationTemplateExporter,
    ContextualTemplateHandlerRegistry,
    InMemoryTemplateRepository,
    TemplateApplicationService,
    TemplateConfiguration,
    TemplateContent,
    TemplateEnvironment,
    TemplateProvenance,
    TemplateType,
    register_automation_template_handler,
)


async def _unused_task_creator(*args: object) -> str:
    del args
    return new_id("task")


def _automation_service() -> AutomationService:
    return AutomationService(
        repository=InMemoryAutomationRepository(),
        task_creator=_unused_task_creator,
    )


def _owner(owner_id: str = "automation-template-target") -> OwnerRef:
    return OwnerRef(type="user", id=owner_id)


def _application(service: AutomationService) -> TemplateApplicationService:
    registry = ContextualTemplateHandlerRegistry()
    register_automation_template_handler(registry, service)
    return TemplateApplicationService(InMemoryTemplateRepository(), registry)


def test_existing_automation_export_roundtrips_without_runtime_identity_or_state() -> None:
    async def scenario() -> None:
        service = _automation_service()
        application = _application(service)
        source = await service.create_automation(
            name="Daily review",
            description="Reusable manual review automation",
            identity=IdentityContext(
                principal_ref="source-user",
                owner_type="user",
                owner_id="source-user",
            ),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=TaskTemplate(
                title="Review",
                objective="Review the current project state",
                payload={"labels": ["review"]},
            ),
        )
        exporter = AutomationTemplateExporter(service, application.templates)
        draft = await exporter.create_from_automation(
            source.id,
            owner_ref=_owner(),
            author="issue-78-test",
        )
        published = application.templates.publish(
            draft.template_id,
            expected_revision=draft.revision,
        )

        instance = await application.apply(
            published.template_id,
            applied_by=_owner(),
            environment=TemplateEnvironment(),
        )
        assert len(instance.resource_refs) == 1
        created_id = instance.resource_refs[0].resource_id
        assert created_id != source.id
        created = await service.get_automation(created_id)
        assert created.name == source.name
        assert created.description == source.description
        assert created.trigger == source.trigger
        assert created.task_template == source.task_template
        assert created.identity.owner_type == "user"
        assert created.identity.owner_id == _owner().id
        assert created.identity.principal_ref == _owner().id

        payload = published.content.configuration.payload
        assert payload is not None
        for forbidden in (
            "identity",
            "state",
            "revision",
            "created_at",
            "updated_at",
            "last_evaluated_at",
            "next_evaluation_at",
            "deliveries",
        ):
            assert forbidden not in payload

    asyncio.run(scenario())


def test_automation_template_preview_rejects_embedded_webhook_secret_fields() -> None:
    service = _automation_service()
    application = _application(service)
    draft = application.templates.create_draft(
        owner_ref=_owner(),
        content=TemplateContent(
            name="Unsafe webhook",
            description="Must fail closed before Automation creation",
            template_type=TemplateType.AUTOMATION,
            configuration=TemplateConfiguration(
                payload={
                    "name": "Unsafe webhook",
                    "trigger": {
                        "type": "webhook",
                        "webhook_source": "example",
                        "token": "embedded-value",
                    },
                    "task_template": {
                        "title": "Webhook task",
                        "objective": "Handle webhook",
                    },
                }
            ),
            provenance=TemplateProvenance(author="issue-78-test", source="test"),
        ),
    )
    published = application.templates.publish(
        draft.template_id,
        expected_revision=draft.revision,
    )

    with pytest.raises(ContractError) as exc_info:
        application.preview(
            published.template_id,
            applied_by=_owner(),
            environment=TemplateEnvironment(),
        )
    assert exc_info.value.code is ErrorCode.INVALID_CONFIGURATION
    assert "webhook secrets" in str(exc_info.value)
