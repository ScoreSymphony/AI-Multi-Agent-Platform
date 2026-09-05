from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from ai_multi_agent_platform.adapters import HttpJsonResponse
from ai_multi_agent_platform.adapters.onboarding_openai_compatible import (
    OpenAICompatibleOnboardingAdapter,
)
from ai_multi_agent_platform.agents import (
    STANDARD_AGENT_IDS,
    AgentCapabilityPolicy,
    AgentInstructions,
    AgentRevision,
    AgentRuntime,
    AgentService,
    CapabilityConstraint,
    InMemoryAgentRepository,
    InstructionSource,
    bootstrap_standard_agents,
)
from ai_multi_agent_platform.contracts import JsonValue
from ai_multi_agent_platform.control_plane import (
    ActorContext,
    RequestContext,
    ScopeStore,
    WorkspaceIdentity,
)
from ai_multi_agent_platform.domain import OwnerRef, Project
from ai_multi_agent_platform.models import (
    JsonModelRegistryStore,
    ModelRegistry,
    RoutingRequirements,
)
from ai_multi_agent_platform.onboarding import (
    FIRST_RUN_RESOURCE_ID,
    JsonModelProviderSetupStore,
    OnboardingService,
)
from ai_multi_agent_platform.onboarding.service import OnboardingService as ServiceOnboardingService


class ReadinessTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, JsonValue] | None,
        timeout_seconds: float,
    ) -> HttpJsonResponse:
        del headers, payload, timeout_seconds
        self.calls.append((method, url))
        return HttpJsonResponse(200, {"data": [{"id": "qwen-local"}]})


def _context(key: str = "issue-250-readiness") -> RequestContext:
    return RequestContext(
        request_id=f"request:{key}",
        correlation_id=f"correlation:{key}",
        idempotency_key=key,
        actor=ActorContext(
            principal_ref="user-alice",
            owner_type="user",
            owner_id="user-alice",
            actor_type="human",
        ),
    )


def _payload(*, modalities: list[str] | None = None) -> dict[str, JsonValue]:
    return {
        "adapter_id": "openai-compatible",
        "provider_id": "local-openai",
        "model_config_id": "model-qwen-local",
        "provider_model": "qwen-local",
        "display_name": "Qwen Local",
        "base_url": "http://127.0.0.1:8001/v1",
        "location": "local",
        "capabilities": {
            "context_window": 32768,
            "tool_calling": False,
            "structured_output": False,
            "streaming": False,
            "modalities": modalities or ["text"],
        },
    }


def _service(
    tmp_path: Path,
    transport: ReadinessTransport,
    *,
    scopes: ScopeStore | None = None,
    agents: AgentService | None = None,
) -> OnboardingService:
    models = ModelRegistry()
    agent_service = agents or AgentService(InMemoryAgentRepository())
    service = OnboardingService(
        models=models,
        model_store=JsonModelRegistryStore(tmp_path / "models.json"),
        provider_store=JsonModelProviderSetupStore(tmp_path / "model-providers.json"),
        scopes=scopes or ScopeStore(),
        agents=agent_service,
        agent_runtime=AgentRuntime(agent_service, model_registry=models),
        model_adapters=(OpenAICompatibleOnboardingAdapter(transport=transport),),
    )
    service.restore()
    return service


def _ready_scope(
    service: OnboardingService,
    scopes: ScopeStore,
    agents: AgentService,
    *,
    project_key: str = "selected-project",
    workspace_key: str = "selected-workspace",
) -> tuple[Project, WorkspaceIdentity, AgentRevision]:
    asyncio.run(service.configure_model(_context("configure"), FIRST_RUN_RESOURCE_ID, _payload()))
    project = scopes.create_project(
        key=project_key,
        name=project_key,
        owner_type="user",
        owner_id="user-alice",
    )
    workspace = scopes.create_workspace(
        key=workspace_key,
        project_id=project.id,
    )
    bootstrap_standard_agents(agents)
    assistant = agents.clone_agent(
        STANDARD_AGENT_IDS["general_assistant"],
        revision=1,
        owner_ref=OwnerRef(type="user", id="user-alice"),
        project_id=project.id,
        workspace_id=workspace.id,
        name="My General Assistant",
    )
    return project, workspace, assistant


def _first_blocker(status: dict[str, JsonValue]) -> dict[str, JsonValue]:
    blockers = status["general_assistant_blockers"]
    assert isinstance(blockers, list)
    assert blockers
    blocker = blockers[0]
    assert isinstance(blocker, dict)
    return blocker


def test_public_onboarding_service_uses_one_authoritative_implementation() -> None:
    assert OnboardingService is ServiceOnboardingService
    assert OnboardingService.__module__ == "ai_multi_agent_platform.onboarding.service"


