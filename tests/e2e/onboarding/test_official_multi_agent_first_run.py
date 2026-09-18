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
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue
from ai_multi_agent_platform.control_plane import ActorContext, RequestContext
from ai_multi_agent_platform.deployment import SingleNodeConfig
from ai_multi_agent_platform.deployment.product_composition import (
    build_product_single_node_deployment,
)
from ai_multi_agent_platform.onboarding import FIRST_RUN_RESOURCE_ID
from ai_multi_agent_platform.onboarding.multi_agent_first_run import (
    ONBOARDING_RUN_MULTI_AGENT_GOLDEN_PATH_COMMAND,
)

_PASSWORD = "correct horse battery staple for first run"
_COMMAND_KEY = "official-first-run-command"


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
            content = json.dumps({"outcome": "pass", "findings": []})
            return HttpJsonResponse(
                200,
                {
                    "model": "qwen-first-run",
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 20,
                        "completion_tokens": 8,
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
        "provider_id": "local-first-run",
        "model_config_id": "model-first-run-local",
        "provider_model": "qwen-first-run",
        "display_name": "First-run local model",
        "base_url": "http://127.0.0.1:8001/v1",
        "location": "local",
        "capabilities": {
            "context_window": 32768,
            "tool_calling": False,
            "structured_output": True,
            "streaming": False,
            "modalities": ["text"],
        },
    }


def _build(data_dir: Path, transport: MultiAgentFirstRunTransport):
    return build_product_single_node_deployment(
        SingleNodeConfig(data_dir=data_dir, secure_cookie=False),
        onboarding_model_adapters=(OpenAICompatibleOnboardingAdapter(transport=transport),),
    )


def _payload(project_id: str, workspace_id: str) -> dict[str, JsonValue]:
    return {
        "title": "Official first multi-agent goal",
        "objective": (
            "Research two viable approaches, produce a concise result from both inputs, "
            "and review the exact produced result."
        ),
        "project_id": project_id,
        "workspace_id": workspace_id,
    }


def test_official_first_run_exposes_real_plan_agents_artifact_and_verification(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        data_dir = tmp_path / "platform"
        transport = MultiAgentFirstRunTransport()
        deployment = _build(data_dir, transport)
        admin = deployment.bootstrap_admin("first-run-admin", _PASSWORD)

        await deployment.control_plane.execute_command(
            _context(admin.user_id, "configure-local-model"),
            "onboarding.configure-model",
            FIRST_RUN_RESOURCE_ID,
            _model_payload(),
        )
        project = deployment.scopes.create_project(
            key="official-first-run-project",
            name="Official first-run project",
            owner_type="user",
            owner_id=admin.user_id,
        )
        workspace = await deployment.control_plane.create_workspace(
            _context(admin.user_id, "official-first-run-workspace"),
            {
                "project_id": project.id,
                "workspace_type": "persistent_project",
                "access_mode": "read_write",
                "retention": "persistent",
            },
        )
        workspace_id = str(workspace["id"])
        assert deployment.agents.repository.list_agents() == ()

        result = await deployment.control_plane.execute_command(
            _context(admin.user_id, _COMMAND_KEY),
            ONBOARDING_RUN_MULTI_AGENT_GOLDEN_PATH_COMMAND,
            FIRST_RUN_RESOURCE_ID,
            _payload(project.id, workspace_id),
        )

        assert result["type"] == "multi_agent_first_run_result"
        assert result["workflow"] == "reference-multi-agent"
        assert result["task_status"] == "succeeded"
        assert result["result_id"]
        assert set(result["agents"]) == {"researcher", "developer", "reviewer"}
        assert len(deployment.agents.repository.list_agents()) == 3
        assert result["review"]["step_status"] == "passed"
        assert result["review"]["verification_status"] == "pass"
        assert result["review"]["verification_id"]
        assert result["artifact_ids"]

        steps = result["steps"]
        assert len(steps) == 4
        by_title = {step["title"]: step for step in steps}
        research = by_title["Gather authoritative evidence"]
        approach = by_title["Prepare an independent execution approach"]
        execute = by_title["Produce the requested result"]
        review = by_title["Review the exact produced result"]
        assert research["depends_on"] == []
        assert approach["depends_on"] == []
        assert set(execute["depends_on"]) == {research["step_id"], approach["step_id"]}
        assert review["depends_on"] == [execute["step_id"]]
        assert all(step["status"] == "succeeded" for step in steps)
        assert all(step["run_id"] for step in steps)

        exact = [item for item in result["verification"] if item["is_final_result_review"]]
        assert len(exact) == 1
        assert exact[0]["subject_id"] == result["result_id"]
        assert exact[0]["outcome"] == "pass"

        task = await deployment.kernel.get_task(str(result["task_id"]))
        assert set(result["artifact_ids"]).issubset(set(task.artifact_ids))
        assert str(result["result_id"]) in task.result_ids
        first_generation_count = sum(
            1 for call in transport.calls if call[1].endswith("/chat/completions")
        )
        assert first_generation_count >= 5

        replay = await deployment.control_plane.execute_command(
            _context(admin.user_id, _COMMAND_KEY),
            ONBOARDING_RUN_MULTI_AGENT_GOLDEN_PATH_COMMAND,
            FIRST_RUN_RESOURCE_ID,
            _payload(project.id, workspace_id),
        )
        assert replay["task_id"] == result["task_id"]
        assert replay["plan_id"] == result["plan_id"]
        assert replay["artifact_ids"] == result["artifact_ids"]
        assert len(deployment.agents.repository.list_agents()) == 3
        assert sum(1 for call in transport.calls if call[1].endswith("/chat/completions")) == (
            first_generation_count
        )

        restarted_transport = MultiAgentFirstRunTransport()
        restarted = _build(data_dir, restarted_transport)
        before_health = restarted.onboarding.status(_context(admin.user_id, "restart-status"))
        assert before_health["state"] == "needs_model"
        with pytest.raises(ContractError) as missing_health:
            await restarted.control_plane.execute_command(
                _context(admin.user_id, _COMMAND_KEY),
                ONBOARDING_RUN_MULTI_AGENT_GOLDEN_PATH_COMMAND,
                FIRST_RUN_RESOURCE_ID,
                _payload(project.id, workspace.id),
            )
        assert missing_health.value.code is ErrorCode.INVALID_CONFIGURATION
        assert missing_health.value.details["action"] == "onboarding.configure-model"

        await restarted.control_plane.refresh_model_provider_health(
            _context(admin.user_id, "refresh-local-provider"),
            "local-first-run",
        )
        restored = await restarted.control_plane.execute_command(
            _context(admin.user_id, _COMMAND_KEY),
            ONBOARDING_RUN_MULTI_AGENT_GOLDEN_PATH_COMMAND,
            FIRST_RUN_RESOURCE_ID,
            _payload(project.id, workspace.id),
        )
        assert restored["task_id"] == result["task_id"]
        assert restored["result_id"] == result["result_id"]
        assert restored["review"]["verification_status"] == "pass"
        assert not [
            call for call in restarted_transport.calls if call[1].endswith("/chat/completions")
        ]

    asyncio.run(scenario())


def test_official_first_run_reports_actionable_missing_model_guidance(tmp_path: Path) -> None:
    async def scenario() -> None:
        deployment = _build(tmp_path / "missing-model", MultiAgentFirstRunTransport())
        admin = deployment.bootstrap_admin("missing-model-admin", _PASSWORD)
        project = deployment.scopes.create_project(
            key="missing-model-project",
            name="Missing model project",
            owner_type="user",
            owner_id=admin.user_id,
        )
        workspace = deployment.scopes.create_workspace(
            key="missing-model-workspace",
            project_id=project.id,
        )
        with pytest.raises(ContractError) as error:
            await deployment.control_plane.execute_command(
                _context(admin.user_id, "missing-model-first-run"),
                ONBOARDING_RUN_MULTI_AGENT_GOLDEN_PATH_COMMAND,
                FIRST_RUN_RESOURCE_ID,
                _payload(project.id, workspace.id),
            )
        assert error.value.code is ErrorCode.INVALID_CONFIGURATION
        assert error.value.details["action"] == "onboarding.configure-model"
        assert "local/self-hosted" in str(error.value)

    asyncio.run(scenario())
