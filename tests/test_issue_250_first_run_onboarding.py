from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from ai_multi_agent_platform.adapters import HttpJsonResponse
from ai_multi_agent_platform.adapters.onboarding_openai_compatible import (
    OpenAICompatibleOnboardingAdapter,
)
from ai_multi_agent_platform.adapters.single_node_app import build_default_single_node_deployment
from ai_multi_agent_platform.agents import (
    STANDARD_AGENT_IDS,
    AgentRuntime,
    AgentService,
    InMemoryAgentRepository,
    bootstrap_standard_agents,
)
from ai_multi_agent_platform.configuration import LocalSecretProvider, SecretProvider
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue
from ai_multi_agent_platform.control_plane import ActorContext, RequestContext, ScopeStore
from ai_multi_agent_platform.deployment.config import SingleNodeConfig
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.models import JsonModelRegistryStore, ModelLocation, ModelRegistry
from ai_multi_agent_platform.onboarding import (
    FIRST_RUN_RESOURCE_ID,
    JsonModelProviderSetupStore,
    OnboardingService,
)
from ai_multi_agent_platform.security import SecretReference


class FakeOpenAITransport:
    def __init__(
        self,
        *,
        status_code: int = 200,
        model_ids: tuple[str, ...] = ("qwen-local",),
    ) -> None:
        self.status_code = status_code
        self.model_ids = model_ids
        self.calls: list[tuple[str, str, Mapping[str, str]]] = []

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, JsonValue] | None,
        timeout_seconds: float,
    ) -> HttpJsonResponse:
        del payload, timeout_seconds
        self.calls.append((method, url, headers))
        return HttpJsonResponse(
            status_code=self.status_code,
            payload={"data": [{"id": model_id} for model_id in self.model_ids]},
        )


def _context() -> RequestContext:
    return RequestContext(
        request_id="issue-250-request",
        correlation_id="issue-250-correlation",
        idempotency_key="issue-250-command",
        actor=ActorContext(
            principal_ref="user-alice",
            owner_type="user",
            owner_id="user-alice",
            actor_type="human",
        ),
    )


def _service(
    tmp_path: Path,
    transport: FakeOpenAITransport,
    *,
    models: ModelRegistry | None = None,
    scopes: ScopeStore | None = None,
    agents: AgentService | None = None,
    secret_provider: SecretProvider | None = None,
) -> OnboardingService:
    registry = models or ModelRegistry()
    agent_service = agents or AgentService(InMemoryAgentRepository())
    service = OnboardingService(
        models=registry,
        model_store=JsonModelRegistryStore(tmp_path / "models.json"),
        provider_store=JsonModelProviderSetupStore(tmp_path / "model-providers.json"),
        scopes=scopes or ScopeStore(),
        agents=agent_service,
        agent_runtime=AgentRuntime(agent_service, model_registry=registry),
        model_adapters=(
            OpenAICompatibleOnboardingAdapter(
                transport=transport,
                secret_provider=secret_provider,
            ),
        ),
    )
    service.restore()
    return service


def _payload(**overrides: JsonValue) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "adapter_id": "openai-compatible",
        "provider_id": "local-openai",
        "model_config_id": "model-qwen-local",
        "provider_model": "qwen-local",
        "display_name": "Qwen Local",
        "base_url": "http://127.0.0.1:8001/v1",
        "location": "local",
        "capabilities": {
            "context_window": 32768,
            "tool_calling": True,
            "structured_output": True,
            "streaming": False,
            "modalities": ["text"],
        },
    }
    payload.update(overrides)
    return payload


def test_shipped_single_node_composition_installs_compatible_setup_adapter(
    tmp_path: Path,
) -> None:
    deployment = build_default_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "single-node")
    )

    status = deployment.onboarding.status(_context())

    assert status["state"] == "needs_model"
    assert status["installed_model_adapter_ids"] == ["openai-compatible"]
    assert deployment.models.list_models() == ()


def test_fresh_install_reports_actionable_no_model_state(tmp_path: Path) -> None:
    service = _service(tmp_path, FakeOpenAITransport())

    status = service.status(_context())

    assert status["state"] == "needs_model"
    assert status["local_model_count"] == 0
    assert status["self_hosted_model_count"] == 0
    assert status["remote_model_count"] == 0
    assert status["automatic_remote_provider_selection"] is False
    assert status["automatic_paid_provider_selection"] is False
    assert any("No remote or paid provider" in item for item in status["guidance"])