def test_restored_unknown_provider_health_is_not_reported_ready_until_revalidated(
    tmp_path: Path,
) -> None:
    first = _service(tmp_path, ReadinessTransport())
    asyncio.run(first.configure_model(_context("configure"), FIRST_RUN_RESOURCE_ID, _payload()))
    assert first.status(_context("before-restart"))["state"] == "needs_project"

    restarted_transport = ReadinessTransport()
    restarted = _service(tmp_path, restarted_transport)
    status = restarted.status(_context("after-restart"))

    assert status["state"] == "needs_model"
    assert status["usable_golden_path_model_count"] == 0
    guidance = status["guidance"]
    assert isinstance(guidance, list)
    assert any("refresh" in str(item).casefold() for item in guidance)

    asyncio.run(restarted.models.refresh_health("local-openai"))
    assert restarted.status(_context("after-refresh"))["state"] == "needs_project"
    assert restarted_transport.calls == [("GET", "http://127.0.0.1:8001/v1/models")]


def test_first_run_readiness_requires_text_modality(tmp_path: Path) -> None:
    service = _service(tmp_path, ReadinessTransport())
    asyncio.run(
        service.configure_model(
            _context("configure-image-only"),
            FIRST_RUN_RESOURCE_ID,
            _payload(modalities=["image"]),
        )
    )

    status = service.status(_context("image-only-status"))

    assert status["state"] == "needs_model"
    assert status["text_capable_golden_path_model_count"] == 0
    assert status["usable_golden_path_model_count"] == 0
    guidance = status["guidance"]
    assert isinstance(guidance, list)
    assert any("text modality" in str(item).casefold() for item in guidance)


def test_general_assistant_readiness_requires_enabled_matching_workspace_clone(
    tmp_path: Path,
) -> None:
    scopes = ScopeStore()
    agents = AgentService(InMemoryAgentRepository())
    service = _service(tmp_path, ReadinessTransport(), scopes=scopes, agents=agents)
    asyncio.run(service.configure_model(_context("configure"), FIRST_RUN_RESOURCE_ID, _payload()))

    selected_project = scopes.create_project(
        key="selected-project",
        name="Selected project",
        owner_type="user",
        owner_id="user-alice",
    )
    selected_workspace = scopes.create_workspace(
        key="selected-workspace",
        project_id=selected_project.id,
    )
    other_project = scopes.create_project(
        key="other-project",
        name="Other project",
        owner_type="user",
        owner_id="user-bob",
    )

    bootstrap_standard_agents(agents)
    agents.clone_agent(
        STANDARD_AGENT_IDS["general_assistant"],
        revision=1,
        owner_ref=OwnerRef(type="user", id="user-alice"),
        project_id=other_project.id,
        workspace_id=selected_workspace.id,
        name="Mismatched General Assistant",
    )
    disabled = agents.clone_agent(
        STANDARD_AGENT_IDS["general_assistant"],
        revision=1,
        owner_ref=OwnerRef(type="user", id="user-alice"),
        project_id=selected_project.id,
        workspace_id=selected_workspace.id,
        name="Disabled General Assistant",
    )
    disabled = agents.update_agent(
        disabled.agent_id,
        replace(disabled.profile, enabled=False),
        expected_revision=disabled.revision,
    )

    status = service.status(_context("not-ready"))
    assert status["state"] == "needs_general_assistant"
    assert status["general_assistant_count"] == 0
    guidance = status["guidance"]
    assert isinstance(guidance, list)
    assert any("enabled" in str(item).casefold() for item in guidance)

    agents.update_agent(
        disabled.agent_id,
        replace(disabled.profile, enabled=True),
        expected_revision=disabled.revision,
    )
    ready = service.status(_context("ready"))
    assert ready["state"] == "ready_for_task"
    assert ready["general_assistant_count"] == 1
    assert ready["executable_general_assistant_count"] == 1


