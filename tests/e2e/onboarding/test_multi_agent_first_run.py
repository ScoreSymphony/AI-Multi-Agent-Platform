from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path

from ai_multi_agent_platform.adapters import HttpJsonResponse
from ai_multi_agent_platform.adapters.onboarding_openai_compatible import (
    OpenAICompatibleOnboardingAdapter,
)
from ai_multi_agent_platform.contracts import JsonValue
from ai_multi_agent_platform.control_plane import ActorContext, RequestContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.onboarding import FIRST_RUN_RESOURCE_ID
from ai_multi_agent_platform.onboarding.multi_agent_first_run import (
    ONBOARDING_RUN_MULTI_AGENT_GOLDEN_PATH_COMMAND,
)


class MultiAgentFirstRunTransport:
    def __init__(self) -> None:
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
            return HttpJsonResponse(200, {"data": [{"id": "qwen-first-run"}]})
        if url.endswith("/chat/completions"):
            call_number = sum(1 for call in self.calls if call[1].endswith("/chat/completions"))
            return HttpJsonResponse(
                200,
                {
                    "model": "qwen-first-run",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": f"first-run agent output {call_number}",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 20,
                        "completion_tokens": 6,
                        "total_tokens": 26,
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
        "provider_id": "local-first-run",
        "model_config_id": "model-first-run-local",
        "provider_model": "qwen-first-run",
        "display_name": "First-run local model",
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


def test_official_first_run_executes_real_multi_agent_golden_path_without_optional_adapters(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        transport = MultiAgentFirstRunTransport()
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False),
            onboarding_model_adapters=(OpenAICompatibleOnboardingAdapter(transport=transport),),
        )
        admin = deployment.bootstrap_admin(
            "issue-894-admin",
            "correct horse battery staple for issue 894",
        )
        await deployment.control_plane.execute_command(
            _context(admin.user_id, "issue-894-model"),
            "onboarding.configure-model",
            FIRST_RUN_RESOURCE_ID,
            _model_payload(),
        )
        project = deployment.scopes.create_project(
            key="issue-894-project",
            name="Official first-run project",
            owner_type="user",
            owner_id=admin.user_id,
        )
        workspace = deployment.scopes.create_workspace(
            key="issue-894-workspace",
            project_id=project.id,
        )

        # No standard Agent catalog, General Assistant, Hermes, Forge, LiteLLM or paid API is
        # installed here. The product command must create only its scoped built-in role profiles
        # and run the already-canonical #889 planning/execution path.
        assert deployment.agents.repository.list_agents() == ()
        result = await deployment.control_plane.execute_command(
            _context(admin.user_id, "issue-894-official-first-run"),
            ONBOARDING_RUN_MULTI_AGENT_GOLDEN_PATH_COMMAND,
            FIRST_RUN_RESOURCE_ID,
            {
                "title": "Official first multi-agent goal",
                "objective": (
                    "Research two viable approaches, choose an execution approach, produce a "
                    "concise result, and review the exact result before returning it."
                ),
                "project_id": project.id,
                "workspace_id": workspace.id,
            },
        )

        assert result["type"] == "multi_agent_first_run_result"
        assert result["task_status"] == "succeeded"
        assert result["project_id"] == project.id
        assert result["workspace_id"] == workspace.id
        assert result["review"]["status"] == "passed"
        assert len(result["result_ids"]) == 4
        assert len(result["artifact_ids"]) >= 1

        agents = result["agents"]
        assert set(agents) == {"researcher", "developer", "reviewer"}
        definitions = deployment.agents.repository.list_agents()
        assert len(definitions) == 3
        assert {definition.current_revision for definition in definitions} == {1}
        assert {definition.project_id for definition in definitions} == {project.id}
        assert {definition.workspace_id for definition in definitions} == {workspace.id}

        steps = result["steps"]
        assert len(steps) == 4
        by_title = {step["title"]: step for step in steps}
        research = by_title["Gather authoritative evidence"]
        approach = by_title["Prepare an independent execution approach"]
        execute = by_title["Produce the requested result"]
        review = by_title["Review the exact produced result"]
        assert research["dependency_ids"] == []
        assert approach["dependency_ids"] == []
        assert set(execute["dependency_ids"]) == {research["step_id"], approach["step_id"]}
        assert review["dependency_ids"] == [execute["step_id"]]
        assert all(step["status"] == "succeeded" for step in steps)
        assert all(step["run_id"] for step in steps)
        assert all(len(step["result_ids"]) == 1 for step in steps)

        trace = result["trace"]
        assert trace["task_id"] == result["task_id"]
        assert trace["plan_id"] == result["plan_id"]
        assert trace["step_ids"] == [step["step_id"] for step in steps]
        assert result["verification"]["reviewer_step_is_baseline_check"] is True

        task = await deployment.kernel.get_task(str(result["task_id"]))
        assert task.status.value == "succeeded"
        assert set(result["result_ids"]).issubset(set(task.result_ids))
        assert set(result["artifact_ids"]).issubset(set(task.artifact_ids))

        generation_calls = [call for call in transport.calls if call[1].endswith("/chat/completions")]
        assert len(generation_calls) == 4
        assert all(call[3] is not None and call[3]["model"] == "qwen-first-run" for call in generation_calls)

        # Replaying the same idempotency key reuses Task, proposal, Plan, Agents and summary
        # artifact rather than creating duplicate canonical product state.
        replay = await deployment.control_plane.execute_command(
            _context(admin.user_id, "issue-894-official-first-run"),
            ONBOARDING_RUN_MULTI_AGENT_GOLDEN_PATH_COMMAND,
            FIRST_RUN_RESOURCE_ID,
            {
                "title": "Official first multi-agent goal",
                "objective": (
                    "Research two viable approaches, choose an execution approach, produce a "
                    "concise result, and review the exact result before returning it."
                ),
                "project_id": project.id,
                "workspace_id": workspace.id,
            },
        )
        assert replay["task_id"] == result["task_id"]
        assert replay["plan_id"] == result["plan_id"]
        assert replay["artifact_ids"] == result["artifact_ids"]
        assert len(deployment.agents.repository.list_agents()) == 3
        assert len(generation_calls) == 4

    asyncio.run(scenario())
