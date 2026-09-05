from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest

from ai_multi_agent_platform.adapters import HttpJsonResponse
from ai_multi_agent_platform.adapters.onboarding_openai_compatible import (
    OpenAICompatibleOnboardingAdapter,
)
from ai_multi_agent_platform.agents import (
    STANDARD_AGENT_IDS,
    AgentRevision,
    AgentRuntime,
    AgentService,
    InMemoryAgentRepository,
    bootstrap_standard_agents,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue
from ai_multi_agent_platform.control_plane import ActorContext, RequestContext, ScopeStore
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.models import JsonModelRegistryStore, ModelRegistry
from ai_multi_agent_platform.onboarding import (
    FIRST_RUN_RESOURCE_ID,
    FirstRunTaskService,
    JsonModelProviderSetupStore,
    OnboardingService,
)


class SelectionTransport:
    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, JsonValue] | None,
        timeout_seconds: float,
    ) -> HttpJsonResponse:
        del method, url, headers, payload, timeout_seconds
        return HttpJsonResponse(200, {"data": [{"id": "qwen-local"}]})


class TaskCreationReached(RuntimeError):
    pass


class RecordingKernel:
    def __init__(self) -> None:
        self.create_calls: list[dict[str, object]] = []

    async def create_task(self, **kwargs: object) -> object:
        self.create_calls.append(dict(kwargs))
        raise TaskCreationReached


def _context(key: str) -> RequestContext:
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


def _model_payload() -> dict[str, JsonValue]:
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
            "modalities": ["text"],
        },
    }


def _stack(tmp_path: Path) -> tuple[OnboardingService, ScopeStore, AgentService]:
    scopes = ScopeStore()
    agents = AgentService(InMemoryAgentRepository())
    models = ModelRegistry()
    service = OnboardingService(
        models=models,
        model_store=JsonModelRegistryStore(tmp_path / "models.json"),
        provider_store=JsonModelProviderSetupStore(tmp_path / "model-providers.json"),
        scopes=scopes,
        agents=agents,
        agent_runtime=AgentRuntime(agents, model_registry=models),
        model_adapters=(OpenAICompatibleOnboardingAdapter(transport=SelectionTransport()),),
    )
    service.restore()
    asyncio.run(
        service.configure_model(
            _context("configure-model"),
            FIRST_RUN_RESOURCE_ID,
            _model_payload(),
        )
    )
    bootstrap_standard_agents(agents)
    return service, scopes, agents


def _path(
    scopes: ScopeStore,
    agents: AgentService,
    *,
    project_key: str,
    workspace_key: str,
    assistant_name: str,
) -> tuple[str, str, AgentRevision]:
    project = scopes.create_project(
        key=project_key,
        name=project_key,
        owner_type="user",
        owner_id="user-alice",
    )
    workspace = scopes.create_workspace(key=workspace_key, project_id=project.id)
    assistant = agents.clone_agent(
        STANDARD_AGENT_IDS["general_assistant"],
        revision=1,
        owner_ref=OwnerRef(type="user", id="user-alice"),
        project_id=project.id,
        workspace_id=workspace.id,
        name=assistant_name,
    )
    return project.id, workspace.id, assistant


def _clone_assistant(
    agents: AgentService,
    *,
    project_id: str,
    workspace_id: str,
    name: str,
) -> AgentRevision:
    return agents.clone_agent(
        STANDARD_AGENT_IDS["general_assistant"],
        revision=1,
        owner_ref=OwnerRef(type="user", id="user-alice"),
        project_id=project_id,
        workspace_id=workspace_id,
        name=name,
    )


def _block(agents: AgentService, assistant: AgentRevision) -> AgentRevision:
    return agents.update_agent(
        assistant.agent_id,
        replace(
            assistant.profile,
            model=replace(assistant.profile.model, allow_task_override=False),
        ),
        expected_revision=assistant.revision,
    )


def test_blocked_sibling_agent_does_not_create_false_selection(tmp_path: Path) -> None:
    service, scopes, agents = _stack(tmp_path)
    project_id, workspace_id, executable = _path(
        scopes,
        agents,
        project_key="project-a",
        workspace_key="workspace-a",
        assistant_name="Executable Assistant",
    )
    blocked = _clone_assistant(
        agents,
        project_id=project_id,
        workspace_id=workspace_id,
        name="Blocked Assistant",
    )
    _block(agents, blocked)

    status = service.status(_context("blocked-sibling"))

    assert status["state"] == "ready_for_task"
    assert status["selection_required"] is False
    assert status["candidate_project_ids"] == [project_id]
    assert status["candidate_workspace_ids"] == [workspace_id]
    assert status["candidate_agent_ids"] == [executable.agent_id]
    assert status["general_assistant_count"] == 2
    assert status["executable_general_assistant_count"] == 1


