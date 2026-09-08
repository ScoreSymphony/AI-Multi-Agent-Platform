from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path

from ai_multi_agent_platform.adapters import HttpJsonResponse
from ai_multi_agent_platform.adapters.onboarding_openai_compatible import (
    OpenAICompatibleOnboardingAdapter,
)
from ai_multi_agent_platform.agents import STANDARD_AGENT_IDS, bootstrap_standard_agents
from ai_multi_agent_platform.contracts import JsonValue
from ai_multi_agent_platform.control_plane import ActorContext, PageQuery, RequestContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.onboarding import (
    FIRST_RUN_RESOURCE_ID,
    ONBOARDING_RUN_FIRST_TASK_COMMAND,
)


class _LocalModelTransport:
    def __init__(self, answer: str = "context-bound answer") -> None:
        self.answer = answer
        self.calls: list[tuple[str, str, Mapping[str, str], Mapping[str, JsonValue] | None]] = []

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, JsonValue] | None,
        timeout_seconds: float,
    ) -> HttpJsonResponse:
        del timeout_seconds
        self.calls.append((method, url, dict(headers), payload))
        if url.endswith("/models"):
            return HttpJsonResponse(200, {"data": [{"id": "qwen-context"}]})
        if url.endswith("/chat/completions"):
            return HttpJsonResponse(
                200,
                {
                    "model": "qwen-context",
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": self.answer},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 24,
                        "completion_tokens": 4,
                        "total_tokens": 28,
                    },
                },
            )
        raise AssertionError(f"unexpected model endpoint: {url}")


def _context(user_id: str, key: str) -> RequestContext:
    return RequestContext(
        request_id=f"request:{key}",
        correlation_id=f"correlation:{key}",
        idempotency_key=key,
        actor=ActorContext(
            principal_ref=user_id,
            owner_type="user",
            owner_id=user_id,
            actor_type="human",
        ),
    )


def _model_payload() -> dict[str, JsonValue]:
    return {
        "adapter_id": "openai-compatible",
        "provider_id": "local-context-model",
        "model_config_id": "model-qwen-context",
        "provider_model": "qwen-context",
        "display_name": "Qwen Context",
        "base_url": "http://127.0.0.1:8001/v1",
        "location": "local",
        "capabilities": {
            "context_window": 65536,
            "tool_calling": False,
            "structured_output": False,
            "streaming": False,
            "modalities": ["text"],
        },
    }


def _build(data_dir: Path, transport: _LocalModelTransport):
    return build_single_node_deployment(
        SingleNodeConfig(data_dir=data_dir, secure_cookie=False),
        onboarding_model_adapters=(OpenAICompatibleOnboardingAdapter(transport=transport),),
    )


def test_public_single_node_executes_first_agent_run_through_canonical_context_and_restores_it(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        data_dir = tmp_path / "issue-650-single-node"
        transport = _LocalModelTransport()
        deployment = _build(data_dir, transport)

        assert "context-bundles" in deployment.control_plane.registered_collections
        assert "context-run-bindings" in deployment.control_plane.registered_collections
        assert deployment.context.bundles.path == data_dir / "db" / "context-bundles.json"
        assert deployment.context.run_bindings.path == data_dir / "db" / "context-run-bindings.json"

        admin = deployment.bootstrap_admin("admin", "correct horse battery staple")
        await deployment.control_plane.execute_command(
            _context(admin.user_id, "issue-650-model"),
            "onboarding.configure-model",
            FIRST_RUN_RESOURCE_ID,
            _model_payload(),
        )
        project = deployment.scopes.create_project(
            key="issue-650-project",
            name="Context project",
            owner_type="user",
            owner_id=admin.user_id,
        )
        workspace = deployment.scopes.create_workspace(
            key="issue-650-workspace",
            project_id=project.id,
        )
        bootstrap_standard_agents(deployment.agents)
        assistant = deployment.agents.clone_agent(
            STANDARD_AGENT_IDS["general_assistant"],
            revision=1,
            owner_ref=OwnerRef(type="user", id=admin.user_id),
            project_id=project.id,
            workspace_id=workspace.id,
            name="Context General Assistant",
        )

        objective = "Return one short response using the canonical context path."
        result = await deployment.control_plane.execute_command(
            _context(admin.user_id, "issue-650-first-task"),
            ONBOARDING_RUN_FIRST_TASK_COMMAND,
            FIRST_RUN_RESOURCE_ID,
            {
                "objective": objective,
                "project_id": project.id,
                "workspace_id": workspace.id,
                "agent_id": assistant.agent_id,
            },
        )

        output = result["output"]
        assert isinstance(output, dict)
        run_id = str(result["run_id"])
        agent_run_id = str(output["agent_run_id"])
        bundle_id = str(output["context_bundle_id"])
        bundle_digest = str(output["context_bundle_digest"])

        binding = deployment.context.run_bindings.get(agent_run_id)
        bundle = deployment.context.bundles.get(bundle_id)
        assert binding.run_id == run_id
        assert binding.context_bundle_id == bundle_id
        assert binding.context_bundle_digest == bundle_digest == bundle.digest
        assert bundle.task_id == str(result["task_id"])
        assert bundle.agent_id == assistant.agent_id
        assert {entry.source.source_type.value for entry in bundle.entries}.issuperset(
            {"task", "agent"}
        )

        generation = next(call for call in transport.calls if call[1].endswith("/chat/completions"))
        generation_payload = generation[3]
        assert generation_payload is not None
        serialized_prompt = json.dumps(generation_payload.get("messages"), sort_keys=True)
        assert objective in serialized_prompt
        assert "[context:" in serialized_prompt

        inspection = await deployment.control_plane.list_extension_resources(
            _context(admin.user_id, "issue-650-binding-list"),
            "context-run-bindings",
            PageQuery(filters={"run_id": run_id}),
        )
        items = inspection["items"]
        assert isinstance(items, list)
        assert any(
            isinstance(item, dict)
            and item.get("agent_run_id") == agent_run_id
            and item.get("context_bundle_id") == bundle_id
            for item in items
        )
        inspected_bundle = await deployment.control_plane.get_extension_resource(
            _context(admin.user_id, "issue-650-bundle-read"),
            "context-bundles",
            bundle_id,
        )
        assert inspected_bundle["digest"] == bundle_digest
        assert inspected_bundle["run_id"] == run_id
        assert all(
            isinstance(entry, dict) and entry.get("inline_content") is None
            for entry in inspected_bundle["entries"]
        )

        restarted = _build(data_dir, _LocalModelTransport(answer="unused"))
        restored_bundle = restarted.context.bundles.get(bundle_id)
        restored_binding = restarted.context.run_bindings.get(agent_run_id)
        assert restored_bundle.digest == bundle_digest
        assert restored_binding.context_bundle_digest == bundle_digest
        assert restarted.context.reconciliation.already_bound >= 1

    asyncio.run(scenario())
