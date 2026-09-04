from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path

from ai_multi_agent_platform.adapters import HttpJsonResponse
from ai_multi_agent_platform.adapters.onboarding_openai_compatible import (
    OpenAICompatibleOnboardingAdapter,
)
from ai_multi_agent_platform.agents import (
    STANDARD_AGENT_IDS,
    AgentRunStatus,
    bootstrap_standard_agents,
)
from ai_multi_agent_platform.contracts import JsonValue
from ai_multi_agent_platform.control_plane import ActorContext, RequestContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.onboarding import (
    FIRST_RUN_RESOURCE_ID,
    ONBOARDING_RUN_FIRST_TASK_COMMAND,
)


class FirstTaskTransport:
    def __init__(self, answer: str = "first local answer") -> None:
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
            return HttpJsonResponse(200, {"data": [{"id": "qwen-local"}]})
        if url.endswith("/chat/completions"):
            return HttpJsonResponse(
                200,
                {
                    "model": "qwen-local",
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": self.answer},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 11,
                        "completion_tokens": 4,
                        "total_tokens": 15,
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


def _build(data_dir: Path, transport: FirstTaskTransport):
    return build_single_node_deployment(
        SingleNodeConfig(data_dir=data_dir, secure_cookie=False),
        onboarding_model_adapters=(OpenAICompatibleOnboardingAdapter(transport=transport),),
    )


def test_first_general_assistant_task_produces_visible_canonical_result_and_survives_restart(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        data_dir = tmp_path / "single-node"
        first_transport = FirstTaskTransport()
        deployment = _build(data_dir, first_transport)
        admin = deployment.bootstrap_admin("admin", "correct horse battery staple")
        setup_context = _context(admin.user_id, "issue-250-model")

        await deployment.control_plane.execute_command(
            setup_context,
            "onboarding.configure-model",
            FIRST_RUN_RESOURCE_ID,
            _model_payload(),
        )
        project = deployment.scopes.create_project(
            key="issue-250-project",
            name="First local workspace",
            owner_type="user",
            owner_id=admin.user_id,
        )
        workspace = deployment.scopes.create_workspace(
            key="issue-250-workspace",
            project_id=project.id,
        )
        bootstrap_standard_agents(deployment.agents)
        assistant = deployment.agents.clone_agent(
            STANDARD_AGENT_IDS["general_assistant"],
            revision=1,
            owner_ref=OwnerRef(type="user", id=admin.user_id),
            project_id=project.id,
            workspace_id=workspace.id,
            name="My General Assistant",
        )
        assert deployment.onboarding.status(_context(admin.user_id, "status"))["state"] == (
            "ready_for_task"
        )

        result = await deployment.control_plane.execute_command(
            _context(admin.user_id, "issue-250-first-task"),
            ONBOARDING_RUN_FIRST_TASK_COMMAND,
            FIRST_RUN_RESOURCE_ID,
            {
                "objective": "Answer with one short local response.",
                "project_id": project.id,
                "workspace_id": workspace.id,
                "agent_id": assistant.agent_id,
            },
        )

        task_id = str(result["task_id"])
        run_id = str(result["run_id"])
        result_id = str(result["result_id"])
        assert result["task_status"] == "succeeded"
        assert result["run_status"] == "succeeded"
        output = result["output"]
        assert isinstance(output, dict)
        assert output["text"] == "first local answer"
        assert output["model_ref"] == "model-qwen-local"
        assert output["result_id"] == result_id

        task = await deployment.kernel.get_task(task_id)
        run = await deployment.kernel.get_run(task_id, run_id)
        assert result_id in task.result_ids
        assert run.output["text"] == "first local answer"
        result_resource = await deployment.control_plane.get_reference(
            _context(admin.user_id, "read-result"),
            "results",
            result_id,
        )
        assert result_resource == {
            "id": result_id,
            "type": "result",
            "task_id": task_id,
        }
        agent_run_id = str(output["agent_run_id"])
        agent_run = deployment.agents.repository.get_agent_run(agent_run_id)
        assert agent_run.status is AgentRunStatus.SUCCEEDED
        assert agent_run.result_ids == (result_id,)
        assert agent_run.selected_model_config_id == "model-qwen-local"
        generation_calls = [
            call for call in first_transport.calls if call[1].endswith("/chat/completions")
        ]
        assert len(generation_calls) == 1
        generation_payload = generation_calls[0][3]
        assert generation_payload is not None
        assert generation_payload["model"] == "qwen-local"

        restarted = _build(data_dir, FirstTaskTransport(answer="unused after restart"))
        restarted_task = await restarted.kernel.get_task(task_id)
        restarted_run = await restarted.kernel.get_run(task_id, run_id)
        assert restarted_task.result_ids == (result_id,)
        assert restarted_run.output["text"] == "first local answer"
        restarted_result = await restarted.control_plane.get_reference(
            _context(admin.user_id, "read-result-after-restart"),
            "results",
            result_id,
        )
        assert restarted_result == result_resource
        assert restarted.onboarding.status(_context(admin.user_id, "restart-status"))["state"] == (
            "ready_for_task"
        )

    asyncio.run(scenario())