def test_configure_validates_and_persists_local_model_without_secrets(tmp_path: Path) -> None:
    transport = FakeOpenAITransport()
    service = _service(tmp_path, transport)

    result = asyncio.run(service.configure_model(_context(), FIRST_RUN_RESOURCE_ID, _payload()))

    assert result["id"] == "model-qwen-local"
    assert result["location"] == "local"
    assert result["external_paid_provider_selected"] is False
    assert result["credential_mode"] == "none"
    assert service.models.get_model("model-qwen-local").location is ModelLocation.LOCAL
    assert service.status(_context())["state"] == "needs_project"
    persisted = (tmp_path / "model-providers.json").read_text(encoding="utf-8")
    assert "qwen-local" in persisted
    assert "api_key" not in persisted.casefold()
    assert "password" not in persisted.casefold()
    assert "bearer" not in persisted.casefold()
    assert all("Authorization" not in headers for _, _, headers in transport.calls)


def test_secret_reference_is_resolved_only_at_request_boundary_and_value_is_never_persisted(
    tmp_path: Path,
) -> None:
    secret_value = "issue-250-secret-material"
    reference = SecretReference(
        provider="local-secrets",
        secret_id="local-model-token",
        scope="platform",
    )
    secrets = LocalSecretProvider()
    asyncio.run(
        secrets.create(
            reference,
            secret_value,
            purpose="model-provider-auth",
            allowed_consumers=("model-provider:local-openai",),
            allowed_purposes=("model-provider-auth",),
        )
    )
    transport = FakeOpenAITransport()
    service = _service(tmp_path, transport, secret_provider=secrets)

    result = asyncio.run(
        service.configure_model(
            _context(),
            FIRST_RUN_RESOURCE_ID,
            _payload(credential_ref=reference.to_dict()),
        )
    )

    assert result["credential_mode"] == "secret_reference"
    assert "credential_ref" not in result
    assert transport.calls
    assert all(
        headers.get("Authorization") == f"Bearer {secret_value}"
        for _, _, headers in transport.calls
    )
    persisted = (tmp_path / "model-providers.json").read_text(encoding="utf-8")
    assert secret_value not in persisted
    assert "local-model-token" in persisted
    assert '"credential_ref"' in persisted


def test_secret_reference_survives_restart_while_ephemeral_material_is_reprovisioned(
    tmp_path: Path,
) -> None:
    reference = SecretReference(
        provider="local-secrets",
        secret_id="restart-model-token",
        scope="platform",
    )
    first_secrets = LocalSecretProvider()
    asyncio.run(
        first_secrets.create(
            reference,
            "first-process-value",
            purpose="model-provider-auth",
            allowed_consumers=("model-provider:local-openai",),
            allowed_purposes=("model-provider-auth",),
        )
    )
    first = _service(tmp_path, FakeOpenAITransport(), secret_provider=first_secrets)
    asyncio.run(
        first.configure_model(
            _context(),
            FIRST_RUN_RESOURCE_ID,
            _payload(credential_ref=reference.to_dict()),
        )
    )

    restarted_secrets = LocalSecretProvider()
    asyncio.run(
        restarted_secrets.create(
            reference,
            "reprovisioned-value",
            purpose="model-provider-auth",
            allowed_consumers=("model-provider:local-openai",),
            allowed_purposes=("model-provider-auth",),
        )
    )
    restarted_transport = FakeOpenAITransport()
    restarted = _service(
        tmp_path,
        restarted_transport,
        secret_provider=restarted_secrets,
    )

    provider = restarted.models.get_provider("local-openai")
    assert asyncio.run(provider.health()).value == "healthy"
    assert restarted_transport.calls[-1][2]["Authorization"] == "Bearer reprovisioned-value"
    persisted = (tmp_path / "model-providers.json").read_text(encoding="utf-8")
    assert "first-process-value" not in persisted
    assert "reprovisioned-value" not in persisted
    assert "restart-model-token" in persisted


def test_connection_failure_is_actionable_and_not_persisted(tmp_path: Path) -> None:
    service = _service(tmp_path, FakeOpenAITransport(status_code=503))

    with pytest.raises(ContractError) as caught:
        asyncio.run(service.configure_model(_context(), FIRST_RUN_RESOURCE_ID, _payload()))

    assert caught.value.code is ErrorCode.UNAVAILABLE
    assert caught.value.retryable is True
    assert not (tmp_path / "model-providers.json").exists()
    assert service.models.list_models() == ()


