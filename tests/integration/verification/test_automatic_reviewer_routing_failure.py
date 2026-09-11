from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path

import pytest

from ai_multi_agent_platform.adapters import HttpJsonResponse
from ai_multi_agent_platform.adapters.onboarding_openai_compatible import (
    OpenAICompatibleOnboardingAdapter,
)
from ai_multi_agent_platform.agents import (
    STANDARD_AGENT_IDS,
    STANDARD_TEAM_IDS,
    bootstrap_standard_agents,
)
from ai_multi_agent_platform.agents.execution_profile import (
    AgentExecutionBinding,
    encode_agent_execution_binding,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue
from ai_multi_agent_platform.control_plane import HTTPRequest
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef, RunStatus, TaskStatus
from ai_multi_agent_platform.onboarding import FIRST_RUN_RESOURCE_ID
from ai_multi_agent_platform.verification import (
    VerificationPolicy,
    VerificationStage,
    VerifierKind,
)

_PASSWORD = "issue-759-routing-failure-password"
_MODEL_ID = "model-local-routing-failure"
_PROVIDER_ID = "local-routing-failure-provider"
_PROVIDER_MODEL = "qwen-routing-failure"


class _ProducerOnlyTransport:
    """Produce one local Result; ambiguous routing must fail before reviewer execution."""

    def __init__(self) -> None:
        self.chat_calls = 0

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, JsonValue] | None,
        timeout_seconds: float,
    ) -> HttpJsonResponse:
        del method, headers, payload, timeout_seconds
        if url.endswith("/models"):
            return HttpJsonResponse(200, {"data": [{"id": _PROVIDER_MODEL}]})
        if not url.endswith("/chat/completions"):
            raise AssertionError(f"unexpected model endpoint: {url}")

        self.chat_calls += 1
        if self.chat_calls != 1:
            raise AssertionError(
                "ambiguous reviewer routing must fail before reviewer model execution"
            )
        return HttpJsonResponse(
            200,
            {
                "model": _PROVIDER_MODEL,
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "candidate output for ambiguous reviewer routing",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 5,
                    "total_tokens": 13,
                },
            },
        )


def _headers(token: str, *, key: str | None = None) -> dict[str, str]:
    headers = {
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
    }
    if key is not None:
        headers["idempotency-key"] = key
    return headers


def _model_payload() -> dict[str, JsonValue]:
    return {
        "resource_ref": FIRST_RUN_RESOURCE_ID,
        "adapter_id": "openai-compatible",
        "provider_id": _PROVIDER_ID,
        "model_config_id": _MODEL_ID,
        "provider_model": _PROVIDER_MODEL,
        "display_name": "Local Routing Failure Model",
        "base_url": "http://127.0.0.1:8007/v1",
        "location": "local",
        "capabilities": {
            "context_window": 32768,
            "tool_calling": False,
            "structured_output": True,
            "streaming": False,
            "modalities": ["text"],
        },
    }


