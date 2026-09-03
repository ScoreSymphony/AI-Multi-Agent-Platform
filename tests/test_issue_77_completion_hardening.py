from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from ai_multi_agent_platform.agents import (
    SCOPED_STANDARD_AGENT_KEYS,
    STANDARD_AGENT_CATALOG_COLLECTION,
    STANDARD_AGENT_CATALOG_REF,
    STANDARD_AGENT_CONTROL_PLANE_COMMANDS,
    STANDARD_TEAM_CATALOG_COLLECTION,
    AgentRuntime,
    AgentService,
    InMemoryAgentRepository,
    StandardAgentCatalogResourceService,
    StandardAgentCommandHandlers,
    StandardAgentTeamCatalogResourceService,
    bootstrap_standard_agents,
    get_standard_agent_template,
    register_agent_control_plane,
    register_standard_agent_control_plane,
)
from ai_multi_agent_platform.capabilities import (
    CapabilityRegistration,
    CapabilityRegistry,
    NativeEchoProvider,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, HealthStatus
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.control_plane.models import ActorContext, PageQuery, RequestContext
from ai_multi_agent_platform.data import MemoryScope
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
)
from ai_multi_agent_platform.testing import (
    FakeLifecycleBackend,
    FakeModelProvider,
    FakeOrchestrator,
)

ADMIN_APPROVAL_REF = "approval:standard-privileged-admin"
SHELL_CAPABILITY_ID = "tool.shell.execute"


def _context(owner_id: str = "issue-77-user") -> RequestContext:
    return RequestContext(
        request_id=f"request-{owner_id}",
        correlation_id=f"correlation-{owner_id}",
        actor=ActorContext(
            principal_ref=f"user:{owner_id}",
            owner_type="user",
            owner_id=owner_id,
            actor_type="human",
        ),
        idempotency_key=f"idempotency-{owner_id}",
    )


def _headers(key: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Request-Id": "request-issue-77-control-plane",
        "X-Correlation-Id": "correlation-issue-77-control-plane",
        "X-Principal-Ref": "user:issue-77-http-user",
        "X-Owner-Type": "user",
        "X-Owner-Id": "issue-77-http-user",
    }
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _control_plane_stack() -> tuple[ControlPlaneHTTP, AgentService]:
    kernel_repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=kernel_repository,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=kernel_repository,
    )
    service = AgentService(InMemoryAgentRepository())
    register_agent_control_plane(control_plane, service)
    register_standard_agent_control_plane(control_plane, service)
    return ControlPlaneHTTP(control_plane), service


class _AdminShellProvider(NativeEchoProvider):
    protected = True

    async def capability_registrations(self) -> tuple[CapabilityRegistration, ...]:
        registrations = await super().capability_registrations()
        base = registrations[0]
        shell = replace(
            base,
            capability=replace(
                base.capability,
                capability_id=SHELL_CAPABILITY_ID,
                name="Privileged Shell Execute",
                required_approvals=(ADMIN_APPROVAL_REF,) if self.protected else (),
            ),
        )
        return (shell,)


class _UnprotectedAdminShellProvider(_AdminShellProvider):
    protected = False


def _model_registry() -> ModelRegistry:
    registry = ModelRegistry()
    provider = FakeModelProvider()
    registry.register_provider(provider)
    registry.register_model(
        ModelConfiguration(
            config_id="model-issue-77-admin",
            display_name="Issue 77 Admin Test Model",
            provider_id=provider.descriptor.provider_id,
            capabilities=ModelCapabilities(
                context_window=32_768,
                modalities=("text",),
            ),
            location=ModelLocation.LOCAL,
            health=HealthStatus.HEALTHY,
        )
    )
    return registry


