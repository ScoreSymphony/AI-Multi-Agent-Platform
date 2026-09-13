from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from ai_multi_agent_platform.agents import (
    AgentCapabilityPolicy,
    AgentInstructions,
    AgentProfile,
    CapabilityConstraint,
    InstructionSource,
)
from ai_multi_agent_platform.capabilities import ECHO_CAPABILITY_ID, NativeEchoProvider
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.templates.application import (
    ContextualTemplateHandlerRegistry,
    TemplateApplicationService,
    TemplateInstantiationContext,
)
from ai_multi_agent_platform.templates.models import (
    TemplateConfiguration,
    TemplateContent,
    TemplateDependency,
    TemplateInstantiationProvenance,
    TemplateProvenance,
    TemplateResourceChange,
    TemplateResourceRef,
    TemplateRevision,
    TemplateTrust,
    TemplateType,
)
from ai_multi_agent_platform.templates.repository import InMemoryTemplateRepository
from ai_multi_agent_platform.templates.service import TemplateEnvironment

PASSWORD = "correct horse battery staple"
OWNER = OwnerRef(type="user", id="template-compensation-owner")


def _capability_profile() -> AgentProfile:
    return AgentProfile(
        name="Capability-aware template source",
        role="worker",
        instructions=AgentInstructions(
            role=InstructionSource(content="Use the required canonical capability."),
        ),
        capabilities=AgentCapabilityPolicy(
            allowed=(ECHO_CAPABILITY_ID,),
            constraints=(CapabilityConstraint(ECHO_CAPABILITY_ID),),
        ),
    )


def _template_content(
    name: str,
    template_type: TemplateType,
    *,
    dependencies: tuple[TemplateDependency, ...] = (),
) -> TemplateContent:
    return TemplateContent(
        name=name,
        description=name,
        template_type=template_type,
        configuration=TemplateConfiguration(payload={"name": name}),
        dependencies=dependencies,
        provenance=TemplateProvenance(
            author="test",
            source="test",
            trust=TemplateTrust.LOCAL,
        ),
    )


@dataclass
class _CreatingHandler:
    created_ids: list[str]
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
        del revision, provenance, context
        resource_id = new_id("agent")
        self.created_ids.append(resource_id)
        return (TemplateResourceRef(resource_type="agent", resource_id=resource_id),)


@dataclass
class _FailingHandler:
    template_type = TemplateType.AUTOMATION

    def preview(self, revision: TemplateRevision) -> tuple[TemplateResourceChange, ...]:
        del revision
        return (TemplateResourceChange(resource_type="automation", action="create"),)

    async def instantiate(
        self,
        revision: TemplateRevision,
        provenance: TemplateInstantiationProvenance,
        context: TemplateInstantiationContext,
    ) -> tuple[TemplateResourceRef, ...]:
        del revision, provenance, context
        raise ContractError(ErrorCode.BACKEND_ERROR, "simulated downstream creation failure")


@dataclass
class _RecordingCompensator:
    compensated_ids: list[str] = field(default_factory=list)

    async def compensate(
        self,
        resources: tuple[TemplateResourceRef, ...],
        provenance: TemplateInstantiationProvenance,
        context: TemplateInstantiationContext,
    ) -> None:
        del provenance, context
        self.compensated_ids.extend(resource.resource_id for resource in resources)


def test_failed_composite_apply_compensates_already_created_dependencies() -> None:
    async def scenario() -> None:
        repository = InMemoryTemplateRepository()
        registry = ContextualTemplateHandlerRegistry()
        created_ids: list[str] = []
        registry.register(_CreatingHandler(created_ids))
        registry.register(_FailingHandler())
        compensator = _RecordingCompensator()
        registry.register_compensator(TemplateType.AGENT, compensator)
        application = TemplateApplicationService(repository, registry)

        dependency = application.templates.create_draft(
            owner_ref=OWNER,
            content=_template_content("Dependency", TemplateType.AGENT),
        )
        dependency_published = application.templates.publish(
            dependency.template_id,
            expected_revision=dependency.revision,
        )
        root = application.templates.create_draft(
            owner_ref=OWNER,
            content=_template_content(
                "Failing root",
                TemplateType.AUTOMATION,
                dependencies=(
                    TemplateDependency(
                        dependency.template_id,
                        revision=dependency_published.revision,
                    ),
                ),
            ),
        )
        root_published = application.templates.publish(
            root.template_id,
            expected_revision=root.revision,
        )

        with pytest.raises(ContractError) as exc_info:
            await application.apply(
                root.template_id,
                applied_by=OWNER,
                environment=TemplateEnvironment(),
                revision=root_published.revision,
            )
        assert exc_info.value.code is ErrorCode.BACKEND_ERROR
        assert len(created_ids) == 1
        assert compensator.compensated_ids == created_ids
        assert repository.list_instantiations(root.template_id) == ()

    asyncio.run(scenario())


def test_single_node_template_preview_uses_live_canonical_capability_inventory(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        )
        admin = deployment.bootstrap_admin("admin", PASSWORD)
        owner = OwnerRef(type="user", id=admin.user_id)
        source = deployment.agents.create_agent(_capability_profile(), owner_ref=owner)
        assert deployment.agent_runtime.capability_registry is deployment.capabilities

        actor = ActorContext(
            principal_ref=admin.user_id,
            owner_type="user",
            owner_id=admin.user_id,
        )
        created = await deployment.control_plane.execute_command(
            RequestContext(
                request_id="template-capability-create",
                correlation_id="template-capability",
                actor=actor,
                idempotency_key="template-capability-create",
            ),
            "template.create-from-agent",
            "templates",
            {"agent_id": source.agent_id},
        )
        template_id = created["id"]
        assert isinstance(template_id, str)
        await deployment.control_plane.execute_command(
            RequestContext(
                request_id="template-capability-publish",
                correlation_id="template-capability",
                actor=actor,
                idempotency_key="template-capability-publish",
            ),
            "template.publish",
            template_id,
            {"expected_revision": 1},
        )

        before = await deployment.control_plane.execute_command(
            RequestContext(
                request_id="template-capability-preview-before",
                correlation_id="template-capability",
                actor=actor,
                idempotency_key="template-capability-preview-before",
            ),
            "template.preview",
            template_id,
            {},
        )
        assert before["applicable"] is False
        assert before["missing_required_capability_ids"] == [ECHO_CAPABILITY_ID]

        await deployment.capabilities.register_provider(NativeEchoProvider())

        after = await deployment.control_plane.execute_command(
            RequestContext(
                request_id="template-capability-preview-after",
                correlation_id="template-capability",
                actor=actor,
                idempotency_key="template-capability-preview-after",
            ),
            "template.preview",
            template_id,
            {},
        )
        assert after["applicable"] is True
        assert after["missing_required_capability_ids"] == []

    asyncio.run(scenario())
