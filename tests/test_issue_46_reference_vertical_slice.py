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
from ai_multi_agent_platform.agents.execution_profile import (
    AgentExecutionBinding,
    encode_agent_execution_binding,
)
from ai_multi_agent_platform.contracts import JsonValue, OperationContext
from ai_multi_agent_platform.control_plane import HTTPRequest
from ai_multi_agent_platform.data import DataAccessContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef, RunStatus, TaskStatus, new_id
from ai_multi_agent_platform.onboarding import FIRST_RUN_RESOURCE_ID
from ai_multi_agent_platform.verification import (
    VerificationOutcome,
    VerificationPolicy,
    VerificationStage,
    VerifierKind,
)

_PASSWORD = "correct horse battery staple"


class _LocalModelTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Mapping[str, JsonValue] | None]] = []

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
            return HttpJsonResponse(200, {"data": [{"id": "qwen-vertical"}]})
        if url.endswith("/chat/completions"):
            return HttpJsonResponse(
                200,
                {
                    "model": "qwen-vertical",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "reference vertical response",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 3,
                        "total_tokens": 8,
                    },
                },
            )
        raise AssertionError(f"unexpected model endpoint: {url}")


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
        "provider_id": "local-vertical-provider",
        "model_config_id": "model-reference-vertical",
        "provider_model": "qwen-vertical",
        "display_name": "Reference Vertical Model",
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


