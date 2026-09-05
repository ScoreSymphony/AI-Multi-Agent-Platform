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
    AgentService,
    InMemoryAgentRepository,
    bootstrap_standard_agents,
)
from ai_multi_agent_platform.contracts import JsonValue
from ai_multi_agent_platform.control_plane import ActorContext, RequestContext, ScopeStore
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.models import JsonModelRegistryStore, ModelRegistry
from ai_multi_agent_platform.onboarding import (
    FIRST_RUN_RESOURCE_ID,
    JsonModelProviderSetupStore,
    OnboardingService,
)


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
    service = OnboardingService(
        models=ModelRegistry(),
        model_store=JsonModelRegistryStore(tmp_path / "models.json"),
        provider_store=JsonModelProviderSetupStore(tmp_path / "model-providers.json"),
        scopes=scopes or ScopeStore(),
        agents=agents or AgentService(InMemoryAgentRepository()),
        model_adapters=(OpenAICompatibleOnboardingAdapter(transport=transport),),
    )
    service.restore()
    return service


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
    assert any("refresh" in str(item).casefold() for item in status["guidance"])

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
    assert any("text modality" in str(item).casefold() for item in status["guidance"])


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
        owner_id="user-alice",
    )
    other_workspace = scopes.create_workspace(
        key="other-workspace",
        project_id=other_project.id,
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
    assert any("enabled" in str(item).casefold() for item in status["guidance"])

    agents.update_agent(
        disabled.agent_id,
        replace(disabled.profile, enabled=True),
        expected_revision=disabled.revision,
    )
    ready = service.status(_context("ready"))
    assert ready["state"] == "ready_for_task"
    assert ready["general_assistant_count"] == 1

    del other_workspace