def test_unknown_provider_model_is_rejected_before_persistence(tmp_path: Path) -> None:
    service = _service(tmp_path, FakeOpenAITransport(model_ids=("another-model",)))

    with pytest.raises(ContractError) as caught:
        asyncio.run(service.configure_model(_context(), FIRST_RUN_RESOURCE_ID, _payload()))

    assert caught.value.code is ErrorCode.MODEL_UNAVAILABLE
    assert not (tmp_path / "model-providers.json").exists()


def test_plaintext_credentials_and_remote_paid_golden_path_are_rejected(tmp_path: Path) -> None:
    transport = FakeOpenAITransport()
    service = _service(tmp_path, transport)

    with pytest.raises(ContractError) as plaintext:
        asyncio.run(
            service.configure_model(
                _context(),
                FIRST_RUN_RESOURCE_ID,
                _payload(api_key="do-not-store-this"),
            )
        )
    assert plaintext.value.code is ErrorCode.INVALID_REQUEST
    assert transport.calls == []

    with pytest.raises(ContractError) as remote:
        asyncio.run(
            service.configure_model(
                _context(),
                FIRST_RUN_RESOURCE_ID,
                _payload(location="remote", base_url="https://example.invalid/v1"),
            )
        )
    assert remote.value.code is ErrorCode.INVALID_REQUEST
    assert transport.calls == []


def test_local_classification_requires_loopback_but_self_hosted_is_explicit(
    tmp_path: Path,
) -> None:
    transport = FakeOpenAITransport()
    service = _service(tmp_path, transport)

    with pytest.raises(ContractError) as local_error:
        asyncio.run(
            service.configure_model(
                _context(),
                FIRST_RUN_RESOURCE_ID,
                _payload(base_url="http://models.internal:8000/v1", location="local"),
            )
        )
    assert local_error.value.code is ErrorCode.INVALID_REQUEST
    assert transport.calls == []

    result = asyncio.run(
        service.configure_model(
            _context(),
            FIRST_RUN_RESOURCE_ID,
            _payload(base_url="http://models.internal:8000/v1", location="self_hosted"),
        )
    )
    assert result["location"] == "self_hosted"


def test_model_and_provider_attachment_restore_after_restart(tmp_path: Path) -> None:
    first_transport = FakeOpenAITransport()
    first = _service(tmp_path, first_transport)
    asyncio.run(first.configure_model(_context(), FIRST_RUN_RESOURCE_ID, _payload()))

    second_transport = FakeOpenAITransport()
    restored = _service(tmp_path, second_transport)

    model = restored.models.get_model("model-qwen-local")
    assert model.location is ModelLocation.LOCAL
    provider = restored.models.get_provider("local-openai")
    assert provider.descriptor.provider_id == "local-openai"
    assert restored.status(_context())["state"] == "needs_model"
    assert restored.status(_context())["usable_golden_path_model_count"] == 0

    asyncio.run(restored.models.refresh_health("local-openai"))
    assert restored.status(_context())["state"] == "needs_project"
    provider_document = json.loads((tmp_path / "model-providers.json").read_text(encoding="utf-8"))
    assert provider_document["schema_version"] == "1"


def test_existing_canonical_project_workspace_and_general_assistant_advance_state(
    tmp_path: Path,
) -> None:
    transport = FakeOpenAITransport()
    scopes = ScopeStore()
    agents = AgentService(InMemoryAgentRepository())
    service = _service(tmp_path, transport, scopes=scopes, agents=agents)
    asyncio.run(service.configure_model(_context(), FIRST_RUN_RESOURCE_ID, _payload()))

    project = scopes.create_project(
        key="issue-250-project",
        name="First project",
        owner_type="user",
        owner_id="user-alice",
    )
    workspace = scopes.create_workspace(
        key="issue-250-workspace",
        project_id=project.id,
    )
    assert service.status(_context())["state"] == "needs_general_assistant"

    bootstrap_standard_agents(agents)
    agents.clone_agent(
        STANDARD_AGENT_IDS["general_assistant"],
        revision=1,
        owner_ref=OwnerRef(type="user", id="user-alice"),
        project_id=project.id,
        workspace_id=workspace.id,
        name="My General Assistant",
    )

    status = service.status(_context())
    assert status["state"] == "ready_for_task"
    assert status["general_assistant_count"] == 1
    assert status["executable_general_assistant_count"] == 1
    assert status["selection_required"] is False
    assert status["starter_catalog_installed"] is True