def test_standard_catalog_is_discoverable_without_installing_definitions() -> None:
    async def scenario() -> None:
        service = AgentService(InMemoryAgentRepository())
        context = _context()

        agents = await StandardAgentCatalogResourceService().list_resources(
            context,
            PageQuery(),
        )
        teams = await StandardAgentTeamCatalogResourceService().list_resources(
            context,
            PageQuery(),
        )

        assert len(agents) == 8
        assert len(teams) == 2
        developer = next(item for item in agents if item["id"] == "developer")
        file_assistant = next(item for item in agents if item["id"] == "file_assistant")
        general = next(item for item in agents if item["id"] == "general_assistant")
        assert developer["requires_explicit_scope"] is True
        assert file_assistant["requires_explicit_scope"] is True
        assert general["requires_explicit_scope"] is False
        assert "developer" in SCOPED_STANDARD_AGENT_KEYS
        assert service.repository.list_agents() == ()
        assert service.repository.list_teams() == ()

    asyncio.run(scenario())


def test_standard_catalog_lifecycle_uses_real_control_plane_http_command_path() -> None:
    async def scenario() -> None:
        http, service = _control_plane_stack()

        catalog = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/standard-agents",
                headers=_headers(),
            )
        )
        assert catalog.status == 200
        assert isinstance(catalog.body, dict)
        items = catalog.body["items"]
        assert isinstance(items, list)
        assert len(items) == 8
        assert service.repository.list_agents() == ()

        installed = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/standard-agent.bootstrap",
                headers=_headers("issue-77-bootstrap"),
                body={"resource_ref": STANDARD_AGENT_CATALOG_REF},
            )
        )
        assert installed.status == 200
        assert isinstance(installed.body, dict)
        assert len(installed.body["installed_agent_keys"]) == 8
        assert len(installed.body["installed_team_keys"]) == 2

        rejected = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/standard-agent.clone",
                headers=_headers("issue-77-clone-no-scope"),
                body={
                    "resource_ref": "developer",
                    "name": "HTTP Developer",
                },
            )
        )
        assert rejected.status == 400
        assert isinstance(rejected.body, dict)
        assert rejected.body["code"] == ErrorCode.INVALID_REQUEST.value

        workspace_id = new_id("workspace")
        cloned = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/standard-agent.clone",
                headers=_headers("issue-77-clone-scoped"),
                body={
                    "resource_ref": "developer",
                    "name": "HTTP Developer",
                    "workspace_id": workspace_id,
                },
            )
        )
        assert cloned.status == 200
        assert isinstance(cloned.body, dict)
        clone_id = cloned.body["id"]
        assert isinstance(clone_id, str)
        persisted = service.get_agent_revision(clone_id)
        assert persisted.workspace_id == workspace_id
        assert persisted.owner_ref == OwnerRef(type="user", id="issue-77-http-user")

        deleted = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/agent.delete",
                headers=_headers("issue-77-delete"),
                body={"resource_ref": clone_id},
            )
        )
        assert deleted.status == 200
        assert isinstance(deleted.body, dict)
        assert deleted.body["deleted"] is True
        with pytest.raises(ContractError) as removed:
            service.get_agent_revision(clone_id)
        assert removed.value.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_control_plane_bootstrap_clone_scope_customize_and_delete_workflow() -> None:
    async def scenario() -> None:
        service = AgentService(InMemoryAgentRepository())
        handlers = StandardAgentCommandHandlers(service)
        context = _context()

        installed = await handlers.bootstrap(context, STANDARD_AGENT_CATALOG_REF, {})
        assert len(installed["installed_agent_keys"]) == 8
        assert len(installed["installed_team_keys"]) == 2

        with pytest.raises(ContractError) as missing_scope:
            await handlers.clone_agent(context, "developer", {"name": "Scoped Developer"})
        assert missing_scope.value.code is ErrorCode.INVALID_REQUEST

        workspace_id = new_id("workspace")
        clone_payload = {
            "name": "Scoped Developer",
            "workspace_id": workspace_id,
        }
        cloned_resource = await handlers.clone_agent(context, "developer", clone_payload)
        cloned_id = str(cloned_resource["id"])
        cloned = service.get_agent_revision(cloned_id)
        assert cloned.owner_ref == OwnerRef(type="user", id="issue-77-user")
        assert cloned.workspace_id == workspace_id

        knowledge_source_id = new_id("knowledge_source")
        customized_profile = replace(
            cloned.profile,
            data_access=replace(
                cloned.profile.data_access,
                memory_scopes=(MemoryScope.TASK, MemoryScope.WORKSPACE),
                memory_config_refs=("memory-config:issue-77",),
                knowledge_source_ids=(knowledge_source_id,),
            ),
        )
        customized = service.update_agent(
            cloned.agent_id,
            customized_profile,
            expected_revision=cloned.revision,
        )
        assert customized.profile.data_access.memory_config_refs == ("memory-config:issue-77",)
        assert customized.profile.data_access.knowledge_source_ids == (knowledge_source_id,)

        await handlers.delete_agent(context, cloned.agent_id, {})
        with pytest.raises(ContractError) as removed:
            service.get_agent_revision(cloned.agent_id)
        assert removed.value.code is ErrorCode.NOT_FOUND

        bundled = get_standard_agent_template("developer")
        with pytest.raises(ContractError) as bundled_delete:
            await handlers.delete_agent(context, bundled.agent_id, {})
        assert bundled_delete.value.code is ErrorCode.FORBIDDEN

    asyncio.run(scenario())


