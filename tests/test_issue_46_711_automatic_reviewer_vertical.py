from __future__ import annotations

import asyncio
import json
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
from ai_multi_agent_platform.agents.execution_profile import (
    AgentExecutionBinding,
    encode_agent_execution_binding,
)
from ai_multi_agent_platform.contracts import JsonValue
from ai_multi_agent_platform.control_plane import HTTPRequest
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef, RunStatus, TaskStatus
from ai_multi_agent_platform.kernel import EventSourcedRunRepository, EventSourcedTaskRepository
from ai_multi_agent_platform.onboarding import FIRST_RUN_RESOURCE_ID
from ai_multi_agent_platform.verification import (
    ReviewerIndependence,
    VerificationCompletionAuthority,
    VerificationOutcome,
    VerificationPolicy,
    VerificationStage,
    VerifierKind,
)
from ai_multi_agent_platform.verification.agent_workflow import AutomaticReviewerWorkflow
from ai_multi_agent_platform.verification.output_workflow import (
    AutomaticReviewerOutputCoordinator,
    PolicyMetadataReviewerResolver,
    install_automatic_reviewer_output_observer,
)
from ai_multi_agent_platform.verification.reference_reviewer import ModelRuntimeReviewerExecutor
from ai_multi_agent_platform.verification.reviewer_input import (
    KernelFileReviewerSubjectInputProvider,
)

_PASSWORD = "correct horse battery staple"