def test_edited_general_assistant_must_pass_exact_execution_preflight(tmp_path: Path) -> None:
    scopes = ScopeStore()
    agents = AgentService(InMemoryAgentRepository())
    service = _service(tmp_path, ReadinessTransport(), scopes=scopes, agents=agents)
    project, workspace, assistant = _ready_scope(service, scopes, agents)

    assert service.status(_context("ready"))["state"] == "ready_for_task"

    referenced_instruction = agents.update_agent(
        assistant.agent_id,
        replace(
            assistant.profile,
            instructions=AgentInstructions(role=InstructionSource(ref="prompt:general-assistant")),
        ),
        expected_revision=assistant.revision,
    )
    status = service.status(_context("instruction-ref"))
    assert status["state"] == "needs_general_assistant"
    assert status["executable_general_assistant_count"] == 0
    assert _first_blocker(status)["error_code"] == "unsupported_capability"

    no_override = agents.update_agent(
        assistant.agent_id,
        replace(
            referenced_instruction.profile,
            instructions=assistant.profile.instructions,
            model=replace(assistant.profile.model, allow_task_override=False),
        ),
        expected_revision=referenced_instruction.revision,
    )
    status = service.status(_context("no-task-override"))
    assert status["state"] == "needs_general_assistant"
    assert _first_blocker(status)["error_code"] == "forbidden"

    required_capability = agents.update_agent(
        assistant.agent_id,
        replace(
            no_override.profile,
            model=assistant.profile.model,
            capabilities=AgentCapabilityPolicy(
                allowed=("tool.file.read",),
                constraints=(CapabilityConstraint(capability_id="tool.file.read", required=True),),
            ),
        ),
        expected_revision=no_override.revision,
    )
    status = service.status(_context("missing-required-capability"))
    assert status["state"] == "needs_general_assistant"
    assert _first_blocker(status)["error_code"] == "unsupported_capability"

    conflicting_model = agents.update_agent(
        assistant.agent_id,
        replace(
            required_capability.profile,
            capabilities=assistant.profile.capabilities,
            model=replace(
                assistant.profile.model,
                requirements=RoutingRequirements(modalities=("text",), local_only=True),
                allow_task_override=True,
            ),
        ),
        expected_revision=required_capability.revision,
    )
    status = service.status(_context("conflicting-model-policy"))
    assert status["state"] == "needs_general_assistant"
    assert _first_blocker(status)["error_code"] == "invalid_configuration"

    restored = agents.update_agent(
        assistant.agent_id,
        assistant.profile,
        expected_revision=conflicting_model.revision,
    )
    assert restored.revision > assistant.revision
    restored_status = service.status(_context("restored-ready"))
    assert restored_status["state"] == "ready_for_task"
    assert restored_status["candidate_project_ids"] == [project.id]
    assert restored_status["candidate_workspace_ids"] == [workspace.id]


def test_multiple_projects_require_explicit_selection(tmp_path: Path) -> None:
    scopes = ScopeStore()
    agents = AgentService(InMemoryAgentRepository())
    service = _service(tmp_path, ReadinessTransport(), scopes=scopes, agents=agents)
    _ready_scope(service, scopes, agents, project_key="project-a", workspace_key="workspace-a")

    second_project = scopes.create_project(
        key="project-b",
        name="Project B",
        owner_type="user",
        owner_id="user-alice",
    )
    second_workspace = scopes.create_workspace(
        key="workspace-b",
        project_id=second_project.id,
    )
    agents.clone_agent(
        STANDARD_AGENT_IDS["general_assistant"],
        revision=1,
        owner_ref=OwnerRef(type="user", id="user-alice"),
        project_id=second_project.id,
        workspace_id=second_workspace.id,
        name="Second General Assistant",
    )

    status = service.status(_context("multiple-projects"))
    assert status["state"] == "needs_selection"
    assert status["selection_required"] is True
    assert status["selection_kind"] == "project"
    candidate_project_ids = status["candidate_project_ids"]
    assert isinstance(candidate_project_ids, list)
    assert len(candidate_project_ids) == 2


def test_multiple_workspaces_require_explicit_selection(tmp_path: Path) -> None:
    scopes = ScopeStore()
    agents = AgentService(InMemoryAgentRepository())
    service = _service(tmp_path, ReadinessTransport(), scopes=scopes, agents=agents)
    project, _, _ = _ready_scope(service, scopes, agents)

    second_workspace = scopes.create_workspace(
        key="workspace-b",
        project_id=project.id,
    )
    agents.clone_agent(
        STANDARD_AGENT_IDS["general_assistant"],
        revision=1,
        owner_ref=OwnerRef(type="user", id="user-alice"),
        project_id=project.id,
        workspace_id=second_workspace.id,
        name="Second Workspace Assistant",
    )

    status = service.status(_context("multiple-workspaces"))
    assert status["state"] == "needs_selection"
    assert status["selection_kind"] == "workspace"


def test_multiple_general_assistants_require_explicit_selection(tmp_path: Path) -> None:
    scopes = ScopeStore()
    agents = AgentService(InMemoryAgentRepository())
    service = _service(tmp_path, ReadinessTransport(), scopes=scopes, agents=agents)
    project, workspace, _ = _ready_scope(service, scopes, agents)
    agents.clone_agent(
        STANDARD_AGENT_IDS["general_assistant"],
        revision=1,
        owner_ref=OwnerRef(type="user", id="user-alice"),
        project_id=project.id,
        workspace_id=workspace.id,
        name="Second General Assistant",
    )

    status = service.status(_context("multiple-agents"))
    assert status["state"] == "needs_selection"
    assert status["selection_kind"] == "agent"
    candidate_agent_ids = status["candidate_agent_ids"]
    assert isinstance(candidate_agent_ids, list)
    assert len(candidate_agent_ids) == 2