def test_scoped_software_team_clone_requires_explicit_scope_and_is_deletable() -> None:
    async def scenario() -> None:
        service = AgentService(InMemoryAgentRepository())
        handlers = StandardAgentCommandHandlers(service)
        context = _context("team-owner")
        await handlers.bootstrap(context, STANDARD_AGENT_CATALOG_REF, {})

        with pytest.raises(ContractError) as missing_scope:
            await handlers.clone_team(context, "software_development", {})
        assert missing_scope.value.code is ErrorCode.INVALID_REQUEST

        project_id = new_id("project")
        cloned_resource = await handlers.clone_team(
            context,
            "software_development",
            {"project_id": project_id, "name": "Project Software Team"},
        )
        cloned_id = str(cloned_resource["id"])
        cloned = service.get_team_revision(cloned_id)
        assert cloned.project_id == project_id
        assert cloned.owner_ref == OwnerRef(type="user", id="team-owner")

        await handlers.delete_team(context, cloned.team_id, {})
        with pytest.raises(ContractError) as removed:
            service.get_team_revision(cloned.team_id)
        assert removed.value.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_system_administrator_privileged_shell_requires_matching_canonical_approval() -> None:
    async def scenario() -> None:
        service = AgentService(InMemoryAgentRepository())
        bootstrap_standard_agents(service)
        template = get_standard_agent_template("system_administrator")
        source = service.get_agent_revision(template.agent_id, 1)
        enabled = service.create_agent(
            replace(source.profile, enabled=True),
            owner_ref=OwnerRef(type="user", id="admin-copy-owner"),
        )
        registry = CapabilityRegistry()
        await registry.register_provider(_UnprotectedAdminShellProvider())
        runtime = AgentRuntime(
            service,
            model_registry=_model_registry(),
            capability_registry=registry,
        )

        with pytest.raises(ContractError) as exc_info:
            runtime.prepare_agent(
                task_id=new_id("task"),
                run_id=new_id("run"),
                agent_id=enabled.agent_id,
                requested_capability_ids=(SHELL_CAPABILITY_ID,),
            )

        assert exc_info.value.code is ErrorCode.INVALID_CONFIGURATION
        assert exc_info.value.details["capability_id"] == SHELL_CAPABILITY_ID
        assert exc_info.value.details["approval_ref"] == ADMIN_APPROVAL_REF

    asyncio.run(scenario())


def test_single_node_exposes_durable_standard_agent_management_without_auto_reinstall(
    tmp_path: Path,
) -> None:
    config = SingleNodeConfig(data_dir=tmp_path / "single-node")
    first = build_single_node_deployment(config)

    assert STANDARD_AGENT_CATALOG_COLLECTION in first.control_plane.registered_collections
    assert STANDARD_TEAM_CATALOG_COLLECTION in first.control_plane.registered_collections
    for command in STANDARD_AGENT_CONTROL_PLANE_COMMANDS:
        assert command in first.control_plane.registered_commands
    assert first.agents.repository.list_agents() == ()

    bootstrap_standard_agents(first.agents)
    assert len(first.agents.repository.list_agents()) == 8
    assert len(first.agents.repository.list_teams()) == 2

    restarted = build_single_node_deployment(config)
    assert len(restarted.agents.repository.list_agents()) == 8
    assert len(restarted.agents.repository.list_teams()) == 2