class _LocalAutomaticReviewTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Mapping[str, JsonValue] | None]] = []
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
        del headers, timeout_seconds
        self.calls.append((method, url, payload))
        if url.endswith("/models"):
            return HttpJsonResponse(200, {"data": [{"id": "qwen-auto-review"}]})
        if not url.endswith("/chat/completions"):
            raise AssertionError(f"unexpected model endpoint: {url}")

        self.chat_calls += 1
        if self.chat_calls == 1:
            content = "candidate output produced by the producer agent"
        else:
            content = json.dumps(
                {
                    "outcome": "pass",
                    "findings": [
                        {
                            "code": "candidate_verified",
                            "message": "The supplied candidate is internally consistent.",
                            "severity": "info",
                        }
                    ],
                }
            )
        return HttpJsonResponse(
            200,
            {
                "model": "qwen-auto-review",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": content},
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
        "provider_id": "local-auto-review-provider",
        "model_config_id": "model-local-auto-review",
        "provider_model": "qwen-auto-review",
        "display_name": "Local Automatic Review Model",
        "base_url": "http://127.0.0.1:8002/v1",
        "location": "local",
        "capabilities": {
            "context_window": 32768,
            "tool_calling": False,
            "structured_output": True,
            "streaming": False,
            "modalities": ["text"],
        },
    }


def test_authenticated_local_agent_result_is_automatically_reviewed_and_completed(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        transport = _LocalAutomaticReviewTransport()
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False),
            onboarding_model_adapters=(
                OpenAICompatibleOnboardingAdapter(transport=transport),
            ),
        )
        admin = deployment.bootstrap_admin("admin", _PASSWORD)
        token = deployment.authentication.create_personal_access_token(
            admin.user_id,
            purpose="issue-46-711-automatic-review",
        ).secret

        configured = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/onboarding.configure-model",
                headers=_headers(token, key="issue-46-711:model"),
                body=_model_payload(),
            )
        )
        assert configured.status == 200, configured.body

        project_response = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/projects",
                headers=_headers(token, key="issue-46-711:project"),
                body={"name": "Automatic review vertical project"},
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
                headers=_headers(token, key="issue-46-711:workspace"),
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
            name="Automatic Review Producer",
        )
        reviewer = deployment.agents.clone_agent(
            STANDARD_AGENT_IDS["reviewer"],
            revision=1,
            owner_ref=OwnerRef(type="user", id=admin.user_id),
            project_id=project_id,
            workspace_id=workspace_id,
            name="Automatic Review Reviewer",
        )

        created = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/tasks",
                headers=_headers(token, key="issue-46-711:task"),
                body={
                    "title": "Automatic reviewer vertical Task",
                    "objective": "Produce a local candidate and require independent review.",
                    "project_id": project_id,
                },
            )
        )
        assert created.status == 201, created.body
        assert isinstance(created.body, dict)
        task_id = created.body["id"]
        assert isinstance(task_id, str)

        await deployment.kernel.update_task(
            idempotency_key="issue-46-711:agent-binding",
            task_id=task_id,
            metadata=encode_agent_execution_binding(
                AgentExecutionBinding(
                    agent_id=producer.agent_id,
                    agent_revision=producer.revision,
                    model_config_id="model-local-auto-review",
                    workspace_id=workspace_id,
                )
            ),
            actor_ref=admin.user_id,
            source="conformance",
        )

        policy = deployment.verification.register_policy(
            VerificationPolicy(
                name="Automatic Agent verification vertical",
                stages=(
                    VerificationStage(
                        stage_id="agent-review",
                        verifier_kind=VerifierKind.AGENT,
                    ),
                ),
                independence=ReviewerIndependence(
                    producer_agent_must_differ=True,
                    agent_reviewer_must_be_read_only=True,
                ),
                metadata={
                    "automatic_reviewer": {
                        "enabled": True,
                        "subject_types": ["result"],
                        "stages": {
                            "agent-review": {
                                "agent_id": reviewer.agent_id,
                                "agent_revision": reviewer.revision,
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

        completion = deployment.kernel._completion_authority  # noqa: SLF001
        assert isinstance(completion, VerificationCompletionAuthority)
        workflow = AutomaticReviewerWorkflow(
            runtime=deployment.verification_runtime,
            completion=completion,
            agents=deployment.agent_runtime,
            resolver=PolicyMetadataReviewerResolver(completion),
            executor=ModelRuntimeReviewerExecutor(
                agents=deployment.agent_runtime,
                models=deployment.model_runtime,
                inputs=KernelFileReviewerSubjectInputProvider(
                    tasks=EventSourcedTaskRepository(deployment.kernel_repository),
                    runs=EventSourcedRunRepository(deployment.kernel_repository),
                    files=deployment.files,
                ),
            ),
        )
        coordinator = AutomaticReviewerOutputCoordinator(
            kernel=deployment.kernel,
            runtime=deployment.verification_runtime,
            completion=completion,
            reviewer=workflow,
        )
        install_automatic_reviewer_output_observer(deployment.kernel, coordinator)

        queued = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:queue",
                headers=_headers(token, key="issue-46-711:queue"),
            )
        )
        assert queued.status == 200, queued.body
        started = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:start",
                headers=_headers(token, key="issue-46-711:start"),
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
            idempotency_key="issue-46-711:refresh",
            task_id=task_id,
            run_id=run_id,
            actor_ref=admin.user_id,
            source="conformance",
        )
        assert run.status is RunStatus.SUCCEEDED
        waiting = await deployment.kernel.get_task(task_id)
        assert waiting.status is TaskStatus.WAITING
        assert waiting.wait_reason == "verification:waiting"

        result_id = run.output.get("result_id")
        producer_agent_run_id = run.output.get("agent_run_id")
        candidate_text = run.output.get("text")
        assert isinstance(result_id, str)
        assert isinstance(producer_agent_run_id, str)
        assert candidate_text == "candidate output produced by the producer agent"

        # This is the decisive #711 product path: after setup, the caller only attaches the
        # canonical output. The kernel observer creates/reuses Verification, runs the reviewer,
        # submits the canonical result and releases Task completion without private review calls.
        completed = await deployment.kernel.attach_result(
            idempotency_key="issue-46-711:result",
            task_id=task_id,
            run_id=run_id,
            result_id=result_id,
            actor_ref=admin.user_id,
            source="conformance",
        )
        assert completed.status is TaskStatus.SUCCEEDED

        history = deployment.verification.history(task_id=task_id)
        matching = [
            (request, result)
            for request, result in history
            if request.policy_id == policy.policy_id
            and request.policy_version == policy.version
            and request.stage_id == "agent-review"
            and request.subject.subject_id == result_id
        ]
        assert len(matching) == 1
        verification_request, verification_result = matching[0]
        assert verification_request.producer is not None
        assert verification_request.producer.agent_id == producer.agent_id
        assert verification_request.run_id == run_id
        assert verification_result is not None
        assert verification_result.outcome is VerificationOutcome.PASS
        assert verification_result.findings[0].code == "candidate_verified"

        reviewer_runs = [
            record
            for record in deployment.agents.repository.list_agent_runs()
            if record.verification_context.get("verification_id")
            == verification_request.verification_id
        ]
        assert len(reviewer_runs) == 1
        reviewer_run = reviewer_runs[0]
        assert reviewer_run.status is AgentRunStatus.SUCCEEDED
        assert reviewer_run.agent.agent_id == reviewer.agent_id
        assert reviewer_run.agent.agent_id != producer.agent_id
        assert reviewer_run.run_id == run_id
        assert reviewer_run.selected_model_config_id == "model-local-auto-review"
        assert reviewer_run.model_call_refs == (
            f"{reviewer_run.agent_run_id}:review-model",
        )

        verification_view = await deployment.http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/verifications/{verification_request.verification_id}",
                headers=_headers(token),
            )
        )
        assert verification_view.status == 200, verification_view.body
        assert isinstance(verification_view.body, dict)
        assert verification_view.body["status"] == "completed"
        result_view = verification_view.body["verification_result"]
        assert isinstance(result_view, dict)
        assert result_view["outcome"] == VerificationOutcome.PASS.value
        verifier_view = result_view["verifier"]
        assert isinstance(verifier_view, dict)
        assert verifier_view["kind"] == VerifierKind.AGENT.value
        assert verifier_view["agent_id"] == reviewer.agent_id
        assert verifier_view["read_only"] is True

        producer_run = deployment.agents.repository.get_agent_run(producer_agent_run_id)
        assert producer_run.status is AgentRunStatus.SUCCEEDED
        assert producer_run.agent.agent_id == producer.agent_id
        assert producer_run.agent_run_id != reviewer_run.agent_run_id
        assert transport.chat_calls == 2

        # Replaying the same canonical attach command reconciles from the persisted event and
        # must not create a second reviewer/model call or VerificationResult.
        repeated = await deployment.kernel.attach_result(
            idempotency_key="issue-46-711:result",
            task_id=task_id,
            run_id=run_id,
            result_id=result_id,
            actor_ref=admin.user_id,
            source="conformance",
        )
        assert repeated.status is TaskStatus.SUCCEEDED
        assert transport.chat_calls == 2
        assert len(
            [
                record
                for record in deployment.agents.repository.list_agent_runs()
                if record.verification_context.get("verification_id")
                == verification_request.verification_id
            ]
        ) == 1
        assert len(
            [
                pair
                for pair in deployment.verification.history(task_id=task_id)
                if pair[0].verification_id == verification_request.verification_id
            ]
        ) == 1

    asyncio.run(scenario())
