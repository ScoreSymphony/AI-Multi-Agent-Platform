from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from ai_multi_agent_platform.agents import (
    AgentDataAccess,
    AgentInstructions,
    AgentPolicyHooks,
    AgentProfile,
    AgentService,
    AgentWorkspaceDefaults,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.templates import (
    AgentTemplateExporter,
    ContextualTemplateHandlerRegistry,
    InMemoryTemplateRepository,
    TemplateApplicationService,
    TemplateEnvironment,
    register_agent_template_handlers,
)


def _owner() -> OwnerRef:
    return OwnerRef(type="user", id="issue-78-portability-user")


def _application() -> tuple[TemplateApplicationService, AgentService]:
    agents = AgentService(InMemoryAgentRepository())
    registry = ContextualTemplateHandlerRegistry()
    register_agent_template_handlers(registry, agents)
    return TemplateApplicationService(InMemoryTemplateRepository(), registry), agents


def _profile(
    name: str,
    *,
    workspace_defaults: AgentWorkspaceDefaults | None = None,
    data_access: AgentDataAccess | None = None,
    policy_hooks: AgentPolicyHooks | None = None,
) -> AgentProfile:
    return AgentProfile(
        name=name,
        role="worker",
        instructions=AgentInstructions(role=InstructionSource(content=f"Act as {name}.")),
        workspace_defaults=workspace_defaults or AgentWorkspaceDefaults(),
        data_access=data_access or AgentDataAccess(),
        policy_hooks=policy_hooks or AgentPolicyHooks(),
    )


def test_agent_export_removes_outer_and_nested_source_workspace_scope() -> None:
    async def scenario() -> None:
        application, agents = _application()
        source_project_id = new_id("project")
        source_workspace_id = new_id("workspace")
        default_project_id = new_id("project")
        default_workspace_id = new_id("workspace")
        source = agents.create_agent(
            _profile(
                "Scoped researcher",
                workspace_defaults=AgentWorkspaceDefaults(
                    project_id=default_project_id,
                    workspace_id=default_workspace_id,
                ),
            ),
            owner_ref=_owner(),
            project_id=source_project_id,
            workspace_id=source_workspace_id,
        )
        exporter = AgentTemplateExporter(agents, application.templates)

        draft = exporter.create_from_agent(
            source.agent_id,
            owner_ref=_owner(),
            author="issue-78-test",
        )
        payload = draft.content.configuration.payload
        assert payload is not None
        assert payload["project_id"] is None
        assert payload["workspace_id"] is None
        profile_payload = payload["profile"]
        assert isinstance(profile_payload, Mapping)
        defaults = profile_payload["workspace_defaults"]
        assert isinstance(defaults, Mapping)
        assert defaults["project_id"] is None
        assert defaults["workspace_id"] is None
        assert source_project_id not in repr(payload)
        assert source_workspace_id not in repr(payload)
        assert default_project_id not in repr(payload)
        assert default_workspace_id not in repr(payload)
        metadata = draft.content.provenance.metadata
        assert metadata["source_project_id"] == source_project_id
        assert metadata["source_workspace_id"] == source_workspace_id
        assert metadata["source_default_project_id"] == default_project_id
        assert metadata["source_default_workspace_id"] == default_workspace_id

        published = application.templates.publish(
            draft.template_id,
            expected_revision=draft.revision,
        )
        instance = await application.apply(
            published.template_id,
            applied_by=_owner(),
            environment=TemplateEnvironment(),
        )
        created = agents.get_agent_revision(instance.resource_refs[0].resource_id)
        assert created.project_id is None
        assert created.workspace_id is None
        assert created.profile.workspace_defaults.project_id is None
        assert created.profile.workspace_defaults.workspace_id is None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("profile", "field"),
    (
        (
            _profile(
                "Memory config",
                data_access=AgentDataAccess(memory_config_refs=("memory-config-local",)),
            ),
            "data_access.memory_config_refs",
        ),
        (
            _profile(
                "Knowledge source",
                data_access=AgentDataAccess(knowledge_source_ids=(new_id("knowledge_source"),)),
            ),
            "data_access.knowledge_source_ids",
        ),
        (
            _profile(
                "Authorization policy",
                policy_hooks=AgentPolicyHooks(authorization_profile_ref="local-policy"),
            ),
            "policy_hooks.authorization_profile_ref",
        ),
        (
            _profile(
                "Verification policy",
                policy_hooks=AgentPolicyHooks(verification_policy_refs=("local-verification",)),
            ),
            "policy_hooks.verification_policy_refs",
        ),
    ),
)
def test_agent_export_rejects_undeclared_deployment_local_references(
    profile: AgentProfile,
    field: str,
) -> None:
    application, agents = _application()
    source = agents.create_agent(profile, owner_ref=_owner())
    exporter = AgentTemplateExporter(agents, application.templates)

    with pytest.raises(ContractError) as exc_info:
        exporter.create_from_agent(
            source.agent_id,
            owner_ref=_owner(),
            author="issue-78-test",
        )

    assert exc_info.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
    assert field in exc_info.value.details["fields"]
