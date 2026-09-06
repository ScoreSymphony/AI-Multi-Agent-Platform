from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentService,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.templates import (
    AgentTemplateExporter,
    ContextualTemplateHandlerRegistry,
    InMemoryTemplateRepository,
    TemplateApplicationService,
    TemplateConfiguration,
    TemplateContent,
    TemplateDependency,
    TemplateEnvironment,
    TemplateInstantiationContext,
    TemplateInstantiationProvenance,
    TemplateProvenance,
    TemplateRequirements,
    TemplateResourceChange,
    TemplateResourceRef,
    TemplateRevision,
    TemplateType,
    register_agent_template_handlers,
    register_workflow_template_handler,
)
from ai_multi_agent_platform.workflows import (
    AuthorizedWorkflowService,
    InMemoryWorkflowRepository,
    JsonWorkflowRepository,
    WorkflowService,
)

OWNER = OwnerRef(type="user", id="issue-78-workflow-template-user")


def _profile(name: str) -> AgentProfile:
    return AgentProfile(
        name=name,
        role="worker",
        instructions=AgentInstructions(role=InstructionSource(content=f"Act as {name}.")),
        metadata={"portable": True},
    )


def _authorization(
    *,
    project_ids: frozenset[str] = frozenset(),
) -> AuthorizationGate:
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref=f"user:{OWNER.id}",
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset(
                    {
                        AuthorizationAction.CREATE,
                        AuthorizationAction.READ,
                        AuthorizationAction.MODIFY,
                        AuthorizationAction.EXECUTE,
                    }
                ),
                resource_types=frozenset({ResourceType.GENERIC}),
                project_ids=project_ids,
            ),
        )
    )
    return AuthorizationGate(provider)


def _workflow_content(
    *,
    agent_template_id: str | None = None,
    agent_template_revision: int | None = None,
) -> TemplateContent:
    stage: dict[str, object] = {
        "stage_id": "research",
        "title": "Research",
        "description": "Perform the canonical research stage",
        "metadata": {"portable": True},
    }
    if agent_template_id is not None:
        stage["agent_template_id"] = agent_template_id
        stage["agent_template_revision"] = agent_template_revision
    dependencies = (
        (TemplateDependency(agent_template_id, agent_template_revision),)
        if agent_template_id is not None
        else ()
    )
    return TemplateContent(
        name="Portable research workflow",
        description="Canonical workflow created from a Template",
        template_type=TemplateType.WORKFLOW_PLAN,
        configuration=TemplateConfiguration(
            payload={
                "stages": (stage,),
                "parameters": (
                    {
                        "name": "query",
                        "required": True,
                        "secret_reference": False,
                        "description": "Research query",
                    },
                ),
                "metadata": {"portable": True},
            }
        ),
        dependencies=dependencies,
        requirements=TemplateRequirements(),
        provenance=TemplateProvenance(author="issue-78-test", source="test"),
    )