def test_productive_ambiguous_reviewer_routing_fails_closed_with_canonical_reason(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        transport = _ProducerOnlyTransport()
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False),
            onboarding_model_adapters=(OpenAICompatibleOnboardingAdapter(transport=transport),),
        )
        admin = deployment.bootstrap_admin("admin", _PASSWORD)
        token = deployment.authentication.create_personal_access_token(
            admin.user_id,
            purpose="issue-759-routing-failure",
        ).secret

        configured = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/onboarding.configure-model",
                headers=_headers(token, key="issue-759-routing-failure:model"),
                body=_model_payload(),
            )
        )
        assert configured.status == 200, configured.body

        project_response = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/projects",
                headers=_headers(token, key="issue-759-routing-failure:project"),
                body={"name": "Ambiguous reviewer routing project"},
            )
        )
        assert project_response.status == 201, project_response.body
        assert isinstance(project_response.body, dict)
        project_id = project_response.body["id"]
        assert isinstance(project_id, str)

        workspace_response = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/workspaces",
                headers=_headers(token, key="issue-759-routing-failure:workspace"),
                body={"project_id": project_id, "workspace_type": "isolated_run"},
            )
        )
        assert workspace_response.status == 201, workspace_response.body
        assert isinstance(workspace_response.body, dict)
        workspace_id = workspace_response.body["id"]
        workspace_snapshot_id = workspace_response.body["base_snapshot_id"]
        assert isinstance(workspace_id, str)
        assert isinstance(workspace_snapshot_id, str)

        bootstrap_standard_agents(deployment.agents)
        producer = deployment.agents.clone_agent(
            STANDARD_AGENT_IDS["general_assistant"],
            revision=1,
            owner_ref=OwnerRef(type="user", id=admin.user_id),
            project_id=project_id,
            workspace_id=workspace_id,
            name="Ambiguous Routing Producer",
        )

        created = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/tasks",
                headers=_headers(token, key="issue-759-routing-failure:task"),
                body={
                    "title": "Ambiguous automatic reviewer routing",
                    "objective": (
                        "Produce a Result whose reviewer route is intentionally ambiguous."
                    ),
                    "project_id": project_id,
                },
            )
        )
        assert created.status == 201, created.body
        assert isinstance(created.body, dict)
        task_id = created.body["id"]
        assert isinstance(task_id, str)

        await deployment.kernel.update_task(
            idempotency_key="issue-759-routing-failure:agent-binding",
            task_id=task_id,
            metadata=encode_agent_execution_binding(
                AgentExecutionBinding(
                    agent_id=producer.agent_id,
                    agent_revision=producer.revision,
                    model_config_id=_MODEL_ID,
                    workspace_id=workspace_id,
                )
            ),
            actor_ref=admin.user_id,
            source="conformance",
        )

        policy = deployment.verification.register_policy(
            VerificationPolicy(
                name="Ambiguous productive reviewer routing",
                stages=(
                    VerificationStage(
                        stage_id="agent-review",
                        verifier_kind=VerifierKind.AGENT,
                    ),
                ),
                metadata={
                    "automatic_reviewer": {
                        "enabled": True,
                        "subject_types": ["result"],
                        "stages": {
                            "agent-review": {
                                "candidate_team_ids": [
                                    STANDARD_TEAM_IDS["software_development"],
                                    STANDARD_TEAM_IDS["research"],
                                ],
                                "reviewer_role": "reviewer",
                            }
                        },
                    }
                },
            )
        )
        deployment.verification_runtime.require_task(
            task_id=task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
        )

        queued = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:queue",
                headers=_headers(token, key="issue-759-routing-failure:queue"),
            )
        )
        assert queued.status == 200, queued.body
        started = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:start",
                headers=_headers(token, key="issue-759-routing-failure:start"),
                body={
                    "workspace_id": workspace_id,
                    "workspace_snapshot_id": workspace_snapshot_id,
                },
            )
        )
        assert started.status == 200, started.body
        assert isinstance(started.body, dict)
        run_id = started.body["id"]
        assert isinstance(run_id, str)

        run = await deployment.kernel.refresh_run(
            idempotency_key="issue-759-routing-failure:refresh",
            task_id=task_id,
            run_id=run_id,
            actor_ref=admin.user_id,
            source="conformance",
        )
        assert run.status is RunStatus.SUCCEEDED
        result_id = run.output.get("result_id")
        assert isinstance(result_id, str)

        with pytest.raises(ContractError) as caught:
            await deployment.kernel.attach_result(
                idempotency_key="issue-759-routing-failure:result",
                task_id=task_id,
                run_id=run_id,
                result_id=result_id,
                actor_ref=admin.user_id,
                source="conformance",
            )

        assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
        assert caught.value.message == (
            "scoped reviewer discovery must resolve exactly one enabled candidate"
        )
        assert caught.value.details["match_count"] == 2
        assert caught.value.details["stage_id"] == "agent-review"
        assert transport.chat_calls == 1

        task = await deployment.kernel.get_task(task_id)
        assert task.status is TaskStatus.WAITING
        assert task.wait_reason == "verification:waiting"

        history = [
            pair
            for pair in deployment.verification.history(task_id=task_id)
            if pair[0].policy_id == policy.policy_id
            and pair[0].policy_version == policy.version
            and pair[0].stage_id == "agent-review"
            and pair[0].subject.subject_id == result_id
        ]
        assert len(history) == 1
        request, result = history[0]
        assert result is None
        assert request.status.value == "pending"

        reviewer_runs = [
            record
            for record in deployment.agents.repository.list_agent_runs()
            if record.verification_context.get("verification_id") == request.verification_id
        ]
        assert reviewer_runs == []

        with pytest.raises(ContractError) as repeated:
            await deployment.kernel.attach_result(
                idempotency_key="issue-759-routing-failure:result",
                task_id=task_id,
                run_id=run_id,
                result_id=result_id,
                actor_ref=admin.user_id,
                source="conformance",
            )
        assert repeated.value.code is ErrorCode.INVALID_CONFIGURATION
        assert repeated.value.details["match_count"] == 2
        assert transport.chat_calls == 1
        assert (
            len(
                [
                    pair
                    for pair in deployment.verification.history(task_id=task_id)
                    if pair[0].policy_id == policy.policy_id
                    and pair[0].policy_version == policy.version
                    and pair[0].stage_id == "agent-review"
                    and pair[0].subject.subject_id == result_id
                ]
            )
            == 1
        )

    asyncio.run(scenario())