def test_unrelated_project_and_workspace_do_not_require_ids_for_one_executable_path(
    tmp_path: Path,
) -> None:
    service, scopes, agents = _stack(tmp_path)
    project_id, workspace_id, executable = _path(
        scopes,
        agents,
        project_key="project-a",
        workspace_key="workspace-a",
        assistant_name="Executable Assistant",
    )
    unrelated_project = scopes.create_project(
        key="project-b",
        name="project-b",
        owner_type="user",
        owner_id="user-alice",
    )
    scopes.create_workspace(key="workspace-b", project_id=unrelated_project.id)

    status = service.status(_context("unrelated-structural-resources"))
    resolved = service.resolve_first_run_path(_context("resolve-unique"))

    assert status["state"] == "ready_for_task"
    assert status["candidate_project_ids"] == [project_id]
    assert status["candidate_workspace_ids"] == [workspace_id]
    assert status["candidate_agent_ids"] == [executable.agent_id]
    assert resolved.project_id == project_id
    assert resolved.workspace_id == workspace_id
    assert resolved.agent_id == executable.agent_id


def test_multiple_executable_paths_report_first_genuinely_ambiguous_dimension(
    tmp_path: Path,
) -> None:
    service, scopes, agents = _stack(tmp_path)
    project_a, workspace_a, _ = _path(
        scopes,
        agents,
        project_key="project-a",
        workspace_key="workspace-a",
        assistant_name="Assistant A",
    )
    project_b, workspace_b, _ = _path(
        scopes,
        agents,
        project_key="project-b",
        workspace_key="workspace-b",
        assistant_name="Assistant B",
    )

    project_status = service.status(_context("project-selection"))
    assert project_status["state"] == "needs_selection"
    assert project_status["selection_kind"] == "project"
    assert project_status["candidate_project_ids"] == sorted([project_a, project_b])

    with pytest.raises(ContractError) as project_error:
        service.resolve_first_run_path(_context("project-resolution"))
    assert project_error.value.code is ErrorCode.INVALID_REQUEST
    assert project_error.value.details["selection_kind"] == "project"

    project_c = scopes.create_project(
        key="project-c",
        name="project-c",
        owner_type="user",
        owner_id="user-bob",
    )
    del project_c

    workspace_c = scopes.create_workspace(key="workspace-c", project_id=project_a)
    _clone_assistant(
        agents,
        project_id=project_a,
        workspace_id=workspace_c.id,
        name="Assistant C",
    )
    workspace_paths = service.resolve_first_run_path
    with pytest.raises(ContractError) as workspace_error:
        workspace_paths(_context("workspace-resolution"), project_id=project_a)
    assert workspace_error.value.details["selection_kind"] == "workspace"

    second_agent = _clone_assistant(
        agents,
        project_id=project_b,
        workspace_id=workspace_b,
        name="Second Assistant B",
    )
    assert second_agent.agent_id
    with pytest.raises(ContractError) as agent_error:
        service.resolve_first_run_path(
            _context("agent-resolution"),
            project_id=project_b,
            workspace_id=workspace_b,
        )
    assert agent_error.value.details["selection_kind"] == "agent"


def test_idless_first_task_uses_unique_executable_path_before_task_side_effect(
    tmp_path: Path,
) -> None:
    service, scopes, agents = _stack(tmp_path)
    project_id, workspace_id, executable = _path(
        scopes,
        agents,
        project_key="project-a",
        workspace_key="workspace-a",
        assistant_name="Executable Assistant",
    )
    unrelated_project = scopes.create_project(
        key="project-b",
        name="project-b",
        owner_type="user",
        owner_id="user-alice",
    )
    scopes.create_workspace(key="workspace-b", project_id=unrelated_project.id)
    kernel = RecordingKernel()
    first_task = FirstRunTaskService(
        onboarding=service,
        kernel=kernel,  # type: ignore[arg-type]
        scopes=scopes,
        agents=agents,
    )

    with pytest.raises(TaskCreationReached):
        asyncio.run(
            first_task.run_first_task(
                _context("idless-first-task"),
                FIRST_RUN_RESOURCE_ID,
                {"objective": "Prove the unique executable first-run path."},
            )
        )

    assert len(kernel.create_calls) == 1
    assert kernel.create_calls[0]["project_id"] == project_id
    resolved = service.resolve_first_run_path(_context("idless-verify"))
    assert resolved.workspace_id == workspace_id
    assert resolved.agent_id == executable.agent_id


def test_explicit_blocked_selection_is_rejected_before_task_creation(tmp_path: Path) -> None:
    service, scopes, agents = _stack(tmp_path)
    project_id, workspace_id, executable = _path(
        scopes,
        agents,
        project_key="project-a",
        workspace_key="workspace-a",
        assistant_name="Executable Assistant",
    )
    blocked = _clone_assistant(
        agents,
        project_id=project_id,
        workspace_id=workspace_id,
        name="Blocked Assistant",
    )
    blocked = _block(agents, blocked)
    assert blocked.agent_id != executable.agent_id
    kernel = RecordingKernel()
    first_task = FirstRunTaskService(
        onboarding=service,
        kernel=kernel,  # type: ignore[arg-type]
        scopes=scopes,
        agents=agents,
    )

    with pytest.raises(ContractError) as caught:
        asyncio.run(
            first_task.run_first_task(
                _context("blocked-explicit"),
                FIRST_RUN_RESOURCE_ID,
                {
                    "objective": "This must not create a Task.",
                    "project_id": project_id,
                    "workspace_id": workspace_id,
                    "agent_id": blocked.agent_id,
                },
            )
        )

    assert caught.value.code is ErrorCode.FORBIDDEN
    assert kernel.create_calls == []