def test_workflow_template_preview_apply_and_restart_preserve_canonical_intent(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        path = tmp_path / "workflows.json"
        base = WorkflowService(JsonWorkflowRepository(path))
        workflows = AuthorizedWorkflowService(base, _authorization())
        handlers = ContextualTemplateHandlerRegistry()
        register_workflow_template_handler(handlers, workflows)
        application = TemplateApplicationService(InMemoryTemplateRepository(), handlers)

        draft = application.templates.create_draft(owner_ref=OWNER, content=_workflow_content())
        published = application.templates.publish(
            draft.template_id,
            expected_revision=draft.revision,
        )

        preview = application.preview(
            published.template_id,
            applied_by=OWNER,
            environment=TemplateEnvironment(platform_version=__version__),
        )
        assert preview.applicable is True
        assert preview.missing_handler_types == ()
        assert tuple(item.resource_type for item in preview.resource_changes) == ("workflow",)

        instance = await application.apply(
            published.template_id,
            applied_by=OWNER,
            environment=TemplateEnvironment(platform_version=__version__),
        )
        assert len(instance.resource_refs) == 1
        resource = instance.resource_refs[0]
        assert resource.resource_type == "workflow"

        stored = base.resolve(base.list_revisions(resource.resource_id)[0].ref)
        assert stored.owner_ref == OWNER
        assert stored.content.name == published.content.name
        assert stored.content.stages[0].stage_id == "research"
        assert stored.content.parameters[0].name == "query"
        assert stored.content.provenance.source == (
            f"template:{published.template_id}@{published.revision}"
        )
        assert stored.content.provenance.metadata["template_instance_id"] == instance.instance_id
        assert stored.content.compatibility.provider_agnostic is True
        assert stored.content.compatibility.orchestrator_agnostic is True

        restored = WorkflowService(JsonWorkflowRepository(path))
        restored_revision = restored.resolve(stored.ref)
        assert restored_revision == stored
        assert restored_revision.content.metadata == stored.content.metadata

    asyncio.run(scenario())


def test_workflow_template_remaps_agent_template_dependency_to_created_agent() -> None:
    async def scenario() -> None:
        agents = AgentService(InMemoryAgentRepository())
        base = WorkflowService(InMemoryWorkflowRepository())
        workflows = AuthorizedWorkflowService(base, _authorization())
        handlers = ContextualTemplateHandlerRegistry()
        register_agent_template_handlers(handlers, agents)
        register_workflow_template_handler(handlers, workflows, agents=agents)
        application = TemplateApplicationService(InMemoryTemplateRepository(), handlers)

        source = agents.create_agent(_profile("Researcher"), owner_ref=OWNER)
        agent_exporter = AgentTemplateExporter(agents, application.templates)
        agent_draft = agent_exporter.create_from_agent(
            source.agent_id,
            owner_ref=OWNER,
            author="issue-78-test",
        )
        agent_template = application.templates.publish(
            agent_draft.template_id,
            expected_revision=agent_draft.revision,
        )
        workflow_draft = application.templates.create_draft(
            owner_ref=OWNER,
            content=_workflow_content(
                agent_template_id=agent_template.template_id,
                agent_template_revision=agent_template.revision,
            ),
        )
        workflow_template = application.templates.publish(
            workflow_draft.template_id,
            expected_revision=workflow_draft.revision,
        )

        preview = application.preview(
            workflow_template.template_id,
            applied_by=OWNER,
            environment=TemplateEnvironment(),
        )
        assert [item.resource_type for item in preview.resource_changes] == ["agent", "workflow"]

        instance = await application.apply(
            workflow_template.template_id,
            applied_by=OWNER,
            environment=TemplateEnvironment(),
        )
        created_agent_id = next(
            item.resource_id for item in instance.resource_refs if item.resource_type == "agent"
        )
        workflow_id = next(
            item.resource_id for item in instance.resource_refs if item.resource_type == "workflow"
        )
        workflow = base.list_revisions(workflow_id)[0]

        assert created_agent_id != source.agent_id
        assert workflow.content.stages[0].agent is not None
        assert workflow.content.stages[0].agent.agent_id == created_agent_id
        assert workflow.content.stages[0].agent.revision == 1

    asyncio.run(scenario())


def test_workflow_template_target_project_is_authorized_before_creation() -> None:
    async def scenario() -> None:
        allowed_project = new_id("project")
        forbidden_project = new_id("project")
        base = WorkflowService(InMemoryWorkflowRepository())
        workflows = AuthorizedWorkflowService(
            base,
            _authorization(project_ids=frozenset({allowed_project})),
        )
        handlers = ContextualTemplateHandlerRegistry()
        register_workflow_template_handler(handlers, workflows)
        application = TemplateApplicationService(InMemoryTemplateRepository(), handlers)

        draft = application.templates.create_draft(
            owner_ref=OWNER,
            project_id=forbidden_project,
            content=_workflow_content(),
        )
        published = application.templates.publish(
            draft.template_id,
            expected_revision=draft.revision,
        )

        with pytest.raises(ContractError):
            await application.apply(
                published.template_id,
                applied_by=OWNER,
                environment=TemplateEnvironment(),
            )
        assert base.list() == ()

    asyncio.run(scenario())


@dataclass(slots=True)
class _FailingAutomationHandler:
    template_type = TemplateType.AUTOMATION

    def preview(self, revision: TemplateRevision) -> tuple[TemplateResourceChange, ...]:
        return (
            TemplateResourceChange(
                resource_type="automation",
                action="create",
                description=f"Fail after {revision.template_id}@{revision.revision}",
            ),
        )

    async def instantiate(
        self,
        revision: TemplateRevision,
        provenance: TemplateInstantiationProvenance,
        context: TemplateInstantiationContext,
    ) -> tuple[TemplateResourceRef, ...]:
        del revision, provenance, context
        raise RuntimeError("intentional workflow compensation regression")


def test_composite_failure_persistently_compensates_created_workflow(tmp_path: Path) -> None:
    async def scenario() -> None:
        workflow_path = tmp_path / "workflows.json"
        base = WorkflowService(JsonWorkflowRepository(workflow_path))
        workflows = AuthorizedWorkflowService(base, _authorization())
        handlers = ContextualTemplateHandlerRegistry()
        register_workflow_template_handler(handlers, workflows)
        handlers.register(_FailingAutomationHandler())
        application = TemplateApplicationService(InMemoryTemplateRepository(), handlers)

        workflow_draft = application.templates.create_draft(
            owner_ref=OWNER,
            content=_workflow_content(),
        )
        workflow_template = application.templates.publish(
            workflow_draft.template_id,
            expected_revision=workflow_draft.revision,
        )
        failing_draft = application.templates.create_draft(
            owner_ref=OWNER,
            content=TemplateContent(
                name="Failing dependency",
                description="Regression-only failure",
                template_type=TemplateType.AUTOMATION,
                configuration=TemplateConfiguration(payload={"portable": True}),
                provenance=TemplateProvenance(author="issue-78-test", source="test"),
            ),
        )
        failing_template = application.templates.publish(
            failing_draft.template_id,
            expected_revision=failing_draft.revision,
        )
        composite_draft = application.templates.create_draft(
            owner_ref=OWNER,
            content=TemplateContent(
                name="Workflow rollback composite",
                description="Create Workflow then fail",
                template_type=TemplateType.COMPOSITE,
                configuration=TemplateConfiguration(payload={"portable": True}),
                dependencies=(
                    TemplateDependency(workflow_template.template_id, workflow_template.revision),
                    TemplateDependency(failing_template.template_id, failing_template.revision),
                ),
                provenance=TemplateProvenance(author="issue-78-test", source="test"),
            ),
        )
        composite = application.templates.publish(
            composite_draft.template_id,
            expected_revision=composite_draft.revision,
        )

        with pytest.raises(RuntimeError, match="intentional workflow compensation"):
            await application.apply(
                composite.template_id,
                applied_by=OWNER,
                environment=TemplateEnvironment(),
            )

        assert base.list() == ()
        restored = WorkflowService(JsonWorkflowRepository(workflow_path))
        assert restored.list() == ()

    asyncio.run(scenario())


def test_single_node_composes_workflow_template_handler_and_durable_store(tmp_path: Path) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "single-node", secure_cookie=False)
        )
        account = deployment.bootstrap_admin("issue-78-workflow-admin", "workflow-admin-pass")
        owner = OwnerRef(type="user", id=account.user_id)

        draft = deployment.templates.templates.create_draft(
            owner_ref=owner,
            content=TemplateContent(
                name="Single-node workflow Template",
                description="Composed through the production-shaped deployment",
                template_type=TemplateType.WORKFLOW_PLAN,
                configuration=TemplateConfiguration(
                    payload={
                        "stages": (
                            {
                                "stage_id": "one",
                                "title": "One",
                            },
                        )
                    }
                ),
                provenance=TemplateProvenance(author="issue-78-test", source="test"),
            ),
        )
        published = deployment.templates.templates.publish(
            draft.template_id,
            expected_revision=draft.revision,
        )

        preview = deployment.templates.preview(
            published.template_id,
            applied_by=owner,
            environment=TemplateEnvironment(platform_version=__version__),
        )
        assert preview.applicable is True
        assert "workflow_plan" not in preview.missing_handler_types

        instance = await deployment.templates.apply(
            published.template_id,
            applied_by=owner,
            environment=TemplateEnvironment(platform_version=__version__),
        )
        workflow_id = instance.resource_refs[0].resource_id
        definition = deployment.workflows.workflows.get(workflow_id)
        assert definition.owner_ref == owner
        assert (deployment.config.database_dir / "workflows.json").is_file()

    asyncio.run(scenario())