def test_authenticated_reference_vertical_preserves_canonical_evidence_end_to_end(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        transport = _LocalModelTransport()
        deployment = build_single_node_deployment(
            config,
            onboarding_model_adapters=(OpenAICompatibleOnboardingAdapter(transport=transport),),
        )
        admin = deployment.bootstrap_admin("admin", _PASSWORD)
        credential = deployment.authentication.create_personal_access_token(
            admin.user_id,
            purpose="issue-46-reference-vertical",
        )
        token = credential.secret

        configured = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/onboarding.configure-model",
                headers=_headers(token, key="issue-46:model"),
                body=_model_payload(),
            )
        )
        assert configured.status == 200, configured.body

        project_response = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/projects",
                headers=_headers(token, key="issue-46:project"),
                body={"name": "Reference vertical project"},
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
                headers=_headers(token, key="issue-46:workspace"),
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
        assistant = deployment.agents.clone_agent(
            STANDARD_AGENT_IDS["general_assistant"],
            revision=1,
            owner_ref=OwnerRef(type="user", id=admin.user_id),
            project_id=project_id,
            workspace_id=workspace_id,
            name="Reference Vertical Assistant",
        )

        created = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/tasks",
                headers=_headers(token, key="issue-46:task"),
                body={
                    "title": "Reference vertical Task",
                    "objective": "Produce one locally generated response for verified acceptance",
                    "project_id": project_id,
                },
            )
        )
        assert created.status == 201, created.body
        assert isinstance(created.body, dict)
        task_id = created.body["id"]
        assert isinstance(task_id, str)

        # The northbound Task-create contract intentionally does not expose arbitrary
        # execution metadata. Bind the platform-owned Agent execution contract through
        # the canonical kernel seam rather than inventing a conformance-only API.
        await deployment.kernel.update_task(
            idempotency_key="issue-46:agent-binding",
            task_id=task_id,
            metadata=encode_agent_execution_binding(
                AgentExecutionBinding(
                    agent_id=assistant.agent_id,
                    agent_revision=assistant.revision,
                    model_config_id="model-reference-vertical",
                    workspace_id=workspace_id,
                )
            ),
            actor_ref=admin.user_id,
            source="conformance",
        )

        policy = deployment.verification.register_policy(
            VerificationPolicy(
                name="Reference vertical human verification",
                stages=(
                    VerificationStage(
                        stage_id="human-review",
                        verifier_kind=VerifierKind.HUMAN,
                    ),
                ),
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
                headers=_headers(token, key="issue-46:queue"),
            )
        )
        assert queued.status == 200, queued.body

        started = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:start",
                headers=_headers(token, key="issue-46:start"),
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
        assert started.body["workspace_id"] == workspace_id
        assert started.body["workspace_snapshot_id"] == workspace_snapshot_id

        binding = await deployment.run_workspace_bindings.get(run_id)
        assert binding is not None
        assert binding.task_id == task_id
        assert binding.workspace_id == workspace_id
        assert binding.workspace_snapshot_id == workspace_snapshot_id

        run = await deployment.kernel.refresh_run(
            idempotency_key="issue-46:refresh",
            task_id=task_id,
            run_id=run_id,
            actor_ref=admin.user_id,
            source="conformance",
        )
        assert run.status is RunStatus.SUCCEEDED
        waiting = await deployment.kernel.get_task(task_id)
        assert waiting.status is TaskStatus.WAITING
        assert waiting.blocked is True
        assert waiting.wait_reason == "verification:waiting"

        result_id = run.output.get("result_id")
        agent_run_id = run.output.get("agent_run_id")
        assert isinstance(result_id, str)
        assert isinstance(agent_run_id, str)
        assert run.output.get("model_ref") == "model-reference-vertical"
        assert run.output.get("text") == "reference vertical response"

        agent_run = deployment.agents.repository.get_agent_run(agent_run_id)
        assert agent_run.status is AgentRunStatus.SUCCEEDED
        assert agent_run.task_id == task_id
        assert agent_run.run_id == run_id
        assert agent_run.agent.agent_id == assistant.agent_id
        assert agent_run.agent.revision == assistant.revision
        assert agent_run.selected_model_config_id == "model-reference-vertical"
        assert agent_run.selected_provider_id == "local-vertical-provider"
        assert agent_run.result_ids == (result_id,)
        assert agent_run.model_call_refs == (f"{run_id}:model",)

        await deployment.kernel.attach_result(
            idempotency_key="issue-46:result",
            task_id=task_id,
            run_id=run_id,
            result_id=result_id,
            actor_ref=admin.user_id,
            source="conformance",
        )

        data_context = DataAccessContext(
            operation=OperationContext(
                correlation_id=task_id,
                owner_type="user",
                owner_id=admin.user_id,
                project_id=project_id,
            ),
            actor_ref=admin.user_id,
            task_id=task_id,
        )
        file_record = await deployment.files.create_file(
            b"reference vertical response\n",
            data_context,
            content_type="text/plain",
        )
        artifact_id = new_id("artifact")
        linked = await deployment.files.link_artifact(
            file_record.file_id,
            artifact_id,
            data_context,
        )
        assert artifact_id in linked.artifact_ids
        await deployment.kernel.attach_artifact(
            idempotency_key="issue-46:artifact",
            task_id=task_id,
            run_id=run_id,
            artifact_id=artifact_id,
            actor_ref=admin.user_id,
            source="conformance",
        )

        canonical_run = await deployment.kernel.get_run(task_id, run_id)
        assert result_id in canonical_run.result_ids
        assert artifact_id in canonical_run.artifact_ids

        verification_request = await deployment.verification_runtime.request_verification(
            task_id=task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="human-review",
            subject_type="result",
            subject_id=result_id,
            correlation_id=task_id,
        )
        assert verification_request.task_id == task_id
        assert verification_request.run_id == run_id
        assert verification_request.result_id == result_id
        assert verification_request.project_id == project_id
        assert verification_request.producer is not None
        assert verification_request.producer.agent_id == assistant.agent_id
        assert verification_request.producer.agent_revision == assistant.revision
        assert verification_request.producer.model_config_id == "model-reference-vertical"
        assert verification_request.producer.provider_id == "local-vertical-provider"
        assert verification_request.subject.revision == f"{run_id}:attempt:1"
        assert verification_request.subject == (
            await deployment.verification_runtime.evidence.resolve_subject(
                task_id=task_id,
                subject_type="result",
                subject_id=result_id,
            )
        )

        review_queue = await deployment.http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/verification-reviews",
                headers=_headers(token),
            )
        )
        assert review_queue.status == 200, review_queue.body
        assert isinstance(review_queue.body, dict)
        reviews = review_queue.body["items"]
        assert isinstance(reviews, list)
        assert any(
            isinstance(item, dict) and item.get("id") == verification_request.verification_id
            for item in reviews
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
        assert verification_view.body["task_id"] == task_id
        assert verification_view.body["run_id"] == run_id
        assert verification_view.body["result_id"] == result_id
        producer = verification_view.body["producer"]
        assert isinstance(producer, dict)
        assert producer["agent_id"] == assistant.agent_id
        assert producer["model_config_id"] == "model-reference-vertical"

        accepted = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/verification.accept",
                headers=_headers(token, key="issue-46:accept"),
                body={
                    "resource_ref": verification_request.verification_id,
                    "comment": "Reference vertical evidence accepted",
                    "evidence_artifact_ids": [artifact_id],
                },
            )
        )
        assert accepted.status == 200, accepted.body
        assert isinstance(accepted.body, dict)
        verification_result = accepted.body["verification_result"]
        assert isinstance(verification_result, dict)
        assert verification_result["outcome"] == VerificationOutcome.PASS.value
        assert verification_result["subject"] == verification_view.body["subject"]
        assert verification_result["evidence_artifact_ids"] == [artifact_id]

        completed = await deployment.kernel.complete_task(
            idempotency_key="issue-46:complete",
            task_id=task_id,
            actor_ref=admin.user_id,
            source="conformance",
        )
        assert completed.status is TaskStatus.SUCCEEDED

        task_view = await deployment.http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/tasks/{task_id}",
                headers=_headers(token),
            )
        )
        run_view = await deployment.http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/runs/{run_id}",
                headers=_headers(token),
            )
        )
        result_view = await deployment.http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/results/{result_id}",
                headers=_headers(token),
            )
        )
        timeline = await deployment.http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/tasks/{task_id}/timeline",
                headers=_headers(token),
            )
        )
        assert task_view.status == run_view.status == result_view.status == timeline.status == 200
        assert isinstance(task_view.body, dict)
        assert isinstance(run_view.body, dict)
        assert isinstance(result_view.body, dict)
        assert isinstance(timeline.body, dict)
        assert task_view.body["status"] == TaskStatus.SUCCEEDED.value
        assert run_view.body["status"] == RunStatus.SUCCEEDED.value
        assert result_view.body["task_id"] == task_id
        assert result_view.body["id"] == result_id

        timeline_items = timeline.body["items"]
        assert isinstance(timeline_items, list)
        event_types = {
            item.get("event_type")
            for item in timeline_items
            if isinstance(item, dict) and item.get("type") == "event"
        }
        telemetry_names = {
            item.get("event_name")
            for item in timeline_items
            if isinstance(item, dict) and item.get("type") == "telemetry"
        }
        assert {
            "run.succeeded",
            "result.attached",
            "artifact.attached",
            "task.succeeded",
        } <= event_types
        assert {"verification.requested", "verification.result_recorded"} <= telemetry_names

        model_calls = [call for call in transport.calls if call[1].endswith("/chat/completions")]
        assert len(model_calls) == 1
        assert deployment.observability_exporter.logs
        assert deployment.observability_exporter.metrics

    asyncio.run(scenario())