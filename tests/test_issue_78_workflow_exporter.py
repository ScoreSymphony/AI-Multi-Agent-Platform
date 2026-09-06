from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.agents import AgentService, InMemoryAgentRepository
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.templates import (
    InMemoryTemplateRepository,
    TemplateService,
    TemplateType,
    WorkflowTemplateExporter,
)
from ai_multi_agent_platform.workflows import (
    AuthorizedWorkflowService,
    InMemoryWorkflowRepository,
    WorkflowCallContext,
    WorkflowCapabilityRequirement,
    WorkflowCompatibility,
    WorkflowContent,
    WorkflowParameter,
    WorkflowProvenance,
    WorkflowService,
    WorkflowStage,
)

OWNER = OwnerRef(type="user", id="alice")
PRINCIPAL = "user:alice"


def _context() -> WorkflowCallContext:
    return WorkflowCallContext(
        operation=OperationContext(
            correlation_id="issue-78-workflow-export",
            owner_type=OWNER.type,
            owner_id=OWNER.id,
        ),
        actor_ref=PRINCIPAL,
    )


def _authorization(*, allow_read: bool = True) -> AuthorizationGate:
    actions = (
        frozenset({AuthorizationAction.READ})
        if allow_read
        else frozenset({AuthorizationAction.MODIFY})
    )
    return AuthorizationGate(
        LocalAuthorizationProvider(
            (
                LocalPrincipalPolicy(
                    principal_ref=PRINCIPAL,
                    actor_types=frozenset({ActorType.HUMAN}),
                    allowed_actions=actions,
                    resource_types=frozenset({ResourceType.GENERIC}),
                ),
            )
        )
    )


def _source() -> tuple[WorkflowService, str]:
    workflows = WorkflowService(InMemoryWorkflowRepository())
    revision = workflows.create(
        owner_ref=OWNER,
        content=WorkflowContent(
            name="Reusable research workflow",
            description="Canonical source workflow",
            stages=(
                WorkflowStage(
                    stage_id="research",
                    title="Research",
                    parameter_refs=("topic",),
                    capabilities=(
                        WorkflowCapabilityRequirement(
                            capability_id="tool.search",
                            version_constraint=">=2.0",
                        ),
                    ),
                    model_routing_policy_ref="model_routing_profile_research@r3",
                    permission_actions=("file:read",),
                    metadata={"purpose": "research"},
                ),
            ),
            parameters=(
                WorkflowParameter(
                    name="topic",
                    required=True,
                    description="Research topic",
                ),
            ),
            provenance=WorkflowProvenance(
                creator=PRINCIPAL,
                source="canonical-workflow-test",
            ),
            compatibility=WorkflowCompatibility(
                platform_version_range=">=1.0",
                contract_versions={"workflow": "1"},
            ),
            metadata={"portable": True},
        ),
    )
    return workflows, revision.workflow_id


def test_workflow_export_preserves_portable_configuration_requirements_and_provenance() -> None:
    async def scenario() -> None:
        base, workflow_id = _source()
        templates = TemplateService(InMemoryTemplateRepository())
        exporter = WorkflowTemplateExporter(
            AuthorizedWorkflowService(base, _authorization()),
            AgentService(InMemoryAgentRepository()),
            templates,
        )

        exported = await exporter.create_from_workflow(
            workflow_id,
            context=_context(),
            owner_ref=OWNER,
            author=PRINCIPAL,
            name="Exported research workflow",
        )

        assert exported.content.template_type is TemplateType.WORKFLOW_PLAN
        assert exported.content.name == "Exported research workflow"
        assert exported.content.provenance.source == "canonical-workflow-export"
        assert exported.content.provenance.metadata["source_resource_id"] == workflow_id
        assert exported.content.provenance.metadata["source_resource_revision"] == 1

        payload = exported.content.configuration.payload
        assert payload is not None
        stages = payload["stages"]
        assert isinstance(stages, tuple)
        stage = stages[0]
        assert stage["stage_id"] == "research"
        assert stage["parameter_refs"] == ("topic",)
        assert stage["metadata"] == {"purpose": "research"}
        assert "agent_id" not in stage
        assert "team_id" not in stage

        requirements = exported.content.requirements
        assert len(requirements.capabilities) == 1
        assert requirements.capabilities[0].capability_id == "tool.search"
        assert requirements.capabilities[0].version_constraint == ">=2.0"
        assert requirements.model_policy_refs == ("model_routing_profile_research@r3",)
        assert requirements.permission_actions == ("file:read",)
        assert exported.content.compatibility.platform_version_range == ">=1.0"
        assert exported.content.compatibility.contract_versions == {"workflow": "1"}

    asyncio.run(scenario())


def test_workflow_export_authorizes_source_read_before_creating_templates() -> None:
    async def scenario() -> None:
        base, workflow_id = _source()
        templates = TemplateService(InMemoryTemplateRepository())
        exporter = WorkflowTemplateExporter(
            AuthorizedWorkflowService(base, _authorization(allow_read=False)),
            AgentService(InMemoryAgentRepository()),
            templates,
        )

        with pytest.raises(ContractError) as captured:
            await exporter.create_from_workflow(
                workflow_id,
                context=_context(),
                owner_ref=OWNER,
                author=PRINCIPAL,
            )
        assert captured.value.code is ErrorCode.FORBIDDEN
        assert templates.repository.list_definitions() == ()

    asyncio.run(scenario())
